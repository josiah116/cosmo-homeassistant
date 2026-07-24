"""Base entity for Cosmo."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import CosmoCoordinator


class CosmoEntity(CoordinatorEntity[CosmoCoordinator]):
    """Common device wiring for all Cosmo entities.

    Identity (device identifiers + entity unique_ids) is anchored on the config
    entry, NOT the FiLIP device_id. A watch can break and get replaced with a
    new one carrying a different device_id — reconfiguring the entry to point
    at the new device_id must not spawn a new HA device / new entity_ids and
    orphan the history, so entry_id is the stable anchor and device_id is just
    "whichever physical watch this entry currently talks to".
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: CosmoCoordinator, name: str, model: str | None) -> None:
        super().__init__(coordinator)
        d = coordinator.data or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name=name,
            manufacturer=MANUFACTURER,
            model=model or "JrTrack",
            sw_version=d.get("firmwareVersion"),
            serial_number=d.get("imei"),
        )

    @property
    def _device(self) -> dict:
        return self.coordinator.data or {}
