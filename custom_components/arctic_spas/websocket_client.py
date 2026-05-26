"""Arctic Spa local WebSocket client (port 8765).

Connects to the Customer Portal WebSocket server running on the spa's Yocto
Linux board. Available on firmware YOC 3.x+ (replaces the port 12121 protobuf
protocol from YOC 1.x).

Protocol:
  - Endpoint: ws://<host>:8765
  - On connect, send {"query": 0} to trigger a full state dump
  - Server pushes JSON messages: {"action": "publish", "topic": "<t>", "data": {...}}
  - Topics: live, sett, const, error, status, update-status, diagnostic
  - Commands are sent as plain JSON: {"P1next": 1}, {"setTSP": 97}, etc.
  - The "live" topic streams at ~500ms intervals after initial query
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

import aiohttp

from .api import ArcticSpaApiError, ArcticSpaClientBase
from .spa_data import POWER_CALIBRATION, SpaCapabilities

_LOGGER = logging.getLogger(__name__)

_WS_PORT = 8765
_RECONNECT_DELAYS = (5, 10, 30, 60, 120)

# Map WebSocket "live" topic keys to canonical state dict keys.
_LIVE_KEY_MAP: dict[str, str] = {
    "STemp": "temperatureF",
    "TSP": "setpointF",
    "P1": "pump1",
    "P2": "pump2",
    "P3": "pump3",
    "P4": "pump4",
    "P5": "pump5",
    "BL1": "blower1",
    "BL2": "blower2",
    "Li": "lights",
    "H1": "heater1_state",
    "H2": "heater2_state",
    "Filter": "filter_status",
    "Fan": "exhaust",
    "HTemp": "heater_outlet_temp_f",
    "Econ": "economy",
    "Current": "current_adc",
    "AllOn": "easymode",
    "Fogger": "fogger",
    "SDS": "sds",
    "Yess": "yess",
}

_PUMP_STATUS_MAP: dict[int, str] = {0: "off", 1: "low", 2: "high"}
_FILTER_STATUS_MAP: dict[int, str] = {
    0: "idle", 1: "purge", 2: "filtering", 3: "suspended",
    4: "overtemperature", 5: "resuming", 6: "boost", 7: "sanitize",
}
_HEATER_STATUS_MAP: dict[int, str] = {
    0: "idle", 1: "warmup", 2: "heating", 3: "cooldown",
}

_SPABOY_COLOR_MAP: dict[int, str] = {
    0: "low", 1: "caution_low", 2: "ok", 3: "caution_high", 4: "high",
}

# Error labels from the Customer Portal source (arcticLabels.enums.ts).
_ERROR_LABELS: dict[int, str] = {
    0: "No Flow",
    1: "Flow Switch",
    2: "Heater Over Temperature",
    3: "Spa Over Temperature",
    4: "Spa Temperature Probe",
    5: "Spa High Limit",
    7: "Freeze Protect",
    8: "PH High",
    9: "Heater Probe Disconnected",
    11: "SpaBoy Comm Error",
    13: "Heater Way Above Water Temp",
    14: "ORP Not Responding To Production",
    15: "PH Too Low (<6.5)",
}


def _normalise_ws_live(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a WebSocket 'live' topic payload to the canonical state dict."""
    result: dict[str, Any] = {
        "connected": True,
        "errors": [],
        "data_timestamp": time.monotonic(),
    }

    for ws_key, canonical_key in _LIVE_KEY_MAP.items():
        if ws_key not in payload:
            continue
        value = payload[ws_key]

        if canonical_key in ("pump1", "pump2", "pump3", "pump4", "pump5"):
            if isinstance(value, int):
                # New firmware uses values >15 for "high" in the portal UI
                if value > 15:
                    value = "high"
                else:
                    value = _PUMP_STATUS_MAP.get(value, "off")
        elif canonical_key == "filter_status":
            if isinstance(value, int):
                value = _FILTER_STATUS_MAP.get(value, "idle")
        elif canonical_key in ("heater1_state", "heater2_state"):
            if isinstance(value, int):
                value = _HEATER_STATUS_MAP.get(value, "idle")
        elif canonical_key in ("lights", "easymode", "economy", "exhaust", "fogger", "sds", "yess"):
            value = bool(value)
        elif canonical_key in ("blower1", "blower2"):
            value = int(bool(value))

        result[canonical_key] = value

    if "filter_status" in result:
        result["filter_on"] = result["filter_status"] != "idle"

    if "current_adc" in result:
        adc = result["current_adc"]
        if isinstance(adc, (int, float)):
            result["power_w"] = round(adc * POWER_CALIBRATION)

    # SpaBoy data is inline in the live topic on this firmware
    if payload.get("sbpH") is not None:
        result["ph"] = round(payload["sbpH"] / 100.0, 2)
    if payload.get("sbORP") is not None:
        result["orp"] = payload["sbORP"]
    if payload.get("sbpHind") is not None:
        result["ph_status"] = _SPABOY_COLOR_MAP.get(payload["sbpHind"], "ok")
    if payload.get("sbORPind") is not None:
        result["orp_status"] = _SPABOY_COLOR_MAP.get(payload["sbORPind"], "ok")

    return result


