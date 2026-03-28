"""DataUpdateCoordinator for Arctic Spa."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcticSpaApiError, ArcticSpaClientBase, ArcticSpaRateLimitError, ArcticSpaTemporaryError
from .const import DEFAULT_POLL_INTERVAL, DOMAIN
from .spa_data import SpaCapabilities

_LOGGER = logging.getLogger(__name__)


class ArcticSpaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates spa data updates for all entities.

    REST mode: polls /v2/spa/status every DEFAULT_POLL_INTERVAL seconds.
    MQTT/Local modes: push-based — update_interval=None; callers push data
    via async_set_updated_data() in the on_update callback.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ArcticSpaClientBase,
        push_mode: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None if push_mode else timedelta(seconds=DEFAULT_POLL_INTERVAL),
        )
        self.client = client
        self.capabilities = SpaCapabilities()

    async def async_request_refresh_delayed(self, delay: float = 1.0) -> None:
        """Schedule a coordinator refresh after a short delay.

        Call with  self.hass.async_create_task(coordinator.async_request_refresh_delayed())
        from within an async entity method.  This keeps the async_create_task call on
        the event loop and avoids the thread-safety warning raised by async_call_later
        callbacks in Python 3.14.
        """
        await asyncio.sleep(delay)
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_status()
        except ArcticSpaRateLimitError as err:
            _LOGGER.warning("Arctic Spa API rate limit hit: %s", err)
            raise UpdateFailed("Rate limit exceeded, will retry next cycle") from err
        except ArcticSpaTemporaryError as err:
            _LOGGER.debug("Transient error from Arctic Spa API: %s", err)
            raise UpdateFailed(f"Temporary error: {err}") from err
        except ArcticSpaApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
