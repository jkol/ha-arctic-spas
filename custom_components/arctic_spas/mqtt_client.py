"""Arctic Spa cloud MQTT client (WS-C).

Auth flow:
  1. POST https://myarcticspa.com/api/auth  {username, hash:null} → Salt
  2. SHA-1(base64_decode(salt) + password.utf16le) → hash
  3. POST https://myarcticspa.com/api/auth  {username, hash}      → UserId, Spas
  4. POST https://api.myarcticspa.com/access_token
       {grant_type, client_id, client_secret, username|spa_id, password=hash, spa}
     → JWT

Broker: tcp://broker.myarcticspa.com:1884 (or WSS if JWT aud contains "aws-iot")
MQTT username: JWT (+ authorizer query param for AWS)
MQTT password: "anything"

Topics subscribed:
  arctic/spa/{spa_id}/telemetry/spa
  arctic/spa/{spa_id}/telemetry/filters
  arctic/spa/{spa_id}/telemetry/errors
  arctic/spa/{spa_id}/telemetry/spaboy
  arctic/spa/{spa_id}/telemetry/heartbeat
  arctic/spa/{spa_id}/telemetry/update   (firmware progress — raw log only; Phase 2 will parse)
  arctic/spa/{spa_id}/telemetry/rfid     (RFID tag events — raw log only; Phase 2 will parse)
  arctic/spa/{spa_id}/settings/spa
  arctic/spa/{spa_id}/settings/spaboy    (ORP/pH target ranges — populates spaboy_orp_*/ph_*)
  arctic/spa/{spa_id}/config/spa         (capability manifest — populates get_config())
  arctic/spa/{spa_id}/information/spa    (model/firmware info — populates get_info())
  arctic/spa/{spa_id}/information/network (Wi-Fi info — raw log only; Phase 2 will parse)
  arctic/spa/{spa_id}/settings/peak      (peak-pricing schedule — raw log only; Phase 2 will parse)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

import aiohttp

from homeassistant.helpers.importlib import async_import_module
from .api import ArcticSpaApiError, ArcticSpaAuthError, ArcticSpaClientBase
from .spa_data import (
    normalise_mqtt_config_spa,
    normalise_mqtt_errors,
    normalise_mqtt_filters,
    normalise_mqtt_information_spa,
    normalise_mqtt_network_info,
    normalise_mqtt_rfid,
    normalise_mqtt_settings_peak,
    normalise_mqtt_settings_spa,
    normalise_mqtt_settings_spaboy,
    normalise_mqtt_spa,
    normalise_mqtt_spaboy,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_URL = "https://myarcticspa.com/api/auth"
_TOKEN_URL = "https://api.myarcticspa.com/access_token"
# OAuth client credential from the official My Arctic Spa mobile app.
# This is a fixed API registration value embedded in the APK, not a user password.
_MQTT_API_CLIENT_SECRET = "@4EUu^Y:U+FGtt2P"
_BROKER_TCP = ("broker.myarcticspa.com", 1884)
_BROKER_WSS = ("a2t84nz00o45m2-ats.iot.ca-central-1.amazonaws.com", 443)
_AUTHORIZER = "MyArcticSpaCustomAuthorizer_Prod"

# Reconnect delay sequence (seconds): 5s, 30s, 5 min, 10 min, 30 min, 1 hour
_RECONNECT_DELAYS = [5, 30, 300, 600, 1800, 3600]
_MAX_RECONNECT_ATTEMPTS = 12

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)
_AUTH_MIN_INTERVAL = 60.0  # minimum seconds between authentication attempts


class ArcticSpaMqttClient(ArcticSpaClientBase):
    """Cloud MQTT client for Arctic Spa.

    Maintains a persistent paho-mqtt connection and calls on_update whenever
    new spa data arrives.  Commands are delegated to an optional REST client.
    """

    def __init__(
        self,
        hass: Any,  # homeassistant.core.HomeAssistant
        username: str,
        password: str,
    ) -> None:
        self._hass = hass
        self._username = username
        # Plaintext kept for re-authentication (new salt is issued each time,
        # so _pw_hash cannot be reused across re-auth calls).
        self._password = password
        self._pw_hash: str | None = None

        self._spa_id: str | None = None
        self._user_id: str | None = None
        self._spas: list[dict] = []
        self._jwt: str | None = None
        self._jwt_exp: float = 0.0

        self._state: dict[str, Any] = {}
        self._state_lock = threading.Lock()
        self._on_update: Callable[[dict[str, Any]], None] | None = None

        # Capability and device-info data — populated when config/spa and
        # information/spa messages arrive (typically on first connect).
        self._config: dict[str, Any] = {}
        self._info: dict[str, Any] = {}

        self._mqtt_client: Any = None  # paho.mqtt.client.Client
        self._use_aws: bool = False
        self._install_id: str = str(uuid.uuid4())

        # Heartbeat + server-status liveness tracking.
        # telemetry/heartbeat carries {server, instance, online, when}.
        # After receiving a heartbeat we subscribe to server/{server_id}/status
        # (a retained message the broker maintains) whose {instance, online}
        # flags together confirm whether the spa is actually live.
        self._heartbeat_server: str | None = None   # broker-assigned server ID
        self._heartbeat_instance: str | None = None # UUID that must match server status
        self._server_status_topic: str | None = None  # subscribed server topic

        self._reconnect_attempt: int = 0
        self._reconnect_handle: asyncio.TimerHandle | None = None
        self._stopping: bool = False
        # Set while _reconnect_async is running so that the _on_mqtt_disconnect
        # callback triggered by our own disconnect() call does not schedule a
        # second concurrent reconnect on top of the one already in progress.
        self._reconnecting: bool = False

        # Minimum wall-clock time between _authenticate() calls to avoid
        # hammering the auth server during reconnect loops.
        self._last_auth_ts: float = 0.0

        # Set when the first config/spa message is received — lets __init__.py
        # wait for the capability manifest before resolving SpaCapabilities.
        self._config_ready: asyncio.Event = asyncio.Event()
        # Set when the first telemetry/filters message is received — lets __init__.py
        # wait for per-filter data before creating filter sensor entities.
        self._filters_ready: asyncio.Event = asyncio.Event()

        # Monotonic timestamp of the last received telemetry/spa message.
        # Used by the staleness check to set connected=False when the spa
        # stops reporting (e.g. cloud outage, spa Wi-Fi drop).
        self._last_telemetry_ts: float = 0.0
        # Monotonic timestamp of the last received MQTT message on any topic.
        # Used to distinguish a truly dead connection (nothing arriving) from a
        # live connection where the spa has simply gone quiet on telemetry/spa.
        # If any message is still flowing (heartbeat, settings, etc.) we leave
        # the connection alone even if telemetry/spa has been silent.
        self._last_any_msg_ts: float = 0.0
        self._staleness_task: asyncio.Task | None = None

    # ── Public properties ────────────────────────────────────────────────────

    @property
    def commands_available(self) -> bool:
        """True once MQTT is connected and the spa_id is known."""
        return self._mqtt_client is not None and self._spa_id is not None

    @property
    def spa_id(self) -> str | None:
        """Spa UUID discovered during authentication."""
        return self._spa_id

    # ── ArcticSpaClientBase interface ────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        """Return the most recent cached canonical state dict.

        On first call (before any MQTT message has arrived) returns an empty
        dict; the coordinator should wait for the on_update callback.
        """
        with self._state_lock:
            return self._state.copy()

    def get_config(self) -> dict[str, Any]:
        """Return the last received config/spa capability payload (thread-safe copy).

        Empty dict if config/spa has not yet been received.
        """
        with self._state_lock:
            return self._config.copy()

    def get_info(self) -> dict[str, Any]:
        """Return the last received information/spa device-info payload (thread-safe copy).

        Empty dict if information/spa has not yet been received.
        """
        with self._state_lock:
            return self._info.copy()

    def _publish_settings(self, fields: dict[str, Any]) -> None:
        """Publish a spaSettings envelope to the spa's controls topic.

        Topic: arctic/spa/{spa_id}/controls/spa  QoS 0
        Envelope: {"spaSettings": {"source": {"name": "HA"}, <field>: <value>, ...}}

        Used for filter schedule (filtrationFrequency, filtrationDuration) and
        filter suspension (filterSuspension) — these use the spaSettings wrapper
        rather than spaCommand.
        """
        if self._mqtt_client is None or self._spa_id is None:
            raise ArcticSpaApiError("MQTT not connected — cannot send settings")
        payload = json.dumps(
            {"spaSettings": {"source": {"name": "HA"}, **fields}}
        )
        self._mqtt_client.publish(
            f"arctic/spa/{self._spa_id}/controls/spa",
            payload,
            qos=0,
        )

    def _publish_command(self, fields: dict[str, int]) -> None:
        """Publish a spaCommand envelope to the spa's controls topic.

        Topic: arctic/spa/{spa_id}/controls/spa  QoS 0
        Envelope: {"spaCommand": {"source": {"name": "HA"}, <field>: <int>, ...}}

        paho.publish() is thread-safe; called synchronously from async methods.
        Raises ArcticSpaApiError if not yet connected.
        """
        if self._mqtt_client is None or self._spa_id is None:
            raise ArcticSpaApiError("MQTT not connected — cannot send command")
        payload = json.dumps(
            {"spaCommand": {"source": {"name": "HA"}, **fields}}
        )
        self._mqtt_client.publish(
            f"arctic/spa/{self._spa_id}/controls/spa",
            payload,
            qos=0,
        )

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        """Set the target temperature (°F)."""
        self._publish_command({"setpoint": setpoint_f})
        return {}

    async def set_lights(self, on: bool) -> dict[str, Any]:
        """Turn lights on or off."""
        self._publish_command({"lights": 1 if on else 0})
        return {}

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        """Set pump state.  pump='1'–'5', state='off'/'low'/'high'."""
        _state_map = {"off": 0, "low": 1, "high": 2}
        self._publish_command({f"pump{pump}": _state_map[state]})
        return {}

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        """Turn blower on or off."""
        self._publish_command({f"blower{blower}": 1 if on else 0})
        return {}

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        """Control the filter.

        state       → spaCommand.filter (1/0)
        frequency   → spaSettings.filtrationFrequency (int cycles/day)
        duration    → spaSettings.filtrationDuration (int hours)
        suspension  → spaSettings.filterSuspension (bool)
        """
        if state is not None:
            self._publish_command({"filter": 1 if state == "on" else 0})
        settings: dict[str, Any] = {}
        if frequency is not None:
            settings["filtrationFrequency"] = frequency
        if duration is not None:
            settings["filtrationDuration"] = duration
        if suspension is not None:
            settings["filterSuspension"] = suspension
        if settings:
            self._publish_settings(settings)
        return {}

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        """Toggle Easy Mode (all jets on)."""
        self._publish_command({"allOn": 1 if on else 0})
        return {}

    async def activate_boost(self) -> dict[str, Any]:
        """Activate filtration boost mode."""
        self._publish_command({"boost": 1})
        return {}

    def _publish_spaboy_settings(self, fields: dict[str, Any]) -> None:
        """Publish a spaboySettings envelope to the spa's controls topic.

        Topic: arctic/spa/{spa_id}/controls/spa  QoS 0
        Envelope: {"spaboySettings": {"source": {"name": "HA"}, <field>: <value>, ...}}

        Used for SpaBoy ORP/pH target adjustments — separate wrapper from spaCommand/spaSettings.
        """
        if self._mqtt_client is None or self._spa_id is None:
            raise ArcticSpaApiError("MQTT not connected — cannot send SpaBoy settings")
        payload = json.dumps(
            {"spaboySettings": {"source": {"name": "HA"}, **fields}}
        )
        self._mqtt_client.publish(
            f"arctic/spa/{self._spa_id}/controls/spa",
            payload,
            qos=0,
        )

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        """Activate SpaBoy chlorine boost.

        Uses the same spaCommand.boost field as filtration boost — the spa's
        firmware routes this to the SpaBoy controller when SpaBoy hardware is present.
        """
        self._publish_command({"boost": 1})
        return {}

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        """Set the SpaBoy ORP (chlorine) target range.

        Fields: orpLow / orpHigh (int mV) in the spaboySettings envelope.
        Presets confirmed from APK: Low=545/555, Mid=645/655, High=745/755.
        """
        self._publish_spaboy_settings({"orpLow": orp_low, "orpHigh": orp_high})
        return {}

    async def set_sds(self, on: bool) -> dict[str, Any]:
        """Toggle SDS."""
        self._publish_command({"sds": 1 if on else 0})
        return {}

    async def set_yess(self, on: bool) -> dict[str, Any]:
        """Toggle YESS."""
        self._publish_command({"yess": 1 if on else 0})
        return {}

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        """Toggle fogger."""
        self._publish_command({"fogger": 1 if on else 0})
        return {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self, on_update: Callable[[dict[str, Any]], None]) -> None:
        """Authenticate, connect to broker, and subscribe to telemetry topics.

        on_update is called (on the HA event loop) each time a telemetry
        message arrives.  This method returns as soon as the broker connection
        is initiated; it does not block until the first message.
        """
        self._on_update = on_update
        self._stopping = False
        await self._authenticate()
        await self._connect_mqtt()
        self._staleness_task = asyncio.ensure_future(self._staleness_check_loop())

    async def wait_for_config(self, timeout: float = 5.0) -> bool:
        """Wait until config/spa has been received, or timeout expires.

        Returns True if config arrived within the timeout, False otherwise.
        Called by __init__.py before resolving SpaCapabilities so the capability
        manifest is available rather than falling back to defaults.
        """
        try:
            await asyncio.wait_for(self._config_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "MQTT: config/spa not received within %.0fs — capabilities may use defaults", timeout
            )
            return False

    async def wait_for_filters(self, timeout: float = 3.0) -> bool:
        """Wait until telemetry/filters has been received, or timeout expires.

        Returns True if filter data arrived within the timeout, False otherwise.
        Non-fatal — filter sensor entities will simply not be created if absent.
        """
        try:
            await asyncio.wait_for(self._filters_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "MQTT: telemetry/filters not received within %.0fs — filter sensors skipped", timeout
            )
            return False

    async def _staleness_check_loop(self) -> None:
        """Background task: detect a truly silent broker and force a reconnect.

        Runs every 60 s on the HA event loop.  We distinguish two cases:

        1. telemetry/spa is quiet but other messages are still arriving
           (heartbeats, retained settings, etc.) — the MQTT connection is
           healthy; the spa is simply not publishing telemetry continuously.
           We log a debug note and leave the connection alone.

        2. No MQTT message of any kind has arrived for _STALE_THRESHOLD
           seconds — the TCP socket has gone zombie (keepalive failed silently,
           or the broker is down).  Only in this case do we push
           connected=False and tear down so _schedule_reconnect can recover.
        """
        _STALE_THRESHOLD = 300.0  # 5 minutes with no messages at all
        while not self._stopping:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return
            if self._stopping:
                continue
            # Nothing received yet — nothing to check.
            if self._last_telemetry_ts == 0.0 and self._last_any_msg_ts == 0.0:
                continue
            telemetry_age = (
                time.monotonic() - self._last_telemetry_ts
                if self._last_telemetry_ts > 0.0
                else float("inf")
            )
            if telemetry_age <= _STALE_THRESHOLD:
                continue

            # telemetry/spa has gone quiet — check whether ANY message has
            # arrived recently before deciding to reconnect.
            any_msg_age = (
                time.monotonic() - self._last_any_msg_ts
                if self._last_any_msg_ts > 0.0
                else telemetry_age
            )
            if any_msg_age <= _STALE_THRESHOLD:
                # Connection is alive — spa just isn't publishing telemetry/spa
                # continuously.  Don't tear down; data will update next time the
                # spa publishes (e.g. after a setting change or the next cycle).
                _LOGGER.debug(
                    "MQTT: telemetry/spa silent for %.0fs but broker still active "
                    "(last message %.0fs ago) — leaving connection alone",
                    telemetry_age,
                    any_msg_age,
                )
                if self._on_update is not None:
                    self._on_update(snapshot)
                # Reset timestamp so we don't fire again every 60 s while reconnecting.
                self._last_telemetry_ts = 0.0
                # Skip if a reconnect is already pending or in progress.
                if self._reconnect_handle is not None or self._reconnecting:
                    _LOGGER.debug("MQTT: reconnect already pending — skipping staleness teardown")
                    continue
                # Tear down paho — set _reconnecting first so the resulting
                # _on_mqtt_disconnect callback doesn't queue a duplicate reconnect.
                self._reconnecting = True
                if self._mqtt_client is not None:
                    try:
                        self._mqtt_client.disconnect()
                        await asyncio.sleep(0.5)  # allow DISCONNECT packet to flush
                        self._mqtt_client.loop_stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._mqtt_client = None
                self._reconnecting = False
                self._schedule_reconnect()

            # Nothing at all has arrived for _STALE_THRESHOLD seconds — treat
            # as a dead connection and reconnect.
            with self._state_lock:
                self._state["connected"] = False
                snapshot = self._state.copy()
            _LOGGER.warning(
                "MQTT: no messages for %.0fs — connection appears dead, forcing reconnect",
                any_msg_age,
            )
            if self._on_update is not None:
                self._on_update(snapshot)
            # Reset timestamps so we don't fire again every 60 s while reconnecting.
            self._last_telemetry_ts = 0.0
            self._last_any_msg_ts = 0.0
            # Tear down paho — set _reconnecting first so the resulting
            # _on_mqtt_disconnect callback doesn't queue a duplicate reconnect.
            self._reconnecting = True
            if self._mqtt_client is not None:
                try:
                    self._mqtt_client.disconnect()
                    self._mqtt_client.loop_stop()
                except Exception:  # noqa: BLE001
                    pass
                self._mqtt_client = None
            self._reconnecting = False
            self._schedule_reconnect()

    async def stop(self) -> None:
        """Disconnect the MQTT client and cancel any pending reconnect."""
        self._stopping = True
        if self._staleness_task is not None:
            self._staleness_task.cancel()
            self._staleness_task = None
        if self._reconnect_handle is not None:
            self._reconnect_handle.cancel()
            self._reconnect_handle = None
        if self._mqtt_client is not None:
            try:
                self._mqtt_client.disconnect()
                # Brief pause so paho can send the MQTT DISCONNECT packet
                # before the network loop thread is killed.
                await asyncio.sleep(0.5)
                self._mqtt_client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            self._mqtt_client = None

    # ── Internal: authentication ─────────────────────────────────────────────

    async def _authenticate(self) -> None:
        """Run the two-round auth flow and fetch a JWT.

        After this method returns:
        - self._pw_hash is set (stable per plaintext password)
        - self._spa_id is set
        - self._jwt and self._jwt_exp are set
        - self.__password is cleared
        """
        now = time.monotonic()
        elapsed = now - self._last_auth_ts
        if elapsed < _AUTH_MIN_INTERVAL:
            wait = _AUTH_MIN_INTERVAL - elapsed
            _LOGGER.debug("MQTT: rate-limiting auth — waiting %.0fs", wait)
            await asyncio.sleep(wait)
        self._last_auth_ts = time.monotonic()

        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            # Round 1 — obtain salt
            salt = await self._fetch_salt(session)

            # Compute hash from salt + plaintext password
            self._pw_hash = _hash_password(self._password, salt)

            # Round 2 — authenticate and retrieve spas list
            self._user_id, self._spas = await self._fetch_user(session)

            # Discover spa ID
            if not self._spa_id:
                self._spa_id = _extract_spa_id(self._spas)
                if not self._spa_id:
                    raise ArcticSpaAuthError(
                        "Could not determine spa ID from auth response"
                    )
                _LOGGER.debug("MQTT: discovered spa_id=%s", self._spa_id)

            # Fetch JWT
            first_spa = self._spas[0] if self._spas else {}
            client_id = f"mqtt-mobile_{self._install_id}"
            self._jwt = await self._fetch_token(session, client_id, first_spa)

        # Determine broker type from JWT audience
        audience = _decode_jwt_audience(self._jwt)
        _LOGGER.debug("MQTT: JWT audience=%s", audience)
        self._use_aws = any("aws-iot" in a for a in audience)

        # Cache expiry (seconds since epoch)
        self._jwt_exp = _decode_jwt_exp(self._jwt)

    async def _fetch_salt(self, session: aiohttp.ClientSession) -> str:
        """Round 1: POST /api/auth with null hash → returns Salt."""
        payload = {
            "username": self._username,
            "hash": None,
            "AllowNoSpaLogin": True,
        }
        async with session.post(
            _AUTH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status in (401, 403):
                raise ArcticSpaAuthError("Authentication rejected (round 1)")
            if not resp.ok:
                text = await resp.text()
                raise ArcticSpaApiError(
                    f"Auth round-1 failed ({resp.status}): {text[:200]}"
                )
            data = await resp.json()

        salt = data.get("Salt")
        if not salt:
            raise ArcticSpaAuthError(
                f"No Salt in auth round-1 response: {list(data.keys())}"
            )
        return salt

    async def _fetch_user(
        self, session: aiohttp.ClientSession
    ) -> tuple[str, list[dict]]:
        """Round 2: POST /api/auth with hashed password → UserId, Spas."""
        payload = {
            "username": self._username,
            "hash": self._pw_hash,
            "AllowNoSpaLogin": True,
        }
        async with session.post(
            _AUTH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status in (401, 403):
                raise ArcticSpaAuthError("Authentication rejected (round 2)")
            if not resp.ok:
                text = await resp.text()
                raise ArcticSpaApiError(
                    f"Auth round-2 failed ({resp.status}): {text[:200]}"
                )
            data = await resp.json()

        user_id: str = data.get("UserId", "")
        spas: list[dict] = data.get("Spas") or []
        return user_id, spas

    async def _fetch_token(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        spa: dict,
    ) -> str:
        """POST /access_token → JWT string."""
        spa_id = spa.get("Id", "")
        if spa_id:
            compound_username = f"{self._username}|{spa_id.lower()}"
        elif self._user_id:
            compound_username = f"{self._username}|{self._user_id.lower()}"
        else:
            raise ArcticSpaAuthError(
                "Cannot build token request: no spa ID or user ID available"
            )

        payload = {
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": _MQTT_API_CLIENT_SECRET,
            "username": compound_username,
            "password": self._pw_hash,
            "spa": spa,
        }
        async with session.post(
            _TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status in (401, 403):
                raise ArcticSpaAuthError("Token request rejected")
            if not resp.ok:
                text = await resp.text()
                raise ArcticSpaApiError(
                    f"Token request failed ({resp.status}): {text[:200]}"
                )
            data = await resp.json()

        token = data.get("access_token")
        if not token:
            raise ArcticSpaAuthError("No access_token in token response")
        _LOGGER.debug("MQTT: obtained JWT %s...", token[:8])
        return token

    async def _refresh_token_if_needed(self) -> None:
        """Re-authenticate if the JWT expires within 5 minutes."""
        if time.time() >= self._jwt_exp - 300:
            _LOGGER.debug("MQTT: JWT near expiry — re-authenticating")
            await self._authenticate()

    # ── Internal: MQTT connection ────────────────────────────────────────────

    async def _connect_mqtt(self) -> None:
        """Build paho client, configure broker path, and call connect()."""

        # Always re-authenticate before (re)connecting so the JWT is fresh.
        await self._refresh_token_if_needed()

        assert self._jwt is not None
        assert self._spa_id is not None

        topics = [
            f"arctic/spa/{self._spa_id}/telemetry/spa",
            f"arctic/spa/{self._spa_id}/telemetry/filters",
            f"arctic/spa/{self._spa_id}/telemetry/errors",
            f"arctic/spa/{self._spa_id}/telemetry/spaboy",
            f"arctic/spa/{self._spa_id}/telemetry/heartbeat",
            f"arctic/spa/{self._spa_id}/telemetry/update",
            f"arctic/spa/{self._spa_id}/telemetry/rfid",
            f"arctic/spa/{self._spa_id}/settings/spa",
            f"arctic/spa/{self._spa_id}/settings/spaboy",
            f"arctic/spa/{self._spa_id}/config/spa",
            f"arctic/spa/{self._spa_id}/information/spa",
            f"arctic/spa/{self._spa_id}/information/network",
            f"arctic/spa/{self._spa_id}/settings/peak",
        ]

        mqtt = await async_import_module(self._hass, "paho.mqtt.client")

        transport = "websockets" if self._use_aws else "tcp"
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha-arctic-spa_{self._install_id[:8]}",
            transport=transport,
            userdata={"topics": topics},
        )
        client.on_connect = self._on_mqtt_connect
        client.on_message = self._on_mqtt_message
        client.on_disconnect = self._on_mqtt_disconnect

        if self._use_aws:
            broker_host, broker_port = _BROKER_WSS
            mqtt_username = (
                f"{self._jwt}?x-amz-customauthorizer-name={_AUTHORIZER}"
            )
            client.tls_set()
            client.ws_set_options(path="/mqtt")
            _LOGGER.debug(
                "MQTT: connecting via AWS IoT WebSocket to %s:%d",
                broker_host,
                broker_port,
            )
        else:
            broker_host, broker_port = _BROKER_TCP
            mqtt_username = self._jwt
            _LOGGER.debug(
                "MQTT: connecting via plain TCP to %s:%d",
                broker_host,
                broker_port,
            )

        client.username_pw_set(mqtt_username, "anything")

        # Store before connect so callbacks can reference it
        self._mqtt_client = client

        # connect() and loop_start() are synchronous; run in executor to
        # avoid blocking the event loop.
        loop = self._hass.loop
        await loop.run_in_executor(
            None,
            lambda: (client.connect(broker_host, broker_port, keepalive=300), client.loop_start()),
        )

    # ── Internal: paho callbacks (background thread) ─────────────────────────

    def _on_mqtt_connect(
        self, client: Any, userdata: dict, connect_flags: Any, reason_code: Any, properties: Any
    ) -> None:
        """paho on_connect callback — runs on paho background thread."""
        if not reason_code.is_failure:
            _LOGGER.info(
                "MQTT: broker connection established — waiting for spa telemetry to confirm spa is online"
            )
            for topic in userdata.get("topics", []):
                client.subscribe(topic)
                _LOGGER.debug("MQTT: subscribed to %s", topic)
            self._reconnect_attempt = 0
        else:
            _LOGGER.warning("MQTT: broker refused connection — %s", reason_code)

    def _on_mqtt_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """paho on_message callback — runs on paho background thread.

        Normalises the payload and merges it into self._state, then schedules
        on_update on the HA event loop via call_soon_threadsafe.
        """
        # Track receipt time for any message — used by the staleness check to
        # distinguish a truly silent broker from a spa that just isn't publishing
        # telemetry/spa continuously.
        self._last_any_msg_ts = time.monotonic()

        # Use last two path segments to distinguish e.g. telemetry/spa vs settings/spa.
        parts = msg.topic.split("/")
        topic_key = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        try:
            payload: dict[str, Any] = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "MQTT: could not parse message on %s: %r", msg.topic, msg.payload[:100]
            )
            return

        try:
            with self._state_lock:
                if topic_key == "telemetry/spa":
                    new_data = normalise_mqtt_spa(payload)
                    self._state.update(new_data)
                    self._last_telemetry_ts = time.monotonic()
                elif topic_key == "telemetry/filters":
                    normalise_mqtt_filters(payload, self._state)
                    self._hass.loop.call_soon_threadsafe(self._filters_ready.set)
                elif topic_key == "telemetry/errors":
                    normalise_mqtt_errors(payload, self._state)
                elif topic_key == "telemetry/spaboy":
                    normalise_mqtt_spaboy(payload, self._state)
                elif topic_key == "telemetry/heartbeat":
                    # Heartbeat: {instance, server, online, when}
                    # Tells us which server the spa is routed through.
                    # We use this to subscribe to server/{server_id}/status
                    # (retained message that confirms the spa is actually live).
                    new_server = payload.get("server")
                    new_instance = str(payload.get("instance", ""))
                    if new_server and new_server != self._heartbeat_server:
                        # Server changed (or first heartbeat) — subscribe to new server topic
                        old_server_topic = self._server_status_topic
                        new_server_topic = f"server/{new_server}/status"
                        self._heartbeat_server = new_server
                        self._server_status_topic = new_server_topic
                        # Unsubscribe from old server topic and subscribe to new one
                        # (must happen outside the lock on the paho thread — schedule it)
                        if self._mqtt_client is not None:
                            if old_server_topic:
                                self._mqtt_client.unsubscribe(old_server_topic)
                            self._mqtt_client.subscribe(new_server_topic)
                            _LOGGER.debug("MQTT: subscribed to server status %s", new_server_topic)
                    self._heartbeat_instance = new_instance
                    _LOGGER.debug(
                        "MQTT: heartbeat server=%s instance=%s",
                        new_server, new_instance,
                    )
                    return  # heartbeat alone does not push coordinator update
                elif self._server_status_topic and msg.topic == self._server_status_topic:
                    # server/{server_id}/status: {instance, online}
                    # Retained message; broker updates it when spa connects/disconnects.
                    status_online = bool(payload.get("online", False))
                    status_instance = str(payload.get("instance", ""))
                    # Spa is live only when server confirms online AND
                    # instance UUID matches the last heartbeat (proves freshness).
                    instance_match = (
                        status_instance == self._heartbeat_instance
                        if self._heartbeat_instance
                        else True  # no heartbeat yet — trust server online flag
                    )
                    connected = status_online and instance_match
                    prev_connected = self._state.get("connected")
                    self._state["connected"] = connected
                    if connected != prev_connected:
                        if connected:
                            _LOGGER.info("MQTT: spa is online (mothership confirmed spa↔cloud connection)")
                        else:
                            _LOGGER.warning(
                                "MQTT: spa is offline — mothership reports spa↔cloud disconnected "
                                "(online=%s instance_match=%s)",
                                status_online, instance_match,
                            )
                    _LOGGER.debug(
                        "MQTT: server status online=%s instance_match=%s → connected=%s",
                        status_online, instance_match, connected,
                    )
                elif topic_key == "settings/spa":
                    normalise_mqtt_settings_spa(payload, self._state)
                elif topic_key == "settings/spaboy":
                    normalise_mqtt_settings_spaboy(payload, self._state)
                elif topic_key == "config/spa":
                    self._config = normalise_mqtt_config_spa(payload)
                    _LOGGER.debug("MQTT: received spa capability config (%d fields)", len(self._config))
                    self._hass.loop.call_soon_threadsafe(self._config_ready.set)
                    return  # config does not trigger a state update
                elif topic_key == "information/spa":
                    self._info = normalise_mqtt_information_spa(payload)
                    _LOGGER.debug("MQTT: received spa information: %s", self._info)
                    return  # info does not trigger a state update
                elif topic_key == "telemetry/update":
                    _LOGGER.debug("MQTT: telemetry/update raw: %s", payload)
                    return  # informational only — no state update
                elif topic_key == "telemetry/rfid":
                    normalise_mqtt_rfid(payload, self._state)
                elif topic_key == "information/network":
                    normalise_mqtt_network_info(payload, self._state)
                elif topic_key == "settings/peak":
                    normalise_mqtt_settings_peak(payload, self._state)
                else:
                    _LOGGER.debug("MQTT: ignoring unknown topic %r", topic_key)
                    return
                snapshot = self._state.copy()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("MQTT: error normalising %s payload", topic_key)
            return

        if self._on_update is not None:
            self._hass.loop.call_soon_threadsafe(self._on_update, snapshot)

    def _on_mqtt_disconnect(
        self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any
    ) -> None:
        """paho on_disconnect callback — runs on paho background thread.

        Schedules a reconnect attempt on the HA event loop unless we are
        stopping intentionally or already mid-reconnect (in which case the
        reconnect logic itself will re-establish the connection).
        """
        if self._stopping:
            _LOGGER.debug("MQTT: broker disconnected (intentional stop)")
            return

        if self._reconnecting:
            # We called disconnect() ourselves inside _reconnect_async — ignore.
            _LOGGER.debug("MQTT: broker disconnected during reconnect teardown (expected)")
            return

        _LOGGER.warning(
            "MQTT: lost broker connection (reason: %s) — spa data will be unavailable until reconnected",
            reason_code,
        )
        # Immediately mark spa as unreachable so the Connected sensor reflects
        # reality without waiting for the 5-minute staleness check.
        with self._state_lock:
            self._state["connected"] = False
            snapshot = self._state.copy()
        if self._on_update is not None:
            self._hass.loop.call_soon_threadsafe(self._on_update, snapshot)
        self._hass.loop.call_soon_threadsafe(self._schedule_reconnect)

    # ── Internal: reconnect (HA event loop) ─────────────────────────────────

    def _schedule_reconnect(self) -> None:
        """Called on the HA event loop after an unexpected disconnect."""
        if self._stopping or self._reconnecting:
            return

        if self._reconnect_handle is not None:
            _LOGGER.debug("MQTT: reconnect already scheduled — ignoring duplicate request")
            return

        if self._reconnect_attempt >= _MAX_RECONNECT_ATTEMPTS:
            _LOGGER.error(
                "MQTT: %d consecutive reconnect failures — giving up. "
                "Reload the integration to retry.",
                self._reconnect_attempt,
            )
            return

        delay_idx = min(self._reconnect_attempt, len(_RECONNECT_DELAYS) - 1)
        delay = _RECONNECT_DELAYS[delay_idx]
        self._reconnect_attempt += 1
        _LOGGER.info(
            "MQTT: scheduling reconnect attempt %d/%d in %ds",
            self._reconnect_attempt,
            _MAX_RECONNECT_ATTEMPTS,
            delay,
        )
        loop = self._hass.loop
        self._reconnect_handle = loop.call_later(delay, self._do_reconnect)

    def _do_reconnect(self) -> None:
        """Fired by call_later — kick off async reconnect coroutine."""
        self._reconnect_handle = None
        if self._stopping:
            return
        _LOGGER.debug("MQTT: reconnect timer fired (attempt %d)", self._reconnect_attempt)
        self._hass.loop.create_task(self._reconnect_async())

    async def _reconnect_async(self) -> None:
        """Async reconnect: stop old client, re-auth, re-connect."""
        if self._stopping:
            return
        self._reconnecting = True
        try:
            # Stop old paho client if still alive — disconnect() first so the
            # MQTT DISCONNECT packet is sent before the loop thread is killed.
            # _reconnecting=True suppresses the resulting _on_mqtt_disconnect
            # callback so it does not queue a second concurrent reconnect.
            if self._mqtt_client is not None:
                _LOGGER.debug("MQTT: tearing down old paho client before reconnect")
                try:
                    self._mqtt_client.disconnect()
                    await asyncio.sleep(0.5)  # flush DISCONNECT packet
                    self._mqtt_client.loop_stop()
                except Exception:  # noqa: BLE001
                    pass
                self._mqtt_client = None
            # Reset heartbeat/server-status state so the new connection re-negotiates.
            # Also reset message timestamps so the staleness check starts fresh.
            self._heartbeat_server = None
            self._heartbeat_instance = None
            self._server_status_topic = None
            self._last_telemetry_ts = 0.0
            self._last_any_msg_ts = 0.0

            try:
                await self._connect_mqtt()
                _LOGGER.info(
                    "MQTT: broker reconnected successfully (was attempt %d)",
                    self._reconnect_attempt,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("MQTT: reconnect attempt %d failed: %s", self._reconnect_attempt, err)
                self._reconnecting = False  # allow _schedule_reconnect to proceed
                self._schedule_reconnect()
        finally:
            self._reconnecting = False

# ── Module-level helpers ─────────────────────────────────────────────────────


def _hash_password(password: str, salt_b64: str) -> str:
    """SHA-1(base64_decode(salt) + password.utf-16-le), base64-encoded."""
    salt = base64.b64decode(salt_b64)
    digest = hashlib.sha1(salt + password.encode("utf-16-le")).digest()  # noqa: S324
    return base64.b64encode(digest).decode("ascii")


def _decode_jwt_audience(token: str) -> list[str]:
    """Decode JWT payload segment (no signature verification) and return audience list."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return []
        payload_b64 = parts[1]
        # Add padding so base64 decoding does not fail
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        aud = payload.get("aud", [])
        return [aud] if isinstance(aud, str) else list(aud)
    except Exception:  # noqa: BLE001
        return []


def _decode_jwt_exp(token: str) -> float:
    """Return the `exp` claim from a JWT payload, or 0.0 on failure."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:  # noqa: BLE001
        return 0.0


def _extract_spa_id(spas: list[dict]) -> str | None:
    """Try common field names for spa ID in the spas list."""
    if not spas:
        return None
    for key in ("SpaId", "spaId", "spa_id", "id", "Id"):
        if key in spas[0]:
            return spas[0][key]
    return None
