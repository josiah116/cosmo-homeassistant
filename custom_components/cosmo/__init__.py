"""The Cosmo (JrTrack kids watch) integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import (
    CONF_ADAPTIVE_POLLING,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TRUSTED_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import CosmoCoordinator

PLATFORMS = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


@dataclass
class CosmoRuntime:
    client: CosmoClient
    coordinator: CosmoCoordinator


CosmoConfigEntry: TypeAlias = ConfigEntry[CosmoRuntime]


def _migrate_unique_ids(hass: HomeAssistant, entry: CosmoConfigEntry) -> None:
    """One-time migration: entity unique_ids used to be keyed by the FiLIP
    device_id (f"{device_id}_tracker" etc). That breaks if the watch is ever
    replaced (new device_id) since a reconfigure would then spawn brand new
    entity_ids and orphan history. Re-key existing entities onto entry_id,
    which reconfigure never changes. Idempotent: no-op once migrated.
    """
    old_prefix = f"{entry.data[CONF_DEVICE_ID]}_"
    new_prefix = f"{entry.entry_id}_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(old_prefix):
            registry.async_update_entity(
                entity.entity_id,
                new_unique_id=new_prefix + entity.unique_id[len(old_prefix) :],
            )

    old_device_ids = {(DOMAIN, str(entry.data[CONF_DEVICE_ID]))}
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers=old_device_ids)
    if device is not None and device.config_entry_id == entry.entry_id:
        device_registry.async_update_device(
            device.id, new_identifiers={(DOMAIN, entry.entry_id)}
        )


def _cleanup_stale_serial_metadata(hass: HomeAssistant, entry: CosmoConfigEntry) -> None:
    """Safely clear any legacy serial_number (IMEI) from this integration's device.

    Never logs or reads/inspects the identifier value itself.
    Does not delete device or any entity. Only metadata update.
    Preserves all stable old unique IDs / entity IDs via prior migration.
    """
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if device is not None:
        # set unconditionally; no getattr/read of the value
        device_registry.async_update_device(device.id, serial_number=None)


def _cleanup_unsupported_entities(hass: HomeAssistant, entry: CosmoConfigEntry) -> None:
    """Remove only the two unsupported sensor registrations from prior releases."""
    registry = er.async_get(hass)
    unsupported_unique_ids = {
        f"{entry.entry_id}_charger_battery",
        f"{entry.entry_id}_firmware",
    }
    for entity in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if entity.unique_id in unsupported_unique_ids:
            registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: CosmoConfigEntry) -> bool:
    _migrate_unique_ids(hass, entry)
    _cleanup_stale_serial_metadata(hass, entry)
    _cleanup_unsupported_entities(hass, entry)
    client = CosmoClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    try:
        await client.login()
    except CosmoAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CosmoApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    opts = entry.options or {}
    adaptive = bool(opts.get(CONF_ADAPTIVE_POLLING, False))
    trusted = opts.get(CONF_TRUSTED_ZONES, []) or []
    coordinator = CosmoCoordinator(
        hass,
        entry,
        client,
        entry.data[CONF_DEVICE_ID],
        DEFAULT_SCAN_INTERVAL,
        adaptive_enabled=adaptive,
        trusted_zone_entity_ids=trusted,
    )
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_initialize_active_tracking()

    entry.runtime_data = CosmoRuntime(client, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CosmoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