def _normalise_ws_sett(payload: dict[str, Any], existing: dict[str, Any]) -> None:
    """Merge a WebSocket 'sett' topic payload into the canonical state dict."""
    if payload.get("TSP") is not None:
        existing["setpointF"] = payload["TSP"]
    if payload.get("FF") is not None:
        existing["filtration_frequency"] = payload["FF"]
    if payload.get("FD") is not None:
        existing["filtration_duration"] = payload["FD"]

    # SpaBoy ORP targets
    if payload.get("SBORPhi") is not None:
        existing["spaboy_orp_high"] = payload["SBORPhi"]
    if payload.get("SBORPlo") is not None:
        existing["spaboy_orp_low"] = payload["SBORPlo"]
    if payload.get("SBpHhi") is not None:
        existing["spaboy_ph_high"] = payload["SBpHhi"]
    if payload.get("SBpHlo") is not None:
        existing["spaboy_ph_low"] = payload["SBpHlo"]


def _normalise_ws_error(payload: dict[str, Any], existing: dict[str, Any]) -> None:
    """Merge a WebSocket 'error' topic payload into the canonical state dict."""
    errors: list[str] = []
    for i in range(64):
        key = f"ERR{i}"
        if payload.get(key):
            label = _ERROR_LABELS.get(i, "")
            if label:
                errors.append(f"ER {i:02d}: {label}")
    existing["errors"] = errors


