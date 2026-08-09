"""Binary sensors: emergency (SOS) mode, powered off."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CosmoConfigEntry
from .entity import CosmoEntity


@dataclass(frozen=True, kw_only=True)
class CosmoBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS: tuple[CosmoBinaryDescription, ...] = (
    CosmoBinaryDescription(
        key="emergency",
        translation_key="emergency",
        device_class=BinarySensorDeviceClass.SAFETY,
        icon="mdi:alarm-light",
        value_fn=lambda d: bool( getattr(d, "emergency_mode", None) if hasattr(d,"emergency_mode") else (d.get("emergencyMode") if isinstance(d,dict) else None) ) ,
    ),

    CosmoBinaryDescription(
        key="cloud_reachable",
        translation_key="cloud_reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,  # special cased in entity
    ),
    CosmoBinaryDescription(
        key="active_tracking",
        translation_key="active_tracking",
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None,  # special cased
    ),
    CosmoBinaryDescription(
        key="powered_off",
        translation_key="powered_off",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: bool( getattr(d, "shutdown", None) if hasattr(d,"shutdown") else (d.get("shutdown") if isinstance(d,dict) else None) ) ,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CosmoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        CosmoBinarySensor(rt.coordinator, entry.data["name"], entry.data.get("model"), desc)
        for desc in BINARY_SENSORS
    )


class CosmoBinarySensor(CosmoEntity, BinarySensorEntity):
    entity_description: CosmoBinaryDescription

    def __init__(self, coordinator, name, model, description: CosmoBinaryDescription) -> None:
        super().__init__(coordinator, name, model)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        key = self.entity_description.key
        if key == "cloud_reachable":
            return bool(getattr(self.coordinator, "cloud_reachable", False))
        if key == "active_tracking":
            return bool(getattr(self.coordinator, "active_tracking", False))
        return self.entity_description.value_fn(self._device)
