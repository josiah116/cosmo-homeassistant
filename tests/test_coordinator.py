"""Tests for coordinator: health timestamps, success/error, unchanged payload behavior.

Uses the narrow dummy HA classes from conftest so real class creation works.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.cosmo.coordinator import CosmoCoordinator
from custom_components.cosmo.models import CosmoDevice, normalize_device


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
