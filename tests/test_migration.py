"""Non-destructive entity/device registry migration tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.cosmo import (
    _cleanup_stale_serial_metadata,
    _migrate_unique_ids,
)


def _entry():
    return SimpleNamespace(
        entry_id="entry-test",
        data={"device_id": "legacy-device"},
    )


def test_migration_preserves_entity_ids_and_only_rekeys_matching_unique_ids():
    entry = _entry()
    entity_registry = MagicMock()
    legacy = SimpleNamespace(
        entity_id="device_tracker.mock_watch",
        unique_id="legacy-device_tracker",
    )
    already_migrated = SimpleNamespace(
        entity_id="sensor.mock_watch_battery",
        unique_id="entry-test_battery",
    )
    device_registry = MagicMock()
    device_registry.async_get_device.return_value = SimpleNamespace(
        id="ha-device-test",
        config_entry_id=entry.entry_id,
    )

    with (
        patch(
            "custom_components.cosmo.er.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.cosmo.er.async_entries_for_config_entry",
            return_value=[legacy, already_migrated],
        ),
        patch(
            "custom_components.cosmo.dr.async_get",
            return_value=device_registry,
        ),
    ):
        _migrate_unique_ids(MagicMock(), entry)

    entity_registry.async_update_entity.assert_called_once_with(
        legacy.entity_id,
        new_unique_id="entry-test_tracker",
    )
    device_registry.async_update_device.assert_called_once_with(
        "ha-device-test",
        new_identifiers={("cosmo", "entry-test")},
    )
    entity_registry.async_remove.assert_not_called()


def test_migration_does_not_claim_device_owned_by_another_entry():
    entry = _entry()
    device_registry = MagicMock()
    device_registry.async_get_device.return_value = SimpleNamespace(
        id="ha-device-test",
        config_entry_id="different-entry",
    )

    with (
        patch("custom_components.cosmo.er.async_get", return_value=MagicMock()),
        patch(
            "custom_components.cosmo.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch(
            "custom_components.cosmo.dr.async_get",
            return_value=device_registry,
        ),
    ):
        _migrate_unique_ids(MagicMock(), entry)

    device_registry.async_update_device.assert_not_called()


def test_serial_cleanup_clears_metadata_without_device_or_entity_deletion():
    entry = _entry()
    device_registry = MagicMock()
    device_registry.async_get_device.return_value = SimpleNamespace(id="ha-device-test")

    with patch(
        "custom_components.cosmo.dr.async_get",
        return_value=device_registry,
    ):
        _cleanup_stale_serial_metadata(MagicMock(), entry)

    device_registry.async_get_device.assert_called_once_with(
        identifiers={("cosmo", "entry-test")}
    )
    device_registry.async_update_device.assert_called_once_with(
        "ha-device-test",
        serial_number=None,
    )
    device_registry.async_remove_device.assert_not_called()
