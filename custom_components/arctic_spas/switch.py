"""Switch entities for Arctic Spa (on/off controls)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArcticSpaApiError, ArcticSpaClientBaseBase
from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info
from .spa_data import SpaCapabilities

_LOGGER = logging.getLogger(__name__)

# Module-level command helpers — used instead of lambdas so these are picklable.
def _lights_on(c: ArcticSpaClientBase) -> Any: return c.set_lights(True)
def _lights_off(c: ArcticSpaClientBase) -> Any: return c.set_lights(False)
def _filter_suspension_on(c: ArcticSpaClientBase) -> Any: return c.set_filter(suspension=True)
def _filter_suspension_off(c: ArcticSpaClientBase) -> Any: return c.set_filter(suspension=False)
def _sds_on(c: ArcticSpaClientBase) -> Any: return c.set_sds(True)
def _sds_off(c: ArcticSpaClientBase) -> Any: return c.set_sds(False)
def _yess_on(c: ArcticSpaClientBase) -> Any: return c.set_yess(True)
def _yess_off(c: ArcticSpaClientBase) -> Any: return c.set_yess(False)
def _fogger_on(c: ArcticSpaClientBase) -> Any: return c.set_fogger(True)
def _fogger_off(c: ArcticSpaClientBase) -> Any: return c.set_fogger(False)
def _blower1_on(c: ArcticSpaClientBase) -> Any: return c.set_blower("1", True)
def _blower1_off(c: ArcticSpaClientBase) -> Any: return c.set_blower("1", False)
def _blower2_on(c: ArcticSpaClientBase) -> Any: return c.set_blower("2", True)
def _blower2_off(c: ArcticSpaClientBase) -> Any: return c.set_blower("2", False)
def _pump2_on(c: ArcticSpaClientBase) -> Any: return c.set_pump("2", "high")
def _pump2_off(c: ArcticSpaClientBase) -> Any: return c.set_pump("2", "off")
def _pump3_on(c: ArcticSpaClientBase) -> Any: return c.set_pump("3", "high")
def _pump3_off(c: ArcticSpaClientBase) -> Any: return c.set_pump("3", "off")
def _pump4_on(c: ArcticSpaClientBase) -> Any: return c.set_pump("4", "high")
def _pump4_off(c: ArcticSpaClientBase) -> Any: return c.set_pump("4", "off")
def _pump5_on(c: ArcticSpaClientBase) -> Any: return c.set_pump("5", "high")
def _pump5_off(c: ArcticSpaClientBase) -> Any: return c.set_pump("5", "off")


@dataclass(frozen=True, kw_only=True)
class ArcticSpaSwitchDescription(SwitchEntityDescription):
    status_key: str
    turn_on: Callable[[ArcticSpaClientBase], Coroutine[Any, Any, Any]]
    turn_off: Callable[[ArcticSpaClientBase], Coroutine[Any, Any, Any]]
    capabilities_check: Callable[[SpaCapabilities], bool] | None = None
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
        turn_on=_lights_on,
        turn_off=_lights_off,
    ),
    ArcticSpaSwitchDescription(
        key="filter_suspension",
        name="Filter Suspension",
        status_key="filter_suspension",
        turn_on=_filter_suspension_on,
        turn_off=_filter_suspension_off,
        icon="mdi:thermometer-alert",
        capabilities_check=lambda caps: caps.has_filter_suspension,
    ),
    ArcticSpaSwitchDescription(
        key="sds",
        name="SDS",
        status_key="sds",
        turn_on=_sds_on,
        turn_off=_sds_off,
        capabilities_check=lambda caps: caps.sds,
    ),
    ArcticSpaSwitchDescription(
        key="yess",
        name="YESS",
        status_key="yess",
        turn_on=_yess_on,
        turn_off=_yess_off,
        capabilities_check=lambda caps: caps.yess,
    ),
    ArcticSpaSwitchDescription(
        key="fogger",
        name="Fogger",
        status_key="fogger",
        turn_on=_fogger_on,
        turn_off=_fogger_off,
        icon="mdi:weather-fog",
        capabilities_check=lambda caps: caps.fogger,
    ),
    ArcticSpaSwitchDescription(
        key="blower1",
        name="Blower 1",
        status_key="blower1",
        turn_on=_blower1_on,
        turn_off=_blower1_off,
        capabilities_check=lambda caps: caps.blower1,
    ),
    ArcticSpaSwitchDescription(
        key="blower2",
        name="Blower 2",
        status_key="blower2",
        turn_on=_blower2_on,
        turn_off=_blower2_off,
        capabilities_check=lambda caps: caps.blower2,
    ),
    # Pump 2–5: only support off/on (high). Pump 1 handled by select.py.
    ArcticSpaSwitchDescription(
        key="pump2",
        name="Pump 2",
        status_key="pump2",
        turn_on=_pump2_on,
        turn_off=_pump2_off,
        capabilities_check=lambda caps: caps.pump2,
    ),
    ArcticSpaSwitchDescription(
        key="pump3",
        name="Pump 3",
        status_key="pump3",
        turn_on=_pump3_on,
        turn_off=_pump3_off,
        capabilities_check=lambda caps: caps.pump3,
    ),
    ArcticSpaSwitchDescription(
        key="pump4",
        name="Pump 4",
        status_key="pump4",
        turn_on=_pump4_on,
        turn_off=_pump4_off,
        capabilities_check=lambda caps: caps.pump4,
    ),
    ArcticSpaSwitchDescription(
        key="pump5",
        name="Pump 5",
        status_key="pump5",
        turn_on=_pump5_on,
        turn_off=_pump5_off,
        capabilities_check=lambda caps: caps.pump5,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    caps = coordinator.capabilities

    entities = [
        ArcticSpaSwitch(coordinator, entry, desc)
        for desc in SWITCHES
        if desc.capabilities_check is None or desc.capabilities_check(caps)
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
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get(self.entity_description.status_key)
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
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.entity_description.turn_off(self.coordinator.client)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to turn off %s: %s", self.entity_description.name, err)
            return
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())
