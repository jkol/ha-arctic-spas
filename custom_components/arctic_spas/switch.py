"""Switch entities for Arctic Spa (on/off controls)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArcticSpaApiError, ArcticSpaClient
from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info

_LOGGER = logging.getLogger(__name__)

# filter_status values that mean the filter is actively running
_FILTER_ON_STATES = {"filtering", "purge", "boost", "sanitize", "resuming"}


@dataclass(frozen=True, kw_only=True)
class ArcticSpaSwitchDescription(SwitchEntityDescription):
    status_key: str
    turn_on: Callable[[ArcticSpaClient], Coroutine[Any, Any, Any]]
    turn_off: Callable[[ArcticSpaClient], Coroutine[Any, Any, Any]]
    optional: bool = False
    state_is_on: Callable[[Any], bool] | None = None


def _is_on(value: Any) -> bool:
    """Coerce status field value to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("on", "high", "low")
    return bool(value)


SWITCHES: tuple[ArcticSpaSwitchDescription, ...] = (
    ArcticSpaSwitchDescription(
        key="lights",
        name="Lights",
        status_key="lights",
        turn_on=lambda c: c.set_lights(True),
        turn_off=lambda c: c.set_lights(False),
    ),

    ArcticSpaSwitchDescription(
        key="filter",
        name="Filter",
        status_key="filter_status",
        turn_on=lambda c: c.set_filter(state="on"),
        turn_off=lambda c: c.set_filter(state="off"),
        state_is_on=lambda v: isinstance(v, str) and v.lower() in _FILTER_ON_STATES,
    ),
    ArcticSpaSwitchDescription(
        key="sds",
        name="SDS",
        status_key="sds",
        turn_on=lambda c: c.set_sds(True),
        turn_off=lambda c: c.set_sds(False),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="yess",
        name="YESS",
        status_key="yess",
        turn_on=lambda c: c.set_yess(True),
        turn_off=lambda c: c.set_yess(False),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="fogger",
        name="Fogger",
        status_key="fogger",
        turn_on=lambda c: c.set_fogger(True),
        turn_off=lambda c: c.set_fogger(False),
        optional=True,
        icon="mdi:weather-fog",
    ),
    ArcticSpaSwitchDescription(
        key="blower1",
        name="Blower 1",
        status_key="blower1",
        turn_on=lambda c: c.set_blower("1", True),
        turn_off=lambda c: c.set_blower("1", False),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="blower2",
        name="Blower 2",
        status_key="blower2",
        turn_on=lambda c: c.set_blower("2", True),
        turn_off=lambda c: c.set_blower("2", False),
        optional=True,
    ),
    # Pump 2–5: only support off/on (high). Pump 1 handled by select.py.
    ArcticSpaSwitchDescription(
        key="pump2",
        name="Pump 2",
        status_key="pump2",
        turn_on=lambda c: c.set_pump("2", "high"),
        turn_off=lambda c: c.set_pump("2", "off"),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="pump3",
        name="Pump 3",
        status_key="pump3",
        turn_on=lambda c: c.set_pump("3", "high"),
        turn_off=lambda c: c.set_pump("3", "off"),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="pump4",
        name="Pump 4",
        status_key="pump4",
        turn_on=lambda c: c.set_pump("4", "high"),
        turn_off=lambda c: c.set_pump("4", "off"),
        optional=True,
    ),
    ArcticSpaSwitchDescription(
        key="pump5",
        name="Pump 5",
        status_key="pump5",
        turn_on=lambda c: c.set_pump("5", "high"),
        turn_off=lambda c: c.set_pump("5", "off"),
        optional=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities = [
        ArcticSpaSwitch(coordinator, entry, desc)
        for desc in SWITCHES
        if not desc.optional or desc.status_key in data
    ]
    async_add_entities(entities)


class ArcticSpaSwitch(CoordinatorEntity[ArcticSpaCoordinator], SwitchEntity):
    """An on/off switch backed by the Arctic Spa API."""

    _attr_has_entity_name = True
    entity_description: ArcticSpaSwitchDescription

    def __init__(
        self,
        coordinator: ArcticSpaCoordinator,
        entry: ConfigEntry,
        description: ArcticSpaSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(self.entity_description.status_key)
        if value is None:
            return None
        if self.entity_description.state_is_on is not None:
            return self.entity_description.state_is_on(value)
        return _is_on(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.entity_description.turn_on(self.coordinator.client)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to turn on %s: %s", self.entity_description.name, err)
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.entity_description.turn_off(self.coordinator.client)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to turn off %s: %s", self.entity_description.name, err)
            return
        await self.coordinator.async_request_refresh()
