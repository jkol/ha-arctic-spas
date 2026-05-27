"""Cloud client for Arctic Spa via dealer API + AWS IoT MQTT.

Authentication:
  1. POST dealer-api.myarcticspa.com/api/login → JWT
  2. Connect to AWS IoT Core MQTT broker using JWT via custom authorizer
  3. Subscribe to spa topics for real-time telemetry
  4. Publish commands to the spa's command topic

The MQTT payload format is identical to the local WebSocket protocol —
same key names, same topic structure — so all normalisation is shared.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import uuid
from typing import Any, Callable

import aiohttp
import aiomqtt

from .api import ArcticSpaApiError, ArcticSpaClientBase
from .spa_data import (
    normalise_native_error,
    normalise_native_live,
    normalise_native_sett,
)

_LOGGER = logging.getLogger(__name__)

_DEALER_API_BASE = "https://dealer-api.myarcticspa.com/api"
_MQTT_BROKER = "a2t84nz00o45m2-ats.iot.ca-central-1.amazonaws.com"
_MQTT_PORT = 443
_MQTT_AUTHORIZER = "DealerPortalCustomAuthorizer_PROD"
_MQTT_ENV = "prod"

_RECONNECT_DELAYS = (5, 10, 30, 60, 120)
_TOKEN_REFRESH_MARGIN = 300  # refresh 5 min before expiry


class ArcticSpaCloudAuthError(ArcticSpaApiError):
    """Dealer API credentials were rejected."""


class ArcticSpaCloudClient(ArcticSpaClientBase):
    """Cloud client using dealer API auth + AWS IoT MQTT.

    Authenticates via the dealer REST API, then connects to the AWS IoT
    MQTT broker for real-time telemetry and commands. The MQTT payload
    format is the same as the local WebSocket protocol.
    """

    def __init__(
        self,
        email: str,
        password: str,
        spa_id: str,
        dealership_id: str,
    ) -> None:
        self._email = email
        self._password = password
        self._spa_id = spa_id
        self._dealership_id = dealership_id

        self._token: str | None = None
        self._state: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        self._config: dict[str, Any] = {}
        self._on_update: Callable[[dict[str, Any]], None] | None = None
        self._mqtt_client: aiomqtt.Client | None = None
        self._running = False
        self._supervisor_task: asyncio.Task | None = None
        self._reconnect_attempt: int = 0
        self._config_ready = asyncio.Event()

    # ── Topic helpers ────────────────────────────────────────────────────────

    @property
    def _topic_base(self) -> str:
        return f"arcticspa/{self._dealership_id}/{self._spa_id}"

    @property
    def _command_topic(self) -> str:
        return f"{self._topic_base}/command"

    @property
    def commands_available(self) -> bool:
        return self._mqtt_client is not None

    def get_settings(self) -> dict[str, Any]:
        return self._settings.copy()

    def get_config(self) -> dict[str, Any]:
        return self._config.copy()

    # ── ArcticSpaClientBase interface ────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        return self._state.copy()

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        await self._publish_command({"setTSP": setpoint_f})
        return {}

    async def set_lights(self, on: bool) -> dict[str, Any]:
        await self._publish_command({"Linext": 1})
        return {}

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        await self._publish_command({f"P{pump}next": 1})
        return {}

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        key = f"Bl{blower}next" if blower == "1" else f"BL{blower}next"
        await self._publish_command({key: 1})
        return {}

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        if state is not None:
            await self._publish_command({"FLTRboost": 1})
        settings: dict[str, Any] = {}
        if frequency is not None:
            settings["setFF"] = frequency
        if duration is not None:
            settings["setFD"] = duration
        if suspension is not None:
            settings["setFS"] = suspension
        if settings:
            await self._publish_command(settings)
        return {}

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        await self._publish_command({"Linext": 1})
        _LOGGER.warning("Cloud: easymode toggle — using Linext as proxy")
        return {}

    async def activate_boost(self) -> dict[str, Any]:
        await self._publish_command({"FLTRboost": 1})
        return {}

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        await self._publish_command({"SBboost": 1})
        return {}

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        await self._publish_command({"SBORPhi": orp_high, "SBORPlo": orp_low})
        return {}

    async def set_sds(self, on: bool) -> dict[str, Any]:
        await self._publish_command({"SDSnext": 1})
        return {}

    async def set_yess(self, on: bool) -> dict[str, Any]:
        await self._publish_command({"YESSnext": 1})
        return {}

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        await self._publish_command({"Fgnext": 1})
        return {}

    # ── Auth ─────────────────────────────────────────────────────────────────

    async def _login(self) -> str:
        """Authenticate with the dealer API and return a JWT access token."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_DEALER_API_BASE}/login",
                json={"Email": self._email, "Password": self._password},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403, 422):
                    raise ArcticSpaCloudAuthError("Invalid credentials")
                if resp.status != 200:
                    raise ArcticSpaApiError(f"Login failed with status {resp.status}")
                data = await resp.json()

        token = data.get("access_token")
        if not token:
            raise ArcticSpaApiError("No access_token in login response")

        self._token = token
        _LOGGER.debug("Cloud: authenticated with dealer API")
        return token

    async def _refresh_token(self) -> str:
        """Refresh the JWT token."""
        if not self._token:
            return await self._login()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_DEALER_API_BASE}/refresh",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        token = data.get("access_token")
                        if token:
                            self._token = token
                            _LOGGER.debug("Cloud: token refreshed")
                            return token
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass

        _LOGGER.warning("Cloud: token refresh failed, falling back to full login")
        return await self._login()

    # ── MQTT ─────────────────────────────────────────────────────────────────

    async def _publish_command(self, payload: dict[str, Any]) -> None:
        if self._mqtt_client is None:
            raise ArcticSpaApiError("MQTT not connected — cannot send command")
        await self._mqtt_client.publish(
            self._command_topic,
            json.dumps(payload),
            qos=0,
        )

    async def start(self, on_update: Callable[[dict[str, Any]], None]) -> None:
        self._on_update = on_update
        self._running = True
        await self._login()
        await self._connect_mqtt()
        self._supervisor_task = asyncio.ensure_future(self._supervisor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
        self._mqtt_client = None

    async def wait_for_config(self, timeout: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._config_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning("Cloud: settings not received within %.0fs", timeout)
            return False

    async def _connect_mqtt(self) -> None:
        if not self._token:
            raise ArcticSpaApiError("No token — call _login() first")

        client_id = f"ha-arctic-spa-{uuid.uuid4()}"
        username = (
            f"?x-amz-customauthorizer-name={_MQTT_AUTHORIZER}"
            f"&mqtt_token={self._token}"
            f"&env={_MQTT_ENV}"
        )

        tls_context = ssl.create_default_context()

        self._mqtt_client = aiomqtt.Client(
            hostname=_MQTT_BROKER,
            port=_MQTT_PORT,
            identifier=client_id,
            username=username,
            password="",
            transport="websockets",
            tls_context=tls_context,
            keepalive=60,
            timeout=30,
        )

        await self._mqtt_client.__aenter__()

        base = self._topic_base
        topics = [
            f"{base}/live",
            f"{base}/sett",
            f"{base}/const",
            f"{base}/error",
            f"{base}/connection-status",
            f"$aws/events/presence/disconnected/{self._spa_id}",
        ]
        for topic in topics:
            await self._mqtt_client.subscribe(topic, qos=0)
            _LOGGER.debug("Cloud: subscribed to %s", topic)

        asyncio.ensure_future(self._reader_loop())
        _LOGGER.info("Cloud: MQTT connected to %s", _MQTT_BROKER)

    async def _disconnect_mqtt(self) -> None:
        if self._mqtt_client is not None:
            try:
                await self._mqtt_client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._mqtt_client = None

    async def _reader_loop(self) -> None:
        assert self._mqtt_client is not None
        try:
            async for message in self._mqtt_client.messages:
                topic_str = str(message.topic)
                payload_str = message.payload
                if isinstance(payload_str, bytes):
                    payload_str = payload_str.decode(errors="replace")
                self._dispatch(topic_str, payload_str)
        except aiomqtt.MqttError as err:
            _LOGGER.warning("Cloud: MQTT reader error: %s", err)

    def _dispatch(self, topic: str, raw: str) -> None:
        parts = topic.split("/")
        suffix = parts[-1] if parts else ""

        if suffix == "live":
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return
            new_data = normalise_native_live(data)
            self._state.update(new_data)
            if self._on_update:
                self._on_update(self._state.copy())

        elif suffix == "sett":
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return
            self._settings = data.copy()
            normalise_native_sett(data, self._state)
            if not self._config_ready.is_set():
                self._config_ready.set()
            if self._on_update:
                self._on_update(self._state.copy())

        elif suffix == "const":
            try:
                self._config = json.loads(raw)
            except (ValueError, TypeError):
                pass

        elif suffix == "error":
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return
            normalise_native_error(data, self._state)
            if self._on_update:
                self._on_update(self._state.copy())

        elif suffix == "connection-status":
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return
            if data.get("connection_status") == "connected":
                self._state["connected"] = True
                if self._on_update:
                    self._on_update(self._state.copy())

        elif topic.startswith("$aws/events/presence/disconnected/"):
            self._state["connected"] = False
            if self._on_update:
                self._on_update(self._state.copy())

    async def _supervisor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)
            if not self._running:
                break

            if self._mqtt_client is None:
                self._state["connected"] = False
                if self._on_update:
                    self._on_update(self._state.copy())

                delay_idx = min(self._reconnect_attempt, len(_RECONNECT_DELAYS) - 1)
                delay = _RECONNECT_DELAYS[delay_idx]
                self._reconnect_attempt += 1
                _LOGGER.warning(
                    "Cloud: MQTT disconnected — reconnecting in %ds (attempt %d)",
                    delay, self._reconnect_attempt,
                )
                await asyncio.sleep(delay)

                if not self._running:
                    break

                try:
                    await self._refresh_token()
                    await self._connect_mqtt()
                    self._reconnect_attempt = 0
                    _LOGGER.info("Cloud: MQTT reconnected")
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Cloud: reconnect failed: %s", err)


# ── Dealer API helpers (used by config flow) ─────────────────────────────────


async def async_dealer_login(email: str, password: str) -> str:
    """Authenticate with the dealer API and return a JWT access token."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_DEALER_API_BASE}/login",
            json={"Email": email, "Password": password},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (401, 403, 422):
                raise ArcticSpaCloudAuthError("Invalid credentials")
            if resp.status != 200:
                raise ArcticSpaApiError(f"Login failed with status {resp.status}")
            data = await resp.json()

    token = data.get("access_token")
    if not token:
        raise ArcticSpaApiError("No access_token in login response")
    return token


async def async_get_spas(token: str) -> list[dict[str, Any]]:
    """Fetch the list of spas associated with the authenticated user.

    Uses /api/user → get user ID → /api/user/{uid}/spas since /api/spas
    is restricted to dealer/admin roles.
    """
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {token}"}

        async with session.get(
            f"{_DEALER_API_BASE}/user",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise ArcticSpaApiError(f"Failed to fetch user: status {resp.status}")
            user_body = await resp.json()

        uid = user_body.get("data", {}).get("id")
        if not uid:
            raise ArcticSpaApiError("No user ID in /api/user response")

        async with session.get(
            f"{_DEALER_API_BASE}/user/{uid}/spas",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise ArcticSpaApiError(f"Failed to fetch spas: status {resp.status}")
            body = await resp.json()

    data = body.get("data", body)
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []
