"""Sensors: battery, charger battery, last fix time, firmware."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CosmoConfigEntry
from .entity import CosmoEntity


@dataclass(frozen=True, kw_only=True)
class CosmoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]


def _as_dt(v: Any):
    return dt_util.parse_datetime(v) if v else None


SENSORS: tuple[CosmoSensorDescription, ...] = (
    CosmoSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: getattr(d, "battery_level", None) if hasattr(d, "battery_level") else (d.get("batteryLevel") if isinstance(d, dict) else None),
    ),
    CosmoSensorDescription(
        key="charger_battery",
        translation_key="charger_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "external_battery_level", None) if hasattr(d, "external_battery_level") else (d.get("externalBatteryLevel") if isinstance(d, dict) else None),
    ),
    CosmoSensorDescription(
        key="last_fix",
        translation_key="last_fix",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_dt( getattr(d, "gps_date", None) if hasattr(d, "gps_date") else (d.get("gpsDate") if isinstance(d, dict) else None) ) ,
    ),
    CosmoSensorDescription(
        key="firmware",
        translation_key="firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: getattr(d, "firmware_version", None) if hasattr(d, "firmware_version") else (d.get("firmwareVersion") if isinstance(d, dict) else None),
    ),

    CosmoSensorDescription(
        key="cloud_reachability",
        translation_key="cloud_reachability",
        icon="mdi:cloud-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: "online" if getattr(d, "cloud_reachable", False) else "offline",
    ),
    CosmoSensorDescription(
        key="last_successful_poll",
        translation_key="last_successful_poll",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,  # special: use coordinator timestamp
    ),
    CosmoSensorDescription(
        key="location_fix_age",
        translation_key="location_fix_age",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,  # special
    ),
    CosmoSensorDescription(
        key="gps_accuracy",
        translation_key="gps_accuracy",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: getattr(d, "radius", None) if hasattr(d, "radius") else (d.get("radius") if isinstance(d, dict) else None),
    ),
    CosmoSensorDescription(
        key="last_locate",
        translation_key="last_locate",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,  # special cased for outcome+time
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CosmoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        CosmoSensor(rt.coordinator, entry.data["name"], entry.data.get("model"), desc)
        for desc in SENSORS
    )


class CosmoSensor(CosmoEntity, SensorEntity):
    entity_description: CosmoSensorDescription

    def __init__(self, coordinator, name, model, description: CosmoSensorDescription) -> None:
        super().__init__(coordinator, name, model)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        key = self.entity_description.key
        coord = self.coordinator
        if key == "last_successful_poll":
            ts = getattr(coord, "last_successful_poll", None)
            return ts
        if key == "location_fix_age":
            age = getattr(coord, "last_poll_age", None)
            return age
        if key == "last_locate":
            # return timestamp; state may be in attributes or separate for outcome
            return getattr(coord, "last_locate_time", None)
        if key == "cloud_reachability":
            return "online" if getattr(coord, "cloud_reachable", False) else "offline"
        return self.entity_description.value_fn(self._device)
