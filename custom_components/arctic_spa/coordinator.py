"""DataUpdateCoordinator for Arctic Spa."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcticSpaApiError, ArcticSpaClient, ArcticSpaTemporaryError
from .const import DEFAULT_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ArcticSpaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /v2/spa/status and distributes data to all entities."""

    def __init__(self, hass: HomeAssistant, client: ArcticSpaClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_status()
        except ArcticSpaTemporaryError as err:
            # 503 — log and surface as UpdateFailed; HA will retry next cycle
            _LOGGER.debug("Transient error from Arctic Spa API: %s", err)
            raise UpdateFailed(f"Temporary error: {err}") from err
        except ArcticSpaApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
