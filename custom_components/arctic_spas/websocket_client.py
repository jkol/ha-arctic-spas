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
import re
from pathlib import Path
from typing import Any, Callable

import aiohttp

from .api import ArcticSpaApiError, ArcticSpaClientBase
from .spa_data import (
    SpaCapabilities,
    normalise_native_error,
    normalise_native_live,
    normalise_native_sett,
    resolve_native_capabilities,
)

_LOGGER = logging.getLogger(__name__)

_WS_PORT = 8765
_RECONNECT_DELAYS = (5, 10, 30, 60, 120)
_MAC_RECOVERY_THRESHOLD = 3


_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}")


def _normalise_mac(mac: str) -> str:
    """Normalise a MAC address to lowercase colon-separated form."""
    return mac.replace("-", ":").lower()


async def async_resolve_mac(host: str) -> str | None:
    """Look up the MAC address for a given IP from the OS ARP/neighbor table."""
    # Linux (HAOS): read /proc/net/arp directly — no subprocess needed
    proc_arp = Path("/proc/net/arp")
    if proc_arp.exists():
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                None, proc_arp.read_text
            )
            for line in text.splitlines()[1:]:  # skip header
                parts = line.split()
                if len(parts) >= 4 and parts[0] == host and parts[3] != "00:00:00:00:00:00":
                    return _normalise_mac(parts[3])
        except OSError:
            pass

    # Fallback: parse `arp -a` output (Windows, macOS, other Linux)
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        text = stdout.decode(errors="replace")
        match = _MAC_RE.search(text)
        if match:
            return _normalise_mac(match.group())
    except (OSError, asyncio.TimeoutError):
        pass

    return None


async def async_resolve_ip_for_mac(mac: str) -> str | None:
    """Scan the OS ARP/neighbor table for an IP matching the given MAC."""
    target = _normalise_mac(mac)

    proc_arp = Path("/proc/net/arp")
    if proc_arp.exists():
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                None, proc_arp.read_text
            )
            for line in text.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and _normalise_mac(parts[3]) == target:
                    return parts[0]
        except OSError:
            pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        for line in stdout.decode(errors="replace").splitlines():
            mac_match = _MAC_RE.search(line)
            if mac_match and _normalise_mac(mac_match.group()) == target:
                # Extract IP — formats: "? (1.2.3.4) at aa:bb:..." or "1.2.3.4  aa-bb-..."
                ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                if ip_match:
                    return ip_match.group(1)
    except (OSError, asyncio.TimeoutError):
        pass

    return None




class ArcticSpaWebSocketClient(ArcticSpaClientBase):
    """Local WebSocket client for Arctic Spa (port 8765, firmware YOC 3.x+).

    Maintains a persistent aiohttp WebSocket connection and calls on_update
    whenever new spa data arrives. Commands are sent as JSON on the same
    WebSocket connection.
    """

    def __init__(
        self,
        host: str,
        mac: str | None = None,
        on_host_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._host = host
        self._mac = _normalise_mac(mac) if mac else None
        self._on_host_changed = on_host_changed
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

    async def _send_command(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise ArcticSpaApiError("WebSocket not connected — cannot send command")
        await self._ws.send_json(payload)

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        await self._send_command({"setTSP": setpoint_f})
        return {}

    async def set_lights(self, on: bool) -> dict[str, Any]:
        await self._send_command({"Linext": 1})
        return {}

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        await self._send_command({f"P{pump}next": 1})
        return {}

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        key = f"Bl{blower}next" if blower == "1" else f"BL{blower}next"
        await self._send_command({key: 1})
        return {}

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        if state is not None:
            await self._send_command({"FLTRboost": 1})
        settings: dict[str, Any] = {}
        if frequency is not None:
            settings["setFF"] = frequency
        if duration is not None:
            settings["setFD"] = duration
        if suspension is not None:
            settings["setFS"] = suspension
        if settings:
            await self._send_command(settings)
        return {}

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        _LOGGER.warning("WebSocket: easymode toggle not available in this firmware")
        return {}

    async def activate_boost(self) -> dict[str, Any]:
        await self._send_command({"FLTRboost": 1})
        return {}

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        await self._send_command({"SBboost": 1})
        return {}

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        await self._send_command({"SBORPhi": orp_high, "SBORPlo": orp_low})
        return {}

    async def set_sds(self, on: bool) -> dict[str, Any]:
        await self._send_command({"SDSnext": 1})
        return {}

    async def set_yess(self, on: bool) -> dict[str, Any]:
        await self._send_command({"YESSnext": 1})
        return {}

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        await self._send_command({"Fgnext": 1})
        return {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self, on_update: Callable[[dict[str, Any]], None]) -> None:
        self._on_update = on_update
        self._running = True
        await self._connect()
        self._supervisor_task = asyncio.create_task(self._supervisor_loop())

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
        self._reader_task = asyncio.create_task(self._reader_loop())
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

                if (
                    self._mac
                    and self._reconnect_attempt >= _MAC_RECOVERY_THRESHOLD
                    and self._reconnect_attempt % _MAC_RECOVERY_THRESHOLD == 0
                ):
                    await self._try_mac_recovery()

    async def _try_mac_recovery(self) -> None:
        """Attempt to find the spa's new IP via its MAC address in the ARP table."""
        _LOGGER.info(
            "WebSocket: attempting MAC-based IP recovery for %s", self._mac,
        )
        new_ip = await async_resolve_ip_for_mac(self._mac)
        if not new_ip or new_ip == self._host:
            _LOGGER.debug(
                "WebSocket: MAC recovery found no new IP (got %s, current %s)",
                new_ip, self._host,
            )
            return

        _LOGGER.info(
            "WebSocket: MAC %s resolved to new IP %s (was %s) — switching",
            self._mac, new_ip, self._host,
        )
        old_host = self._host
        self._host = new_ip
        try:
            await self._connect()
            self._reconnect_attempt = 0
            _LOGGER.info("WebSocket: reconnected to %s:%d after IP change", self._host, _WS_PORT)
            if self._on_host_changed:
                self._on_host_changed(new_ip)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "WebSocket: new IP %s also failed (%s) — reverting to %s",
                new_ip, err, old_host,
            )
            self._host = old_host

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
            new_data = normalise_native_live(payload)
            self._state.update(new_data)
            if not self._first_live_received.is_set():
                self._first_live_received.set()
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "sett":
            self._settings = payload.copy()
            normalise_native_sett(payload, self._state)
            if not self._config_ready.is_set():
                self._config_ready.set()
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "const":
            self._config = payload.copy()

        elif topic == "error":
            normalise_native_error(payload, self._state)
            if self._on_update:
                self._on_update(self._state.copy())

        elif topic == "status":
            # Status flags — log but don't push unless we add status sensors
            _LOGGER.debug("WebSocket: status topic received")

        elif topic == "diagnostic":
            _LOGGER.debug("WebSocket: diagnostic: %s", payload)


