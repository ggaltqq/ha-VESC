"""Minimal, read-only VESC binary protocol client over TCP.

This module intentionally implements ONLY the read side of the protocol.
It never encodes a write/set command, so even if an opcode constant is
wrong, the worst outcome is a malformed read request that the board
ignores or NAKs -- it cannot cause the board to do anything.

Packet framing (stable/standard across VESC firmware):
    start_byte  1 byte   0x02 (payload <= 255 bytes) or 0x03 (longer)
    length      1 or 2 bytes, big-endian
    payload     N bytes
    crc16       2 bytes, big-endian, CRC-CCITT (poly 0x1021, init 0x0000)
    end_byte    1 byte   0x03
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from .const import (
    COMM_BMS_GET_VALUES,
    COMM_FORWARD_CAN,
    COMM_FW_VERSION,
    COMM_GET_VALUES,
    COMM_GET_VALUES_SETUP,
    COMM_PING_CAN,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
)

_CRC_TABLE: list[int] = []


def _build_crc_table() -> None:
    if _CRC_TABLE:
        return
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
        _CRC_TABLE.append(crc)


def crc16(data: bytes) -> int:
    """CRC-CCITT (XModem variant) used by the VESC protocol."""
    _build_crc_table()
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


def encode_packet(payload: bytes) -> bytes:
    """Wrap a payload in a full VESC packet frame."""
    if len(payload) <= 255:
        header = bytes([0x02, len(payload)])
    else:
        header = bytes([0x03, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF])
    crc = crc16(payload)
    return header + payload + bytes([(crc >> 8) & 0xFF, crc & 0xFF, 0x03])


def encode_simple_command(command_id: int) -> bytes:
    """Encode a command with no payload beyond its opcode (e.g. FW_VERSION)."""
    return encode_packet(bytes([command_id]))


def encode_forward_can(can_id: int, inner_command_id: int) -> bytes:
    """Encode COMM_FORWARD_CAN wrapping a simple inner command."""
    payload = bytes([COMM_FORWARD_CAN, can_id, inner_command_id])
    return encode_packet(payload)


class VescProtocolError(Exception):
    """Raised for a malformed/incomplete response."""


async def _read_one_packet(reader: asyncio.StreamReader, timeout: float) -> bytes:
    """Read exactly one framed packet and return its payload (opcode + data)."""
    start = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
    if start[0] == 0x02:
        length_bytes = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
        length = length_bytes[0]
    elif start[0] == 0x03:
        length_bytes = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        length = struct.unpack(">H", length_bytes)[0]
    else:
        raise VescProtocolError(f"Unexpected start byte: {start[0]:#x}")

    payload = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    crc_bytes = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
    end_byte = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)

    if end_byte[0] != 0x03:
        raise VescProtocolError("Missing end byte")
    if struct.unpack(">H", crc_bytes)[0] != crc16(payload):
        raise VescProtocolError("CRC mismatch")

    return payload


@dataclass
class ControllerValues:
    """A best-effort decode of COMM_GET_VALUES.

    Field layout matches the long-standing common VESC firmware struct.
    If your firmware adds/removes fields this may need adjustment --
    if parsing fails, the raw payload length is included in the error to
    help diagnose a layout mismatch.
    """

    temp_mos: float
    temp_motor: float
    current_motor: float
    current_in: float
    duty_now: float
    rpm: float
    v_in: float
    amp_hours: float
    amp_hours_charged: float
    watt_hours: float
    watt_hours_charged: float
    tachometer: int
    tachometer_abs: int
    fault_code: int


def parse_controller_values(payload: bytes) -> ControllerValues:
    # payload[0] is the COMM_GET_VALUES opcode echoed back.
    #
    # Wire layout (big-endian, fixed-point ints -- NEVER IEEE floats):
    #   temp_fet            i16 / 10
    #   temp_motor          i16 / 10
    #   avg_motor_current   i32 / 100
    #   avg_input_current   i32 / 100
    #   avg_id              i32 / 100   (unused here)
    #   avg_iq              i32 / 100   (unused here)
    #   duty_now            i16 / 1000
    #   rpm                 i32
    #   v_in                i16 / 10
    #   amp_hours           i32 / 1e4
    #   amp_hours_charged   i32 / 1e4
    #   watt_hours          i32 / 1e4
    #   watt_hours_charged  i32 / 1e4
    #   tachometer          i32
    #   tachometer_abs      i32
    #   fault_code          u8
    # (further fields -- pid_pos, controller_id, mos temps, vd/vq -- follow but
    # are not needed; we only decode the stable prefix through fault_code.)
    body = payload[1:]
    fmt = ">hhiiiihihiiiiiiB"
    needed = struct.calcsize(fmt)
    if len(body) < needed:
        raise VescProtocolError(
            f"COMM_GET_VALUES payload too short: got {len(body)} bytes, "
            f"need at least {needed} for the expected field layout"
        )
    try:
        (
            temp_fet,
            temp_motor,
            current_motor,
            current_in,
            _id,
            _iq,
            duty_now,
            rpm,
            v_in,
            amp_hours,
            amp_hours_charged,
            watt_hours,
            watt_hours_charged,
            tachometer,
            tachometer_abs,
            fault_code,
        ) = struct.unpack_from(fmt, body)
    except struct.error as exc:
        raise VescProtocolError(
            f"Could not parse COMM_GET_VALUES payload (len={len(body)}): {exc}"
        ) from exc

    return ControllerValues(
        temp_mos=temp_fet / 10,
        temp_motor=temp_motor / 10,
        current_motor=current_motor / 100,
        current_in=current_in / 100,
        duty_now=duty_now / 1000,
        rpm=float(rpm),
        v_in=v_in / 10,
        amp_hours=amp_hours / 1e4,
        amp_hours_charged=amp_hours_charged / 1e4,
        watt_hours=watt_hours / 1e4,
        watt_hours_charged=watt_hours_charged / 1e4,
        tachometer=tachometer,
        tachometer_abs=tachometer_abs,
        fault_code=fault_code,
    )


@dataclass
class BmsValues:
    """Decode of COMM_BMS_GET_VALUES.

    The BMS payload is variable-length: fixed scalars, then a per-cell voltage
    array and balancing array (count-prefixed), then a temperature array (also
    count-prefixed), and only then SoC. We must walk the counts to reach SoC --
    it is NOT at a fixed offset. All values are fixed-point ints on the wire.
    """

    # SoC as *reported by the BMS*. Some BMSes never populate this (stays 0
    # even on a full pack); the integration can fall back to a voltage-based
    # estimate -- see soc.py and the coordinator.
    soc_percent: float
    pack_voltage: float
    charge_voltage: float  # charger-target voltage; ~pack V when a charger is on, else 0
    pack_current: float  # best-available current (i_in, or i_in_ic when i_in is unpopulated)
    current_main: float  # i_in  (main shunt; 0 on BMSes that don't populate it)
    current_ic: float  # i_in_ic (measured by the BMS IC)
    charging: bool
    cells: list[float]  # per-cell voltages, for the SoC estimate fallback


# A charger is considered present when the reported charge-target voltage is a
# meaningful fraction of the pack voltage (scales with any pack size). On BMSes
# that don't populate i_in, current sign alone can't tell charging from idle --
# charge_voltage is the reliable signal.
_CHARGE_DETECT_FRACTION = 0.5


def parse_bms_values(payload: bytes) -> BmsValues:
    body = payload[1:]
    offset = 0

    def take(fmt: str):
        nonlocal offset
        values = struct.unpack_from(fmt, body, offset)
        offset += struct.calcsize(fmt)
        return values

    try:
        (pack_voltage_raw,) = take(">i")  # / 1e6
        (charge_voltage_raw,) = take(">i")  # / 1e6
        (current_main_raw,) = take(">i")  # i_in, / 1e6
        (current_ic_raw,) = take(">i")  # i_in_ic, / 1e6
        take(">i")  # amp_hours
        take(">i")  # watt_hours
        (cell_num,) = take(">B")
        cell_raw = take(f">{cell_num}h")  # per-cell voltages, / 1e3
        take(f">{cell_num}B")  # per-cell balancing state
        (temp_num,) = take(">B")
        take(f">{temp_num}h")  # sensor temps
        take(">h")  # temp_ic
        take(">h")  # temp_humidity
        take(">h")  # humidity
        take(">h")  # temp_max_cell
        (soc_raw,) = take(">h")  # / 1e3, a 0..1 fraction
    except struct.error as exc:
        raise VescProtocolError(
            f"Could not parse COMM_BMS_GET_VALUES payload (len={len(body)}): {exc}"
        ) from exc

    pack_voltage = pack_voltage_raw / 1e6
    charge_voltage = charge_voltage_raw / 1e6
    current_main = current_main_raw / 1e6
    current_ic = current_ic_raw / 1e6
    # Prefer the main-shunt current; fall back to the IC current when the BMS
    # doesn't populate i_in (it reads a flat 0 on some ENNOID configs).
    pack_current = current_main if abs(current_main) > 0.01 else current_ic
    return BmsValues(
        soc_percent=max(0.0, min(100.0, (soc_raw / 1e3) * 100)),
        pack_voltage=pack_voltage,
        charge_voltage=charge_voltage,
        pack_current=pack_current,
        current_main=current_main,
        current_ic=current_ic,
        cells=[c / 1e3 for c in cell_raw],
        charging=charge_voltage > pack_voltage * _CHARGE_DETECT_FRACTION,
    )


def parse_odometer(payload: bytes) -> int:
    """Odometer (meters) from a COMM_GET_VALUES_SETUP reply.

    Parsed from the end: the reply finishes with ...[odometer u32][uptime u32].
    """
    body = payload[1:]  # drop the echoed opcode
    if len(body) < 8:
        raise VescProtocolError(
            f"COMM_GET_VALUES_SETUP payload too short for odometer: {len(body)} bytes"
        )
    return int.from_bytes(body[-8:-4], "big", signed=False)


class VescClient:
    """Opens a short-lived TCP connection per poll, sends one request, reads one reply."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._retries = max(1, retries)

    async def _request_once(self, request: bytes) -> bytes:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=self._timeout
        )
        try:
            writer.write(request)
            await writer.drain()
            return await _read_one_packet(reader, self._timeout)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    async def _request(self, request: bytes) -> bytes:
        # The VESC Express TCP bridge resets/drops sockets transiently
        # (observed on real FW 6.5 hardware -- a fresh reconnect is frequently
        # reset before the reply). Because every request here is strictly
        # read-only, retrying with a short backoff is safe and turns those
        # transient resets into successful reads.
        last: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                return await self._request_once(request)
            except (VescProtocolError, OSError, TimeoutError) as err:
                last = err
                if attempt < self._retries:
                    await asyncio.sleep(0.2 * attempt)
        assert last is not None
        raise last

    async def get_fw_version(self) -> bytes:
        """Cheapest possible "is the controller there" probe."""
        return await self._request(encode_simple_command(COMM_FW_VERSION))

    async def get_controller_values(self, controller_can_id: int) -> ControllerValues:
        # The motor controller (e.g. Thor) sits behind the VESC Express on the
        # CAN bus, so the read must be forwarded to its CAN node -- a direct
        # COMM_GET_VALUES would query the Express itself, not the controller.
        payload = await self._request(
            encode_forward_can(controller_can_id, COMM_GET_VALUES)
        )
        return parse_controller_values(payload)

    async def get_odometer(self, controller_can_id: int) -> int:
        """Return the controller's persistent odometer in meters.

        Read from COMM_GET_VALUES_SETUP (forwarded to the controller). Unlike
        the tachometer, this value is stored on the board and survives reboots
        (it's the "life" distance shown in VESC Tool). The odometer is the
        second-to-last uint32 in the reply (followed by an uptime uint32), so
        we parse from the end -- robust to leading-field changes across
        firmware versions.
        """
        payload = await self._request(
            encode_forward_can(controller_can_id, COMM_GET_VALUES_SETUP)
        )
        return parse_odometer(payload)

    async def get_can_nodes(self) -> list[int]:
        """Return CAN node IDs seen on the bus (best-effort; see COMM_PING_CAN note in const.py)."""
        payload = await self._request(encode_simple_command(COMM_PING_CAN))
        return list(payload[1:])

    async def get_bms_values(self) -> BmsValues:
        # The BMS answers COMM_BMS_GET_VALUES directly on the primary link --
        # it is NOT forwarded over CAN.
        payload = await self._request(encode_simple_command(COMM_BMS_GET_VALUES))
        return parse_bms_values(payload)
