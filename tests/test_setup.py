"""Config-entry setup ordering and startup Active Tracking initialization tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.cosmo import async_setup_entry


def test_setup_initializes_tracking_after_first_map_refresh(mock_hass, mock_entry):
    """The one-time settings read must run after the initial map refresh."""
    client = MagicMock()
    client.login = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_initialize_active_tracking = AsyncMock()
    order = MagicMock()
    order.attach_mock(coordinator.async_config_entry_first_refresh, "first_refresh")
    order.attach_mock(coordinator.async_initialize_active_tracking, "initialize_tracking")

    async def _run():
        with (
            patch("custom_components.cosmo._migrate_unique_ids"),
            patch("custom_components.cosmo._cleanup_stale_serial_metadata"),
            patch("custom_components.cosmo._cleanup_unsupported_entities"),
            patch("custom_components.cosmo.CosmoClient", return_value=client),
            patch(
                "custom_components.cosmo.CosmoCoordinator",
                return_value=coordinator,
            ) as coordinator_factory,
        ):
            assert await async_setup_entry(mock_hass, mock_entry) is True

        assert order.mock_calls == [call.first_refresh(), call.initialize_tracking()]
        assert coordinator_factory.call_args.kwargs == {
            "adaptive_enabled": False,
            "trusted_zone_entity_ids": [],
        }
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        assert mock_entry.runtime_data.coordinator is coordinator

    asyncio.run(_run())


def test_setup_passes_explicit_adaptive_options(mock_hass, mock_entry):
    mock_entry.options = {
        "adaptive_polling": True,
        "trusted_zones": ["zone.synthetic"],
    }
    client = MagicMock()
    client.login = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_initialize_active_tracking = AsyncMock()

    async def _run():
        with (
            patch("custom_components.cosmo._migrate_unique_ids"),
            patch("custom_components.cosmo._cleanup_stale_serial_metadata"),
            patch("custom_components.cosmo._cleanup_unsupported_entities"),
            patch("custom_components.cosmo.CosmoClient", return_value=client),
            patch(
                "custom_components.cosmo.CosmoCoordinator",
                return_value=coordinator,
            ) as coordinator_factory,
        ):
            assert await async_setup_entry(mock_hass, mock_entry) is True

        assert coordinator_factory.call_args.kwargs == {
            "adaptive_enabled": True,
            "trusted_zone_entity_ids": ["zone.synthetic"],
        }

    asyncio.run(_run())


def test_setup_propagates_startup_tracking_auth_failure(mock_hass, mock_entry):
    """An auth failure during startup settings initialization starts reauth."""
    client = MagicMock()
    client.login = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_initialize_active_tracking = AsyncMock(
        side_effect=ConfigEntryAuthFailed("expired")
    )

    async def _run():
        with (
            patch("custom_components.cosmo._migrate_unique_ids"),
            patch("custom_components.cosmo._cleanup_stale_serial_metadata"),
            patch("custom_components.cosmo._cleanup_unsupported_entities"),
            patch("custom_components.cosmo.CosmoClient", return_value=client),
            patch("custom_components.cosmo.CosmoCoordinator", return_value=coordinator),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await async_setup_entry(mock_hass, mock_entry)

        mock_hass.config_entries.async_forward_entry_setups.assert_not_awaited()
        assert mock_entry.runtime_data is None

    asyncio.run(_run())
