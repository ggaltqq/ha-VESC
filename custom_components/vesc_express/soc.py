"""Voltage-based State-of-Charge estimation.

Some VESC-connected BMSes (observed on real ENNOID hardware) never populate
the SoC field over ``COMM_BMS_GET_VALUES`` -- it stays 0 even on a full pack.
When that happens but the per-cell voltages are clearly those of a healthy
Li-ion cell, we estimate SoC from the resting cell voltage using a
per-cell-chemistry open-circuit-voltage (OCV) curve.

IMPORTANT: voltage->SoC is only meaningful at rest. Under load the pack sags
(estimate reads low); while charging it reads high. Good for a parked-board
dashboard, rough while riding. Estimated values are flagged as such so the UI
can distinguish them from a real BMS reading.
"""

from __future__ import annotations

# Below this per-cell voltage we treat the pack as genuinely depleted (or the
# reading as not a real Li-ion cell) and do NOT offer an estimate -- a real 0%
# is a real 0%. Above it, a BMS-reported SoC of 0 is almost certainly the BMS
# simply not computing SoC.
HEALTHY_CELL_FLOOR_V = 3.0

CELL_TYPE_UNKNOWN = "unknown"

# Representative resting OCV curve for high-drain Li-ion NMC cells (the family
# used in Onewheel/e-skate packs: Samsung 50S/40T/30Q, Molicel P42A, ...).
# (per-cell volts, SoC %). Must be sorted by descending voltage.
_NMC_OCV: list[tuple[float, float]] = [
    (4.20, 100.0),
    (4.15, 95.0),
    (4.11, 90.0),
    (4.08, 85.0),
    (4.02, 80.0),
    (3.98, 75.0),
    (3.95, 70.0),
    (3.91, 65.0),
    (3.87, 60.0),
    (3.85, 55.0),
    (3.84, 50.0),
    (3.82, 45.0),
    (3.80, 40.0),
    (3.79, 35.0),
    (3.77, 30.0),
    (3.75, 25.0),
    (3.73, 20.0),
    (3.71, 15.0),
    (3.69, 10.0),
    (3.61, 5.0),
    (3.27, 0.0),
]

# Straight-line fallback: per-cell voltage linearly mapped between a nominal
# empty and full. Chemistry-agnostic, least accurate mid-range. Used by the
# "Linear" profile.
_LINEAR: list[tuple[float, float]] = [(4.20, 100.0), (3.20, 0.0)]

# Battery profiles mirror the cell list offered by the Onewheel companion app's
# "Battery profile" picker. All the named high-drain NMC cells share the NMC
# resting-curve shape today; the distinct entries let the user name their actual
# cell (and let per-cell tuning diverge later without a config migration).
# "Linear" uses the straight-line map instead.
CELL_TYPES: dict[str, dict] = {
    CELL_TYPE_UNKNOWN: {"label": "Not set — ask me", "curve": None},
    "molicel_p42a": {"label": "Molicel P42A", "curve": _NMC_OCV},
    "molicel_p45b": {"label": "Molicel P45B", "curve": _NMC_OCV},
    "molicel_p28a": {"label": "Molicel P28A", "curve": _NMC_OCV},
    "molicel_m35a": {"label": "Molicel M35A", "curve": _NMC_OCV},
    "molicel_p50b": {"label": "Molicel P50B", "curve": _NMC_OCV},
    "dg_01_dg_40": {"label": "DG-01/DG-40", "curve": _NMC_OCV},
    "samsung_50s": {"label": "Samsung 50S", "curve": _NMC_OCV},
    "samsung_40t": {"label": "Samsung 40T", "curve": _NMC_OCV},
    "samsung_30q": {"label": "Samsung 30Q", "curve": _NMC_OCV},
    "reliance_rs50": {"label": "Reliance RS50", "curve": _NMC_OCV},
    "generic_nmc": {"label": "Generic Li-ion (NMC)", "curve": _NMC_OCV},
    "linear": {"label": "Linear", "curve": _LINEAR},
}


def cell_type_labels() -> dict[str, str]:
    """id -> human label, for config-flow dropdowns."""
    return {key: value["label"] for key, value in CELL_TYPES.items()}


def _interpolate(curve: list[tuple[float, float]], voltage: float) -> float:
    if voltage >= curve[0][0]:
        return curve[0][1]
    if voltage <= curve[-1][0]:
        return curve[-1][1]
    for (v_hi, soc_hi), (v_lo, soc_lo) in zip(curve, curve[1:]):
        if v_lo <= voltage <= v_hi:
            span = v_hi - v_lo
            if span <= 0:
                return soc_lo
            frac = (voltage - v_lo) / span
            return soc_lo + frac * (soc_hi - soc_lo)
    return curve[-1][1]  # unreachable, keeps type-checkers happy


def cells_look_healthy(cells: list[float]) -> bool:
    """True if we have real cell voltages above the depleted floor."""
    return bool(cells) and (sum(cells) / len(cells)) >= HEALTHY_CELL_FLOOR_V


def estimate_soc(cells: list[float], cell_type: str) -> float | None:
    """Estimate SoC % from average resting cell voltage, or None if we can't.

    Returns None when there are no cells, the cell type isn't set/known, or the
    cells are below the healthy floor (let a real 0% stand).
    """
    spec = CELL_TYPES.get(cell_type)
    if not spec or not spec["curve"]:
        return None
    if not cells_look_healthy(cells):
        return None
    avg = sum(cells) / len(cells)
    return round(max(0.0, min(100.0, _interpolate(spec["curve"], avg))), 1)
