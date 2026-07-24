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
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CosmoConfigEntry
from .entity import CosmoEntity


@dataclass(frozen=True, kw_only=True)
class CosmoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


def _as_dt(v: Any):
    return dt_util.parse_datetime(v) if v else None


SENSORS: tuple[CosmoSensorDescription, ...] = (
    CosmoSensorDescription(
        key="location",
        translation_key="location",
        icon="mdi:map-marker",
        value_fn=lambda d: d.get("address"),
    ),
    CosmoSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("batteryLevel"),
    ),
    CosmoSensorDescription(
        key="charger_battery",
        translation_key="charger_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("externalBatteryLevel"),
    ),
    CosmoSensorDescription(
        key="last_fix",
        translation_key="last_fix",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_dt(d.get("gpsDate")),
    ),
    CosmoSensorDescription(
        key="firmware",
        translation_key="firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("firmwareVersion"),
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
        return self.entity_description.value_fn(self._device)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose the full geocoded address on the location sensor."""
        if self.entity_description.key == "location":
            return {"full_address": self._device.get("address_full")}
        return None
