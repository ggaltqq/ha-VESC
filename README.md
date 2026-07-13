# HA-VESC (local)

Vibe-coded local-polling Home Assistant integration for a VESC Express-equipped
VESC board. Talks directly to the board over TCP on your LAN using
the VESC binary protocol — no MQTT broker, no cloud, read-only.

## Contents

- [Entities](#entities)
- [Installation](#installation)
  - [Via HACS (custom repository)](#via-hacs-custom-repository--recommended)
  - [Manual](#manual)
- [Setup](#setup)
- [State of Charge estimation](#state-of-charge-estimation)
- [Known limitations / things to verify against your firmware](#known-limitations--things-to-verify-against-your-firmware)

## Entities

Three entities are created per configured board:

- **`sensor.<name>_controller`** — state is the literal string `connected`
  or `disconnected`, reflecting whether the board answered this poll cycle.
  Never goes to `unavailable`/`unknown` — it always has an opinion. Extra
  attributes: duty cycle, RPM, input voltage, motor/input current, MOSFET
  and motor temps, amp-hours, watt-hours, tachometer, fault code.
- **`sensor.<name>_battery`** — state is State of Charge (%). Standard HA
  numeric-sensor availability rules apply:
  - `unavailable` — the controller itself isn't reachable (board asleep or
    offline — this is expected/routine, e.g. during long-term storage)
  - `unknown` — the controller answered, but the BMS-specific read failed
    (worth investigating — check `bms_can_id` and CAN wiring)
  - a number — everything's fine
  Extra attributes: `charging`, `pack_voltage`, `charge_voltage`,
  `pack_current`, `current_main`, `current_ic`, `soc_estimated`,
  `bms_reported_soc`, cell count and min/max cell voltage.

  `charging` is derived from the BMS charge-target voltage (`charge_voltage`
  rises to ~pack voltage when a charger is connected, drops to 0 otherwise) —
  more reliable than current sign. `pack_current` reports the main-shunt
  current (`current_main`), or falls back to the BMS-IC current (`current_ic`)
  when the shunt value is unpopulated (0 on some ENNOID configs); negative
  means charging, positive means discharging.

- **`sensor.<name>_odometer`** — lifetime distance (km), the persistent "life"
  odometer stored on the controller (the same value VESC Tool shows). It
  survives board reboots and never resets. Uses the `total_increasing` state
  class so Home Assistant's long-term statistics track it correctly. The value
  is always meters on the wire regardless of the VESC km/miles display setting
  (that setting is client-side only); it's converted to km here, and Home
  Assistant will display miles automatically if your HA unit system is imperial.

## Installation

This integration is **not** in the HACS default store, so it won't show up if
you just search HACS. Install it as a HACS *custom repository* (recommended) or
copy the files in manually. Both work — no GitHub release and no
`home-assistant/brands` entry are required for a custom-repository install (the
only cost of the missing brands entry is a generic fallback icon in the UI).

### Via HACS (custom repository) — recommended

> Don't have HACS yet? Install it first:
> <https://hacs.xyz/docs/use/download/download/>

1. In Home Assistant, open **HACS**.
2. Top-right **⋮** menu → **Custom repositories**.
3. **Repository**: `https://github.com/ggaltqq/ha-VESC`
   **Type/Category**: `Integration` → **Add**.
4. Close the dialog, then search HACS for **VESC Express** and open it →
   **Download**.
5. **Restart Home Assistant** (Settings → System → Restart).

To update later: HACS shows an update when this repo's default branch (or a new
release, if one is tagged) moves ahead — click **Update**, then restart HA.

### Manual

1. Copy the folder `custom_components/vesc_express/` from this repo into your
   Home Assistant config directory, so you end up with:
   `<config>/custom_components/vesc_express/`
   (`<config>` is the folder containing `configuration.yaml`.)
2. **Restart Home Assistant.**

To update: replace that folder with the newer version and restart again.

After either method, continue with [Setup](#setup) below to add the device.

## Setup

Settings → Devices & Services → Add Integration → search "VESC Express".
You'll be asked for:

- Host / IP address and TCP port (default `65102`) of the VESC Express
- Controller CAN ID — a *hint*, not a hard requirement. If it's wrong or left
  at the default, the integration discovers the real CAN node list via
  `COMM_PING_CAN` and auto-resolves the controller to whichever node answers,
  remembering it for later polls.
- BMS CAN ID
- Poll interval (seconds)
- Battery cell type — used to estimate State of Charge from cell voltage when
  the BMS reports 0% (see below). Leave it unset if unsure.

These can be changed later via the integration's "Configure" option without
re-adding it.

## State of Charge estimation

Some VESC-connected BMSes never populate the SoC field — it stays `0` even on
a full pack (you can confirm this in VESC Tool). When that happens, this
integration estimates SoC from the **resting cell voltage** instead:

- If the BMS reports a real (non-zero) SoC, that's used as-is.
- If the BMS reports `0` but the per-cell voltages are those of a healthy
  Li-ion cell, SoC is estimated from an open-circuit-voltage curve for the
  configured **cell type**. The `sensor.<name>_battery` attribute
  `soc_estimated` is `true` in this case, and `bms_reported_soc` keeps the raw
  BMS value.
- If no cell type is configured when this situation is detected, Home Assistant
  raises a **Repair** ("Select your battery cell type") — pick your cell
  (e.g. Samsung INR21700-50S) and the estimate turns on immediately.

Accuracy note: voltage → SoC is only meaningful **at rest**. Under load the
pack sags (estimate reads low); while charging it reads high. Good for a
parked-board dashboard, rough while riding.

## Known limitations / things to verify against your firmware

The VESC binary protocol's packet framing, CRC, and the telemetry decoders
(`COMM_GET_VALUES` controller struct and the variable-length
`COMM_BMS_GET_VALUES` struct) are validated field-for-field against real
ENNOID BMS + Thor controller hardware. All values are fixed-point integers on
the wire (never IEEE floats).

Transport: controller telemetry is read via `COMM_FORWARD_CAN` to the
configured **Controller CAN ID** (the motor controller lives behind the VESC
Express on the CAN bus), while BMS telemetry is queried directly on the primary
link. If controller telemetry never comes through, check the Controller CAN ID;
if BMS readings don't, check the Home Assistant log — a failed read logs the
payload length it got back, the fastest way to spot a firmware layout mismatch.

This integration is read-only by design — it never sends a write/set
command to the board, so an incorrect opcode can at worst cause a failed
read, not an unsafe action.
