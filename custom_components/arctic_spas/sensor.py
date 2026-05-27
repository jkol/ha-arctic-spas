"""Sensor entities for Arctic Spa (read-only status values)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info
from .spa_data import SpaCapabilities


@dataclass(frozen=True, kw_only=True)
class ArcticSpaSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a capabilities gate."""

    # None = always create; callable returns True when the entity should be created.
    capabilities_check: Callable[[SpaCapabilities], bool] | None = None


SENSORS: tuple[ArcticSpaSensorDescription, ...] = (
    ArcticSpaSensorDescription(
        key="temperatureF",
        name="Water Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    ArcticSpaSensorDescription(
        key="setpointF",
        name="Temperature Setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    ArcticSpaSensorDescription(
        key="ph",
        name="Spa pH Level",
        device_class=SensorDeviceClass.PH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=None,
        suggested_display_precision=2,
        icon="mdi:ph",
        capabilities_check=lambda caps: caps.spaboy,
    ),
    ArcticSpaSensorDescription(
        key="ph_status",
        name="pH Status",
        device_class=SensorDeviceClass.ENUM,
        options=["low", "caution_low", "ok", "caution_high", "high"],
        capabilities_check=lambda caps: caps.spaboy,
    ),
    ArcticSpaSensorDescription(
        key="orp",
        name="Spa Chlorine Level",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="mV",
        icon="mdi:lightning-bolt",
        capabilities_check=lambda caps: caps.spaboy,
    ),
    ArcticSpaSensorDescription(
        key="orp_status",
        name="ORP Status",
        device_class=SensorDeviceClass.ENUM,
        options=["low", "caution_low", "ok", "caution_high", "high"],
        capabilities_check=lambda caps: caps.spaboy,
    ),
    ArcticSpaSensorDescription(
        key="electrode_wear",
        name="SpaBoy Electrode Wear",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        icon="mdi:battery",
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_wear_status",
        name="SpaBoy Electrode Wear Status",
        device_class=SensorDeviceClass.ENUM,
        options=["good", "ok", "fair", "low", "critical"],
        icon="mdi:battery-alert",
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_mah",
        name="SpaBoy Electrode Charge",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="mAh",
        icon="mdi:battery-charging",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_current",
        name="SpaBoy Electrode Current",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_voltage",
        name="SpaBoy Electrode Voltage",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_current_setpoint",
        name="SpaBoy Current Setpoint",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="µA",
        icon="mdi:target",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="electrode_state",
        name="SpaBoy Electrode State",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_spaboy_diagnostics,
    ),
    ArcticSpaSensorDescription(
        key="filter_status",
        name="Filter Status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "purge", "filtering", "suspended", "overtemperature", "resuming", "boost", "sanitize"],
    ),
    ArcticSpaSensorDescription(
        key="errors",
        name="Error Codes",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        capabilities_check=lambda caps: caps.has_errors,
    ),
    ArcticSpaSensorDescription(
        key="power_w",
        name="Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:lightning-bolt",
        capabilities_check=lambda caps: caps.has_power_sensor,
    ),
    ArcticSpaSensorDescription(
        key="current_adc",
        name="Current Draw (raw ADC)",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_power_sensor,
    ),
    ArcticSpaSensorDescription(
        key="heater_outlet_temp_f",
        name="Heater Outlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_heater_outlet_temp,
    ),
    ArcticSpaSensorDescription(
        key="heater1_state",
        name="Heater 1 State",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "warmup", "heating", "cooldown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_heater_states,
    ),
    ArcticSpaSensorDescription(
        key="heater2_state",
        name="Heater 2 State",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "warmup", "heating", "cooldown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_heater_states,
    ),
    ArcticSpaSensorDescription(
        key="wifi_ssid",
        name="Wi-Fi SSID",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_network_info,
    ),
    ArcticSpaSensorDescription(
        key="wifi_ip_address",
        name="Wi-Fi IP Address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_network_info,
    ),
    ArcticSpaSensorDescription(
        key="wifi_rssi_dbm",
        name="Wi-Fi Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_network_info,
    ),
    ArcticSpaSensorDescription(
        key="wifi_mac_address",
        name="Wi-Fi MAC Address",
        icon="mdi:network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        capabilities_check=lambda caps: caps.has_network_info,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    caps = coordinator.capabilities

    entities: list[SensorEntity] = [
        ArcticSpaSensor(coordinator, entry, desc)
        for desc in SENSORS
        if desc.capabilities_check is None or desc.capabilities_check(caps)
    ]
    if caps.has_power_sensor:
        entities.append(ArcticSpaEnergySensor(coordinator, entry))
    if caps.has_filter_details:
        filters = (coordinator.data or {}).get("filters", [])
        for f in filters:
            entities.append(ArcticSpaFilterSensor(coordinator, entry, f["slot"]))
    async_add_entities(entities)


class ArcticSpaSensor(CoordinatorEntity[ArcticSpaCoordinator], SensorEntity):
    """A read-only sensor sourced from the spa status response."""

    _attr_has_entity_name = True
    entity_description: ArcticSpaSensorDescription

    def __init__(
        self,
        coordinator: ArcticSpaCoordinator,
        entry: ConfigEntry,
        description: ArcticSpaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def native_value(self) -> Any:
        value = (self.coordinator.data or {}).get(self.entity_description.key)
        if self.entity_description.key == "errors":
            return ", ".join(value) if value else "none"
        return value


class ArcticSpaEnergySensor(CoordinatorEntity[ArcticSpaCoordinator], RestoreSensor):
    """Cumulative energy consumption in kWh, derived from the power reading.

    Integrates power_w over time using a left-Riemann sum: each coordinator
    update adds (power_w / 1000) × elapsed_hours to the running total.
    The accumulated value survives HA restarts via RestoreSensor.

    Works for MQTT mode (push updates on each telemetry topic) and Local mode
    (~640 ms spa_live cycle).  Gated on caps.has_power_sensor.
    """

    _attr_has_entity_name = True
    _attr_name = "Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_energy_kwh"
        self._attr_device_info = device_info(entry, coordinator.capabilities)
        self._accumulated_kwh: float = 0.0
        self._last_update: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_sensor_data()) is not None:
            try:
                self._accumulated_kwh = float(last_data.native_value)
            except (ValueError, TypeError):
                pass
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        data_ts: float | None = self.coordinator.data.get("data_timestamp")
        if data_ts is None or data_ts == self._last_update:
            # No new primary status reading; skip integration.
            self.async_write_ha_state()
            return
        if self._last_update is not None:
            power_w = self.coordinator.data.get("power_w")
            if isinstance(power_w, (int, float)) and power_w >= 0:
                elapsed_hours = (data_ts - self._last_update) / 3600.0
                self._accumulated_kwh += (power_w / 1000.0) * elapsed_hours
        self._last_update = data_ts
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return round(self._accumulated_kwh, 3)


class ArcticSpaFilterSensor(CoordinatorEntity[ArcticSpaCoordinator], SensorEntity):
    """A sensor representing a single physical filter cartridge.

    State: condition string ("Good" / "Missing").
    Extra attributes: serial number, install date (ISO string), days installed.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        coordinator: ArcticSpaCoordinator,
        entry: ConfigEntry,
        slot: int,
    ) -> None:
        super().__init__(coordinator)
        self._slot = slot
        self._attr_name = f"Filter {slot}"
        self._attr_unique_id = f"{entry.entry_id}_filter_{slot}"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    def _filter_data(self) -> dict[str, Any] | None:
        filters = (self.coordinator.data or {}).get("filters", [])
        for f in filters:
            if f.get("slot") == self._slot:
                return f
        return None

    @property
    def native_value(self) -> str | None:
        f = self._filter_data()
        if f is None:
            return None
        return f.get("condition")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = self._filter_data()
        if f is None:
            return {"serial": None, "install_date": None, "days_installed": None}
        install_date = f.get("install_date")
        return {
            "serial": f.get("serial"),
            "install_date": install_date.isoformat() if install_date else None,
            "days_installed": f.get("days_installed"),
        }
