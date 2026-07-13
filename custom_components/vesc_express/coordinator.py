"""Coordinator that polls the VESC Express for controller + BMS telemetry."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, STATIC_REFRESH_EVERY_POLLS
from .soc import CELL_TYPE_UNKNOWN, cells_look_healthy, estimate_soc
from .vesc_protocol import ControllerValues, VescClient, VescProtocolError

_LOGGER = logging.getLogger(__name__)


class VescCoordinator(DataUpdateCoordinator):
    """Polls both controller and BMS every cycle; each can fail independently.

    This coordinator deliberately never raises UpdateFailed for a
    controller-unreachable condition -- that's an expected, routine state
    (board asleep in storage) rather than an error. Instead it always
    returns a data dict describing what it found, and entities decide how
    to present that.

    The configured controller CAN ID is treated as a hint, not gospel: the
    real node list is discovered via COMM_PING_CAN and the controller read is
    attempted against the configured ID first, then every discovered node. The
    first ID that decodes is remembered so later cycles try it first. This
    self-corrects a wrong/unknown configured CAN ID (the VESC Express itself is
    not the motor controller -- the controller sits behind it on the CAN bus).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
        controller_can_id: int,
        bms_can_id: int,
        poll_interval: int,
        cell_type: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self._entry_id = entry_id
        self._client = VescClient(host, port)
        # Mutable: starts from config, self-heals to whatever node answers.
        self._controller_can_id = controller_can_id
        self._bms_can_id = bms_can_id
        self._cell_type = cell_type
        self._can_nodes: list[int] = []
        self._poll_count = 0

    @property
    def _cell_type_issue_id(self) -> str:
        return f"cell_type_missing_{self._entry_id}"

    def _update_soc(self, data: dict) -> None:
        """Fill soc_percent, preferring the BMS reading, else a voltage estimate.

        Also raises/clears a repair issue: if the BMS reports 0 on a clearly
        healthy Li-ion pack and no cell type is configured, we can't estimate --
        so we ask the user to pick their cell type via a repair prompt.
        """
        bms = data.get("bms")
        data["soc_percent"] = None
        data["soc_estimated"] = False

        if bms is None:
            return

        reported = bms.soc_percent
        if reported > 0:
            data["soc_percent"] = reported
            self._clear_cell_type_issue()
            return

        # BMS SoC is 0. Try a voltage estimate if we know the cell type.
        estimate = estimate_soc(bms.cells, self._cell_type)
        if estimate is not None:
            data["soc_percent"] = estimate
            data["soc_estimated"] = True
            self._clear_cell_type_issue()
            return

        # No estimate. If the pack looks healthy but we lack a cell type, that's
        # the exact case worth prompting for.
        if self._cell_type == CELL_TYPE_UNKNOWN and cells_look_healthy(bms.cells):
            self._raise_cell_type_issue()
        else:
            self._clear_cell_type_issue()

    def _raise_cell_type_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._cell_type_issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="cell_type_missing",
            data={"entry_id": self._entry_id},
        )

    def _clear_cell_type_issue(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._cell_type_issue_id)

    def _controller_can_candidates(self) -> list[int]:
        """Configured CAN ID first, then every other discovered node."""
        candidates = [self._controller_can_id]
        candidates += [n for n in self._can_nodes if n not in candidates]
        return candidates

    async def _refresh_can_nodes(self) -> None:
        """Best-effort refresh of the cached CAN node list.

        Refreshed when the cache is empty or every STATIC_REFRESH_EVERY_POLLS
        cycles. Never fatal -- a failed ping just leaves the previous cache in
        place (or empty, in which case only the configured ID is tried).
        """
        due = not self._can_nodes or (
            self._poll_count % STATIC_REFRESH_EVERY_POLLS == 0
        )
        if not due:
            return
        try:
            self._can_nodes = await self._client.get_can_nodes()
            _LOGGER.debug("CAN nodes: %s", self._can_nodes)
        except (VescProtocolError, OSError, TimeoutError) as err:
            _LOGGER.debug("CAN node refresh failed (keeping cached list): %s", err)

    async def _read_controller(self) -> ControllerValues:
        """Try each candidate CAN ID; first to decode wins and is remembered."""
        last_err: Exception | None = None
        for can_id in self._controller_can_candidates():
            try:
                values = await self._client.get_controller_values(can_id)
            except (VescProtocolError, OSError, TimeoutError) as err:
                last_err = err
                _LOGGER.debug("Controller read failed for CAN %s: %s", can_id, err)
                continue
            if can_id != self._controller_can_id:
                _LOGGER.info(
                    "Resolved controller CAN ID from %s to %s",
                    self._controller_can_id,
                    can_id,
                )
                self._controller_can_id = can_id
            return values
        raise VescProtocolError(
            f"controller unreachable on any CAN candidate "
            f"{self._controller_can_candidates()}: {last_err}"
        )

    async def _async_update_data(self) -> dict:
        self._poll_count += 1
        data: dict = {
            "controller_connected": False,
            "controller": None,
            "bms_ok": False,
            "bms": None,
            "soc_percent": None,
            "soc_estimated": False,
            "odometer_m": None,
        }

        await self._refresh_can_nodes()

        try:
            data["controller"] = await self._read_controller()
            data["controller_connected"] = True
        except (VescProtocolError, OSError, TimeoutError) as err:
            _LOGGER.debug("Controller unreachable this cycle: %s", err)
            return data  # no point trying BMS if the controller itself is down

        # Persistent odometer (survives reboots). Best-effort -- a failed read
        # just leaves the previous value in place, it never fails the poll.
        try:
            data["odometer_m"] = await self._client.get_odometer(
                self._controller_can_id
            )
        except (VescProtocolError, OSError, TimeoutError) as err:
            _LOGGER.debug("Odometer read failed this cycle: %s", err)

        try:
            data["bms"] = await self._client.get_bms_values()
            data["bms_ok"] = True
        except (VescProtocolError, OSError, TimeoutError) as err:
            _LOGGER.warning(
                "Controller responded but BMS read failed (check bms_can_id / wiring): %s",
                err,
            )

        self._update_soc(data)
        return data