class ArcticSpaWebSocketClient(ArcticSpaClientBase):
    """Local WebSocket client for Arctic Spa (port 8765, firmware YOC 3.x+).

    Maintains a persistent aiohttp WebSocket connection and calls on_update
    whenever new spa data arrives. Commands are sent as JSON on the same
    WebSocket connection.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._state: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        self._config: dict[str, Any] = {}
        self._on_update: Callable[[dict[str, Any]], None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reader_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._running = False
        self._reconnect_attempt: int = 0

        self._config_ready = asyncio.Event()
        self._first_live_received = asyncio.Event()

    # ── Public properties ────────────────────────────────────────────────────

    @property
    def commands_available(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def get_settings(self) -> dict[str, Any]:
        return self._settings.copy()

    def get_config(self) -> dict[str, Any]:
        return self._config.copy()

    # ── ArcticSpaClientBase interface ────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        return self._state.copy()

    def _send_command(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise ArcticSpaApiError("WebSocket not connected — cannot send command")
        asyncio.ensure_future(self._ws.send_json(payload))

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        self._send_command({"setTSP": setpoint_f})
        return {}

    async def set_lights(self, on: bool) -> dict[str, Any]:
        # WebSocket protocol uses "cycle next" — Linext toggles lights
        self._send_command({"Linext": 1})
        return {}

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        # Cycle-next command for each pump
        self._send_command({f"P{pump}next": 1})
        return {}

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        key = f"Bl{blower}next" if blower == "1" else f"BL{blower}next"
        self._send_command({key: 1})
        return {}

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        if state is not None:
            self._send_command({"FLTRboost": 1})
        settings: dict[str, Any] = {}
        if frequency is not None:
            settings["setFF"] = frequency
        if duration is not None:
            settings["setFD"] = duration
        if suspension is not None:
            settings["setFS"] = suspension
        if settings:
            self._send_command(settings)
        return {}

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        # No direct easymode toggle in the WS command enum;
        # the portal doesn't expose it either. Use P1next as fallback.
        _LOGGER.warning("WebSocket: easymode toggle not available in this firmware")
        return {}

    async def activate_boost(self) -> dict[str, Any]:
        self._send_command({"FLTRboost": 1})
        return {}

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        self._send_command({"SBboost": 1})
        return {}

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        self._send_command({"SBORPhi": orp_high, "SBORPlo": orp_low})
        return {}

    async def set_sds(self, on: bool) -> dict[str, Any]:
        self._send_command({"SDSnext": 1})
        return {}

    async def set_yess(self, on: bool) -> dict[str, Any]:
        self._send_command({"YESSnext": 1})
        return {}

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        self._send_command({"Fgnext": 1})
        return {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self, on_update: Callable[[dict[str, Any]], None]) -> None:
        self._on_update = on_update
        self._running = True
        await self._connect()
        self._supervisor_task = asyncio.ensure_future(self._supervisor_loop())

    async def wait_for_config(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._config_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "WebSocket: settings not received within %.0fs — capabilities may use defaults",
                timeout,
            )
            return False

    async def stop(self) -> None:
        self._running = False
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        await self._close()

    async def _connect(self) -> None:
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(
                    f"ws://{self._host}:{_WS_PORT}",
                    origin=f"http://{self._host}",
                ),
                timeout=10.0,
            )
        except Exception as err:
            await self._close()
            raise ArcticSpaApiError(
                f"Cannot connect to spa WebSocket at {self._host}:{_WS_PORT}: {err}"
            ) from err

        # Send initial query to trigger full state dump
        await self._ws.send_json({"query": 0})
        self._reader_task = asyncio.ensure_future(self._reader_loop())
        _LOGGER.debug("WebSocket: connected to %s:%d", self._host, _WS_PORT)

    async def _close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _supervisor_loop(self) -> None:
        while self._running:
            if self._reader_task:
                try:
                    await asyncio.shield(self._reader_task)
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if not self._running:
                break

            # Mark disconnected
            self._state["connected"] = False
            if self._on_update:
                self._on_update(self._state.copy())

            delay_idx = min(self._reconnect_attempt, len(_RECONNECT_DELAYS) - 1)
            delay = _RECONNECT_DELAYS[delay_idx]
            self._reconnect_attempt += 1
            _LOGGER.warning(
                "WebSocket: connection lost — reconnecting in %ds (attempt %d)",
                delay, self._reconnect_attempt,
            )
            await self._close()
            await asyncio.sleep(delay)

            if not self._running:
                break

            try:
                await self._connect()
                self._reconnect_attempt = 0
                _LOGGER.info("WebSocket: reconnected to %s:%d", self._host, _WS_PORT)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("WebSocket: reconnect failed: %s", err)

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (ValueError, TypeError):
                    continue

                self._dispatch(data)

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    def _dispatch(self, data: dict[str, Any]) -> None:
        # Direct topic+data format: {"action": "publish", "topic": ..., "data": ...}
        if data.get("action") == "publish" and "topic" in data and "data" in data:
            topic = data["topic"]
            payload = data["data"]
            self._handle_topic(topic, payload)
            return

        # Wrapped diagnostic message
        if data.get("type") == "text" and "message" in data:
            try:
                inner = json.loads(data["message"])
                if inner.get("action") == "publish":
                    self._handle_topic(inner.get("topic", ""), inner.get("data", {}))
            except (ValueError, TypeError):
                pass
            return

        # RTC clock message — ignore
        if "RTC" in data:
            return

    def _handle_topic(self, topic: str, payload: dict[str, Any]) -> None:
        if topic == "live":
            new_data = _normalise_ws_live(payload)
            self._state.update(new_data)
            if not self._first_live_received.is_set():
                self._first_live_received.set()
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "sett":
            self._settings = payload.copy()
            _normalise_ws_sett(payload, self._state)
            if not self._config_ready.is_set():
                self._config_ready.set()
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "const":
            self._config = payload.copy()

        elif topic == "error":
            _normalise_ws_error(payload, self._state)
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "status":
            # Status flags — log but don't push unless we add status sensors
            _LOGGER.debug("WebSocket: status topic received")

        elif topic == "diagnostic":
            _LOGGER.debug("WebSocket: diagnostic: %s", payload)


def resolve_ws_capabilities(
    settings: dict[str, Any],
    state: dict[str, Any],
) -> SpaCapabilities:
    """Resolve capabilities from the WebSocket 'sett' payload.

    The 'sett' topic includes cfgP1..cfgP5, cfgB1, cfgB2, cfgSB, etc. —
    boolean flags indicating which hardware is installed.
    """
    return SpaCapabilities(
        pump2=bool(settings.get("cfgP2", False)),
        pump3=bool(settings.get("cfgP3", False)),
        pump4=bool(settings.get("cfgP4", False)),
        pump5=bool(settings.get("cfgP5", False)),
        blower1=bool(settings.get("cfgB1", False)),
        blower2=bool(settings.get("cfgB2", False)),
        sds=bool(settings.get("cfgSDS", False)),
        yess=bool(settings.get("cfgYESS", False)),
        fogger=bool(settings.get("cfgFG", False)),
        spaboy=bool(settings.get("cfgSB", False)),
        has_power_sensor=True,
        has_exhaust=bool(settings.get("cfgEx", False)),
        has_economy=True,
        has_heater_outlet_temp=True,
        has_heater_states=True,
        has_filter_data=True,
        has_filter_schedule=True,
        has_filter_details=False,
        has_filter_suspension=True,
        has_spaboy_boost=bool(settings.get("cfgSB", False)),
        has_errors=True,
        has_network_info=False,
        has_rfid=bool(settings.get("cfgRFID", False)),
        has_peak_settings=False,
        firmware_lpc=str(settings.get("LPCFWVer")) if settings.get("LPCFWVer") else None,
        firmware_yocto=str(settings.get("YOCFWVer")) if settings.get("YOCFWVer") else None,
    )
