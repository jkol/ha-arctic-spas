"""Local network client for Arctic Spa using the port 12121 persistent session protocol.

Protocol (reverse-engineered, confirmed via direct testing):
  - Magic preamble: 0xABAD1D3A
  - Frame: magic(4) + zeros(12) + type(2 BE) + size(2 BE) + payload(n)
  - No checksum/CRC — the zeros field is always zero
  - Persistent TCP connection — spa only allows one client at a time
  - Protocol is request-response since 1.0.48: each keepalive triggers one
    response frame; spa stays silent otherwise (no spontaneous broadcast)
  - Spa requires ~500ms after TCP connect before it will respond to keepalives
  - Response alternates: type=0x0000 (spa_live) then type=0x0003 (caps frame)
  - type=0x0003 appears to be a capability/config manifest (boolean fields)
  - Commands: type=0x0001, protobuf payload, field 1=setpoint confirmed

SpaCommand (type=0x0001) field numbers — CONFIRMED from coldfire proto schema
(com.levven.protobuf.coldfire.SpaCommand$spa_command, APK reverse-engineering):
  field  1  = SET_TEMPERATURE_SETPOINT_FAHRENHEIT  ← CONFIRMED by test
  field  2  = SET_PUMP_1   (SET_PUMP_STATUS enum: 0=off, 1=low, 2=high)
  field  3  = SET_PUMP_2
  field  4  = SET_PUMP_3
  field  5  = SET_PUMP_4
  field  6  = SET_PUMP_5
  field  7  = SET_BLOWER_1 (bool)
  field  8  = SET_BLOWER_2 (bool)
  field  9  = SET_LIGHTS   (bool)  ← CONFIRMED by test
  field 10  = SET_STEREO   (bool)
  field 11  = SET_FILTER   (bool)
  field 12  = SET_ONZEN    (bool)
  field 13  = SET_OZONE    (bool)
  field 14  = SET_EXHAUST_FAN (bool)
  field 15  = SET_SAUNA_STATE (enum)
  field 16  = SET_SAUNA_TIME_LEFT (int32)
  field 17  = ALL_ON       (bool)  ← "EZ" Easy Mode button (+ stereo field 10)
  field 18  = SET_FOGGER   (bool)
  field 19  = SPABOY_BOOST (bool)  ← SpaBoy chlorine boost (NOT the filter boost)
NOTE: Filter boost uses LpcProtos$lpc_command field 16, packet type 0x0071 (see activate_boost())
  field 20  = PACK_RESET   (bool)
  field 21  = LOG_DUMP     (bool)
  field 22  = SET_SDS      (bool)
  field 23  = SET_YESS     (bool)

NOTE: Economy/easymode has NO command field in the coldfire schema — it is
read-only in the local protocol (shown in spa_live field 22). Use cloud API
for easymode control.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, Callable

from .api import ArcticSpaApiError, ArcticSpaClientBase
from .spa_data import normalise_local

_LOGGER = logging.getLogger(__name__)

# ── Protocol constants ────────────────────────────────────────────────────────
_MAGIC             = b"\xab\xad\x1d\x3a"
_HDR_SIZE          = 20
_KEEPALIVE         = _MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
_KEEPALIVE_INTERVAL = 0.65  # seconds — spa cycle is ~640ms
_CMD_TYPE          = 0x0001
_PORT              = 12121

# Initialization sequence sent immediately after TCP connect (reverse-engineered
# from SendFifo.initialize() in the DirectConnect APK — com.levven.comm.SendFifo).
# Frames are sent 50ms apart so the spa can process each before the next arrives.
# Types 0x0004 and 0x0007 are omitted — confirmed to cause the spa to stop
# responding and prevent cloud reconnection after disconnect.
# Type 0x0005 (sendClock) also omitted — requires a real clock payload.
_INIT_TYPES = (0x0003, 0x0006, 0x0030, 0x0002, 0x0000, 0x000d)

# Reconnect backoff delays (seconds): 5s, 10s, 30s, 60s, then 120s indefinitely.
_RECONNECT_DELAYS = (5, 10, 30, 60, 120)

# ── spa_live decode maps ──────────────────────────────────────────────────────
# pH 5-state scale confirmed 2026-03-23 from SpaBoy Status app screen
# (lo, hi, status_string)
_PH_SCALE = [
    (6.2, 6.7, "low"),
    (6.8, 7.1, "caution_low"),
    (7.2, 7.7, "ok"),
    (7.8, 8.1, "caution_high"),
    (8.2, 8.8, "high"),
]


# Heater element status enum (fields 12 and 13 of spa_live). ALL VALUES CONFIRMED 2026-03-24:
#   0=idle     — elements off, no demand
#   1=warmup   — flow-verification delay before elements fire; ADC = pump1=low only (no heater draw)
#   2=heating  — elements on; confirmed ADC=2386 (~4461W total) with pump1=low
#   3=cooldown — elements off, pump circulating to cool heater manifold
_HEATER_STATUS: dict[int, str] = {0: "idle", 1: "warmup", 2: "heating", 3: "cooldown"}


def _compute_ph_status(ph: float) -> str:
    for lo, hi, label in _PH_SCALE:
        if lo <= ph <= hi:
            return label
    return "low" if ph < 6.2 else "high"


# ── Protobuf helpers ──────────────────────────────────────────────────────────

def _encode_varint(value: int) -> bytes:
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def _decode_proto_varint_fields(data: bytes) -> dict[int, int]:
    """Decode all wire-type-0 (varint) fields from a protobuf payload."""
    fields: dict[int, int] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _decode_varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if wt == 0:
            v, pos = _decode_varint(data, pos)
            fields[fn] = v
        elif wt == 2:
            length, pos = _decode_varint(data, pos)
            pos += length
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


def _proto_field(field_number: int, value: int) -> bytes:
    """Encode a single protobuf varint field (wire type 0)."""
    return _encode_varint((field_number << 3) | 0) + _encode_varint(value)


def _build_command(payload: bytes) -> bytes:
    """Wrap a protobuf payload in a port-12121 command frame."""
    return _MAGIC + b"\x00" * 12 + struct.pack(">HH", _CMD_TYPE, len(payload)) + payload


def _build_packet(pkt_type: int, payload: bytes) -> bytes:
    """Wrap a protobuf payload in an arbitrary-type port-12121 frame."""
    return _MAGIC + b"\x00" * 12 + struct.pack(">HH", pkt_type, len(payload)) + payload


def _parse_spa_live(payload: bytes) -> dict[str, Any]:
    """Decode a type=0x0000 spa_live payload into a canonical dict."""
    f = _decode_proto_varint_fields(payload)
    return normalise_local(f)


def _compute_orp_status(orp: int) -> str:
    """Map ORP/CL value to 5-state status string.

    CL/ORP scale confirmed 2026-03-23 from SpaBoy Status app screen:
      < 400  = low, 400-499 = caution_low, 500-749 = ok,
      750-899 = caution_high, 900+ = high
    """
    if orp < 400:
        return "low"
    if orp < 500:
        return "caution_low"
    if orp < 750:
        return "ok"
    if orp < 900:
        return "caution_high"
    return "high"


def _parse_spaboy_live(payload: bytes, existing: dict[str, Any]) -> None:
    """Decode a type=0x0030 SpaBoy live chemistry payload and merge into status dict.

    Confirmed field mapping (2026-03-23, verified against SpaBoy app + cloud API):
      field 2  = ORP / "CL level" (app displays this directly; 679 = OK range)
      field 3  = pH × 100 (confirmed: 767 → 7.67)

    Status strings are computed from raw values using confirmed scale boundaries.
    Field 13 is NOT pH status despite being in range — do not use it for status.
    """
    f = _decode_proto_varint_fields(payload)
    if 2 in f:
        orp = f[2]
        existing["orp"] = orp
        existing["orp_status"] = _compute_orp_status(orp)
    if 3 in f:
        ph = round(f[3] / 100.0, 2)
        existing["ph"] = ph
        existing["ph_status"] = _compute_ph_status(ph)


# ── Local client ──────────────────────────────────────────────────────────────

class ArcticSpaLocalClient(ArcticSpaClientBase):
    """Arctic Spa client using the port 12121 persistent session protocol.

    Call ``await client.start()`` after construction to establish the connection
    and begin background keepalive/reader tasks. Call ``await client.stop()`` on
    integration unload to disconnect cleanly.

    ``get_status()`` returns the most recently received spa_live frame, updated
    continuously by the background reader. The first call blocks up to 3 seconds
    waiting for the initial frame.

    Commands are sent on the existing persistent connection (no reconnect needed).
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._status: dict[str, Any] = {}
        self._status_lock = asyncio.Lock()
        self._status_event = asyncio.Event()
        self._keepalive_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._running = False
        self._on_update: Callable[[dict], None] | None = None
        self._reconnect_attempt: int = 0
        # Raw protobuf field numbers from the first spa_live (0x0000) frame.
        # Used by resolve_local_capabilities() to determine which hardware is present.
        self._first_proto_fields: dict[int, int] = {}
        self._has_spaboy: bool = False  # True once a 0x0030 SpaBoy frame is received

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self, on_update: Callable[[dict], None] | None = None) -> None:
        """Connect to the spa, start background tasks, and launch supervisor.

        The supervisor watches the reader task and reconnects automatically if the
        connection drops.  This method returns as soon as the initial connection is
        established (or raises ArcticSpaApiError on failure).
        """
        self._on_update = on_update
        self._running = True
        await self._connect()
        self._supervisor_task = asyncio.ensure_future(self._supervisor_loop())

    async def stop(self) -> None:
        """Stop background tasks and disconnect cleanly."""
        self._running = False
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
        await self._cancel_io_tasks()
        await self._close_connection()

    async def _cancel_io_tasks(self) -> None:
        """Cancel keepalive and reader tasks, waiting for them to finish."""
        for task in (self._keepalive_task, self._reader_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._keepalive_task = None
        self._reader_task = None

    async def _supervisor_loop(self) -> None:
        """Watch the reader task and reconnect if the connection drops."""
        while self._running:
            # Wait for the reader task to finish (normally means connection lost)
            if self._reader_task:
                try:
                    await asyncio.shield(self._reader_task)
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if not self._running:
                break

            delay_idx = min(self._reconnect_attempt, len(_RECONNECT_DELAYS) - 1)
            delay = _RECONNECT_DELAYS[delay_idx]
            self._reconnect_attempt += 1
            _LOGGER.warning(
                "Local: connection lost — reconnecting in %ds (attempt %d)",
                delay,
                self._reconnect_attempt,
            )
            await self._cancel_io_tasks()
            await self._close_connection()
            await asyncio.sleep(delay)

            if not self._running:
                break

            try:
                await self._connect()
                self._reconnect_attempt = 0
                _LOGGER.info("Local: reconnected to spa at %s:%d", self._host, _PORT)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Local: reconnect failed: %s", err)
                # reader_task is None after failed connect; loop back to sleep

    async def _connect(self) -> None:
        self._status_event.clear()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, _PORT), timeout=5.0
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise ArcticSpaApiError(
                f"Cannot connect to spa at {self._host}:{_PORT}: {err}"
            ) from err

        self._reader = reader
        self._writer = writer
        # The spa requires ~500ms after TCP connect before it responds.
        # Then send the full initialization sequence (8 request frames) that the
        # DirectConnect app sends via SendFifo.initialize() before normal keepalives.
        # This registers the session correctly and allows the MQTT broker to
        # reconnect cleanly after the local session ends.
        await asyncio.sleep(0.5)
        for t in _INIT_TYPES:
            writer.write(_MAGIC + b"\x00" * 12 + struct.pack(">HH", t, 0))
            await writer.drain()
            await asyncio.sleep(0.05)
        self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())
        self._reader_task = asyncio.ensure_future(self._reader_loop())
        _LOGGER.debug("Connected to spa at %s:%d", self._host, _PORT)

    async def _close_connection(self) -> None:
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None

    # ── Background tasks ─────────────────────────────────────────────────────

    async def _keepalive_loop(self) -> None:
        """Send a keepalive frame every 650ms to hold the session.

        On write failure, cancels the reader task so the supervisor wakes up
        and initiates a reconnect.
        """
        while self._running:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            if not self._running:
                break
            if self._writer and not self._writer.is_closing():
                try:
                    self._writer.write(_KEEPALIVE)
                    await self._writer.drain()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Local: keepalive failed: %s", err)
                    # Wake the supervisor by cancelling the reader task
                    if self._reader_task and not self._reader_task.done():
                        self._reader_task.cancel()
                    break

    async def _reader_loop(self) -> None:
        """Read incoming frames and update the status cache."""
        while self._running and self._reader:
            try:
                hdr = await asyncio.wait_for(
                    self._reader.readexactly(_HDR_SIZE), timeout=3.0
                )
            except asyncio.TimeoutError:
                continue  # normal between cycles
            except asyncio.IncompleteReadError:
                _LOGGER.warning("Local: spa closed connection")
                break
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Local: read error: %s", err)
                break

            if hdr[:4] != _MAGIC:
                continue

            msg_type = struct.unpack_from(">H", hdr, 16)[0]
            size     = struct.unpack_from(">H", hdr, 18)[0]
            payload  = b""
            if size:
                try:
                    payload = await asyncio.wait_for(
                        self._reader.readexactly(size), timeout=2.0
                    )
                except Exception:  # noqa: BLE001
                    break

            if msg_type == 0x0000 and payload:
                try:
                    proto_fields = _decode_proto_varint_fields(payload)
                    new_status = _parse_spa_live(payload)
                    async with self._status_lock:
                        # Capture raw field numbers from the very first frame for
                        # capability detection (resolve_local_capabilities).
                        if not self._first_proto_fields:
                            self._first_proto_fields = proto_fields
                        # Preserve SpaBoy chemistry readings from 0x0030 across spa_live updates
                        for key in ("ph", "ph_status", "orp", "orp_status"):
                            if key in self._status:
                                new_status[key] = self._status[key]
                        self._status = new_status
                        snapshot = self._status.copy()
                    self._status_event.set()
                    if self._on_update:
                        self._on_update(snapshot)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Failed to parse spa_live: %s", err)

            elif msg_type == 0x0030 and payload:
                try:
                    async with self._status_lock:
                        _parse_spaboy_live(payload, self._status)
                        snapshot = self._status.copy()
                    self._has_spaboy = True
                    if self._on_update:
                        self._on_update(snapshot)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Failed to parse spaboy_live: %s", err)

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def commands_available(self) -> bool:
        return True  # local mode always has commands

    @property
    def spa_id(self) -> str | None:
        return None  # local mode has no cloud spa ID

    def get_first_spa_live_fields(self) -> dict[int, int]:
        """Return the raw protobuf field map from the first spa_live frame.

        Used by resolve_local_capabilities() to determine which hardware features
        are installed. Returns an empty dict before the first frame is received.
        """
        return self._first_proto_fields.copy()

    @property
    def has_spaboy(self) -> bool:
        """True if at least one SpaBoy chemistry (0x0030) frame has been received."""
        return self._has_spaboy

    # ── ArcticSpaClientBase interface ────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        """Return the most recently received spa status.

        Blocks up to 3 seconds waiting for the first status frame.
        """
        async with self._status_lock:
            has_status = bool(self._status)
        if not has_status:
            try:
                await asyncio.wait_for(self._status_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                raise ArcticSpaApiError(
                    "No status received from spa — is another client holding the session?"
                ) from None
        async with self._status_lock:
            if not self._status:
                raise ArcticSpaApiError("Spa not reachable")
            return self._status.copy()

    async def _send_command(self, payload: bytes) -> None:
        """Send a command frame on the persistent connection."""
        if not self._writer or self._writer.is_closing():
            raise ArcticSpaApiError("Not connected to spa")
        try:
            self._writer.write(_build_command(payload))
            await self._writer.drain()
        except (OSError, asyncio.TimeoutError) as err:
            raise ArcticSpaApiError(f"Command failed: {err}") from err

    # ── Control commands ─────────────────────────────────────────────────────

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        # SpaCommand field 1: set_temperature_setpoint_fahrenheit (CONFIRMED)
        await self._send_command(_proto_field(1, setpoint_f))
        return {}

    async def set_lights(self, on: bool) -> dict[str, Any]:
        # SpaCommand field 9 = lights (CONFIRMED: spa_live field 10 - 1 = 9)
        await self._send_command(_proto_field(9, int(on)))
        return {}

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        # SpaCommand pump fields: 2=pump1, 3=pump2, 4=pump3, 5=pump4, 6=pump5  (CONFIRMED field 2=pump1)
        # IMPORTANT: pump commands are CYCLE triggers, not direct state sets.
        # The spa ignores the value and advances: off→low→high→off (for pump1).
        # To reach a target state, calculate steps needed from current state.
        _pump_fields = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6}
        _state_order = {"off": 0, "low": 1, "high": 2, "on": 2}
        _cycle = ["off", "low", "high"]   # pump1 cycle; pump2+ may be off→high→off

        target = _state_order.get(state.lower(), 0)
        field = _pump_fields.get(pump)
        if field is None and pump != "all":
            return {}

        if pump == "all":
            # For "all" just send one trigger per pump — caller controls intent
            payload = b"".join(_proto_field(f, 1) for f in (2, 3, 4, 5, 6))
            await self._send_command(payload)
            return {}

        # Determine current state from cached status to calculate cycle steps
        async with self._status_lock:
            current_raw = self._status.get(f"pump{pump}")
        current_str = (current_raw or "off").lower()
        current = _state_order.get(current_str, 0)

        steps = (target - current) % len(_cycle)
        for i in range(steps):
            await self._send_command(_proto_field(field, 1))
            if i < steps - 1:
                await asyncio.sleep(0.4)   # brief pause between steps

        return {}

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        # SpaCommand blower fields: 7=SET_BLOWER_1, 8=SET_BLOWER_2 (bool)
        _blower_fields = {"1": 7, "2": 8}
        val = 1 if on else 0
        if blower == "all":
            payload = _proto_field(7, val) + _proto_field(8, val)
        elif blower in _blower_fields:
            payload = _proto_field(_blower_fields[blower], val)
        else:
            return {}
        await self._send_command(payload)
        return {}

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        # SpaCommand field 11 = SET_FILTER (bool) — confirmed from coldfire schema
        # frequency/duration/suspension require type=0x0002 config messages (not implemented)
        if state is not None:
            await self._send_command(_proto_field(11, int(state.lower() == "on")))
        return {}

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        # SpaCommand field 17 = ALL_ON (the "EZ" / Easy Mode button).
        # The app also sets field 10 (stereo) alongside allOn when turning on.
        # The spa firmware decides which pumps/jets activate from the ALL_ON command.
        payload = _proto_field(17, int(on))
        if on:
            payload += _proto_field(10, 1)   # stereo on, matching app behaviour
        await self._send_command(payload)
        return {}

    async def activate_boost(self) -> dict[str, Any]:
        # Filter boost uses LpcProtos$lpc_command field 16 (setBoost), packet type 0x0071.
        # This is distinct from coldfire SpaCommand field 19 (SPABOY_BOOST).
        # Source: SendFifo.sendBoostStart() → LpcProtos$lpc_command in DirectConnect APK.
        if not self._writer or self._writer.is_closing():
            raise ArcticSpaApiError("Not connected to spa")
        try:
            self._writer.write(_build_packet(0x0071, _proto_field(16, 1)))
            await self._writer.drain()
        except Exception as err:
            raise ArcticSpaApiError(f"Command failed: {err}") from err
        return {}

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        # SpaBoy chlorine boost: coldfire SpaCommand field 19 (SPABOY_BOOST), packet type 0x0001.
        # Source: SpaBoyController$1.smali → SpaCommand.setSpaboyBoost(true).
        await self._send_command(_proto_field(19, 1))
        return {}

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        # SpaBoy ORP target range: packet type 0x0032 with spaboySettings proto.
        # fields 6=orpHigh, 7=orpLow (confirmed from SpaBoyController APK).
        # Presets: Low=545/555, Mid=645/655, High=745/755 mV.
        payload = _proto_field(6, orp_high) + _proto_field(7, orp_low)
        if not self._writer or self._writer.is_closing():
            raise ArcticSpaApiError("Not connected to spa")
        try:
            self._writer.write(_build_packet(0x0032, payload))
            await self._writer.drain()
        except Exception as err:
            raise ArcticSpaApiError(f"Command failed: {err}") from err
        return {}

    async def set_sds(self, on: bool) -> dict[str, Any]:
        # SpaCommand field 22 = SET_SDS (bool) — confirmed from coldfire schema
        await self._send_command(_proto_field(22, int(on)))
        return {}

    async def set_yess(self, on: bool) -> dict[str, Any]:
        # SpaCommand field 23 = SET_YESS (bool) — confirmed from coldfire schema
        await self._send_command(_proto_field(23, int(on)))
        return {}

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        # SpaCommand field 18 = SET_FOGGER (bool) — confirmed from coldfire schema
        await self._send_command(_proto_field(18, int(on)))
        return {}
