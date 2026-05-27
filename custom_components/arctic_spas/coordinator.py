"""DataUpdateCoordinator for Arctic Spa."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcticSpaApiError, ArcticSpaClientBase
from .const import DOMAIN
from .spa_data import SpaCapabilities

_LOGGER = logging.getLogger(__name__)


class ArcticSpaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates spa data updates for all entities.

    Both Direct and Cloud modes are push-based (update_interval=None).
    Callers push data via async_set_updated_data() in the on_update callback.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ArcticSpaClientBase,
        push_mode: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.client = client
        self.capabilities = SpaCapabilities()

    async def async_request_refresh_delayed(self, delay: float = 1.0) -> None:
        await asyncio.sleep(delay)
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_status()
        except ArcticSpaApiError as err:
            raise UpdateFailed(f"Error communicating with spa: {err}") from err
