"""Tests for coordinator: health timestamps, success/error, unchanged payload behavior.

Uses the narrow dummy HA classes from conftest so real class creation works.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.cosmo.coordinator import CosmoCoordinator
from custom_components.cosmo.models import (
    CosmoDevice,
    normalize_device,
    normalize_settings,
)


def _make_coordinator(client_mock, device_id="12345"):
    """Instantiate using dummy base provided by conftest mocks."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {"device_id": device_id}
    coord = CosmoCoordinator(
        hass, entry, client_mock, device_id, timedelta(seconds=30)
    )
    return coord


def test_coordinator_health_on_success(mock_map_payload):
    """Coordinator records last_successful_poll and clears error on success."""
    client = MagicMock()
    dev = normalize_device(mock_map_payload["data"]["Devices"][0])
    client.get_device = AsyncMock(return_value=dev)

    coord = _make_coordinator(client)

    async def _run():
        data = await coord._async_update_data()
        assert isinstance(data, CosmoDevice)
        assert coord.last_successful_poll is not None
        assert coord.last_error is None
        assert coord.last_error_class is None
        return data

    asyncio.run(_run())


def test_coordinator_health_on_error():
    """On auth/api error, records error class and timestamps not updated."""
    from custom_components.cosmo.api import CosmoAuthError

    client = MagicMock()
    client.get_device = AsyncMock(side_effect=CosmoAuthError("bad creds"))

    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()
        assert coord.last_error is not None
        assert coord.last_error_class == "CosmoAuthError"
    asyncio.run(_run())


def test_coordinator_unchanged_payload_behavior():
    """Coordinator configured with always_update=False for unchanged payload skip."""
    client = MagicMock()
    dev = normalize_device({"id": "12345", "batteryLevel": 80})
    client.get_device = AsyncMock(return_value=dev)
    coord = _make_coordinator(client)
    assert coord.always_update is False


def test_startup_active_tracking_init_from_settings_when_map_omits():
    """Startup settings state is authoritative, protected, and lifetime-bounded."""
    client = MagicMock()
    client.get_settings = AsyncMock(
        return_value=normalize_settings({"activeTrackingEnable": True})
    )
    coord = _make_coordinator(client)
    coord.active_tracking = None

    async def _run():
        before = datetime.now(timezone.utc)
        await coord.async_initialize_active_tracking()
        client.get_settings.assert_awaited_once_with("12345")
        assert coord.active_tracking is True
        assert coord._active_readback_protected_until is not None
        assert coord._active_readback_protected_until > before
        assert coord._active_tracking_expires_at is not None
        assert coord._active_tracking_expires_at > before

    asyncio.run(_run())


def test_startup_active_tracking_skips_if_already_set_from_map():
    """If map provided explicit state, do not hit settings at startup (map authority)."""
    client = MagicMock()
    client.get_settings = AsyncMock()
    coord = _make_coordinator(client)
    coord.active_tracking = False

    async def _run():
        await coord.async_initialize_active_tracking()
        client.get_settings.assert_not_awaited()
        assert coord.active_tracking is False

    asyncio.run(_run())


def test_startup_active_tracking_init_swallows_settings_errors():
    """Transient settings failure at startup must not block; state stays None."""
    from custom_components.cosmo.api import CosmoApiError

    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=CosmoApiError("settings down"))
    coord = _make_coordinator(client)
    coord.active_tracking = None

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None
        client.get_settings.assert_awaited_once()

    asyncio.run(_run())


def test_startup_active_tracking_auth_failure_requests_reauthentication():
    """Expired startup credentials must enter native HA reauthentication."""
    from custom_components.cosmo.api import CosmoAuthError

    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=CosmoAuthError("expired"))
    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None

    asyncio.run(_run())


@pytest.mark.parametrize(
    "settings",
    [None, normalize_settings({}), normalize_settings({"activeTrackingEnable": "false"})],
)
def test_startup_active_tracking_malformed_state_remains_unknown(settings):
    """Missing or non-boolean critical state must never become false."""
    client = MagicMock()
    client.get_settings = AsyncMock(return_value=settings)
    coord = _make_coordinator(client)

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None
        assert coord._active_readback_protected_until is None
        assert coord._active_tracking_expires_at is None

    asyncio.run(_run())


def test_startup_active_tracking_false_is_stable_without_expiration():
    client = MagicMock()
    client.get_settings = AsyncMock(
        return_value=normalize_settings({"activeTrackingEnable": False})
    )
    coord = _make_coordinator(client)

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is False
        assert coord._active_readback_protected_until is not None
        assert coord._active_tracking_expires_at is None

    asyncio.run(_run())


def test_startup_active_tracking_cancellation_propagates():
    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=asyncio.CancelledError())
    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(asyncio.CancelledError):
            await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None

    asyncio.run(_run())


def test_recurring_map_updates_never_read_settings():
    """The two-minute coordinator path remains map-only."""
    client = MagicMock()
    client.get_device = AsyncMock(return_value=CosmoDevice(id="12345"))
    client.get_settings = AsyncMock()
    coord = _make_coordinator(client)

    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert client.get_device.await_count == 2
        client.get_settings.assert_not_awaited()

    asyncio.run(_run())
