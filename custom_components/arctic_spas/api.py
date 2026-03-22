"""Arctic Spa API client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)


class ArcticSpaApiError(Exception):
    """General API error."""


class ArcticSpaRateLimitError(ArcticSpaApiError):
    """Raised on HTTP 429."""


class ArcticSpaTemporaryError(ArcticSpaApiError):
    """Raised on HTTP 503 — safe to retry."""


class ArcticSpaAuthError(ArcticSpaApiError):
    """Raised on HTTP 401/403."""


class ArcticSpaClient:
    """Async client for the Arctic Spa REST API."""

    def __init__(self, api_key: str, session: aiohttp.ClientSession) -> None:
        self._api_key = api_key
        self._session = session
        self._headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE_URL}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, json=json, timeout=_TIMEOUT
            ) as resp:
                if resp.status == 429:
                    raise ArcticSpaRateLimitError("Rate limit exceeded")
                if resp.status == 503:
                    raise ArcticSpaTemporaryError("Service temporarily unavailable")
                if resp.status in (401, 403):
                    raise ArcticSpaAuthError(f"Authentication failed: {resp.status}")
                # 202 = command accepted but state already matches — treat as success
                if resp.status not in (200, 201, 202, 204):
                    text = await resp.text()
                    raise ArcticSpaApiError(
                        f"Unexpected status {resp.status}: {text[:200]}"
                    )
                if resp.status == 204 or resp.content_length == 0:
                    return {}
                try:
                    return await resp.json()
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise ArcticSpaApiError(f"Invalid JSON in response: {err}") from err
        except aiohttp.ClientError as err:
            raise ArcticSpaApiError(f"Connection error: {err}") from err

    async def get_status(self) -> dict[str, Any]:
        """GET /v2/spa/status — full spa state."""
        return await self._request("GET", "/v2/spa/status")

    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]:
        """PUT /v2/spa/temperature — set temperature setpoint."""
        return await self._request(
            "PUT", "/v2/spa/temperature", json={"setpointF": setpoint_f}
        )

    async def set_lights(self, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/lights — toggle lights."""
        return await self._request(
            "PUT", "/v2/spa/lights", json={"state": "on" if on else "off"}
        )

    async def set_pump(self, pump: str, state: str) -> dict[str, Any]:
        """PUT /v2/spa/pumps/{pump} — control pumps 1-5 or 'all'."""
        return await self._request(
            "PUT", f"/v2/spa/pumps/{pump}", json={"state": state}
        )

    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/blowers/{blower} — control blowers 1-2 or 'all'."""
        return await self._request(
            "PUT", f"/v2/spa/blowers/{blower}", json={"state": "on" if on else "off"}
        )

    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]:
        """PUT /v2/spa/filter — toggle filter and configure schedule."""
        body = {
            k: v
            for k, v in {
                "state": state,
                "frequency": frequency,
                "duration": duration,
                "suspension": suspension,
            }.items()
            if v is not None
        }
        return await self._request("PUT", "/v2/spa/filter", json=body)

    async def set_easymode(self, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/easymode — toggle easy mode."""
        return await self._request(
            "PUT", "/v2/spa/easymode", json={"state": "on" if on else "off"}
        )

    async def activate_boost(self) -> dict[str, Any]:
        """PUT /v2/spa/boost — activate boost mode (no body required)."""
        return await self._request("PUT", "/v2/spa/boost")

    async def set_sds(self, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/sds — toggle SDS (if available)."""
        return await self._request(
            "PUT", "/v2/spa/sds", json={"state": "on" if on else "off"}
        )

    async def set_yess(self, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/yess — toggle YESS (if available)."""
        return await self._request(
            "PUT", "/v2/spa/yess", json={"state": "on" if on else "off"}
        )

    async def set_fogger(self, on: bool) -> dict[str, Any]:
        """PUT /v2/spa/fogger — toggle fogger (if available)."""
        return await self._request(
            "PUT", "/v2/spa/fogger", json={"state": "on" if on else "off"}
        )
