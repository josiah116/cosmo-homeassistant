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
        value_fn=lambda d: bool(d.get("emergencyMode")),
    ),
    CosmoBinaryDescription(
        key="powered_off",
        translation_key="powered_off",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: bool(d.get("shutdown")),
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
        return self.entity_description.value_fn(self._device)
