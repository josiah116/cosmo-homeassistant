"""Device tracker showing the watch's last-known location on the HA map."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CosmoConfigEntry
from .entity import CosmoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CosmoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        [CosmoTracker(rt.coordinator, entry.data["name"], entry.data.get("model"))]
    )


class CosmoTracker(CosmoEntity, TrackerEntity):
    """The watch as a GPS tracker."""

    _attr_name = None
    _attr_icon = "mdi:watch"

    def __init__(self, coordinator, name, model) -> None:
        super().__init__(coordinator, name, model)
        self._attr_unique_id = f"{coordinator.entry_id}_tracker"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def available(self) -> bool:
        """Fail closed when either critical coordinate is unavailable."""
        return super().available and self.latitude is not None and self.longitude is not None

    @property
    def latitude(self) -> float | None:
        d = self._device
        return (
            getattr(d, "latitude", None)
            if hasattr(d, "latitude")
            else (d.get("latitude") if isinstance(d, dict) else None)
        )

    @property
    def longitude(self) -> float | None:
        d = self._device
        return (
            getattr(d, "longitude", None)
            if hasattr(d, "longitude")
            else (d.get("longitude") if isinstance(d, dict) else None)
        )

    @property
    def location_accuracy(self) -> int:
        d = self._device
        rad = (
            getattr(d, "radius", None)
            if hasattr(d, "radius")
            else (d.get("radius") if isinstance(d, dict) else None)
        )
        return int(rad or 0)
