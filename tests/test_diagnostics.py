"""Tests for the privacy-safe Home Assistant diagnostics export."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.cosmo.api import CosmoClient, _reset_governors_for_testing
from custom_components.cosmo.coordinator import CosmoCoordinator
from custom_components.cosmo.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)

SENSITIVE_KEYS = {
    "email",
    "password",
    "access",
    "refresh",
    "token",
    "imei",
    "serial_number",
    "latitude",
    "longitude",
    "gpsDate",
    "radius",
    "firstName",
    "lastName",
    "gsmNumber",
    "phone",
    "phoneNumber",
    "message",
    "messages",
    "call",
    "calls",
    "data",
    "entry_id",
}


def test_diagnostics_reports_only_redacted_operational_health(
    mock_entry, mock_coordinator
):
    entry = mock_entry
    runtime = MagicMock()
    coordinator = mock_coordinator
    coordinator.last_successful_poll = None
    coordinator.last_error_class = "CosmoApiError"
    coordinator.cloud_reachable = True
    coordinator.last_poll_age = 42
    runtime.coordinator = coordinator
    runtime.client = MagicMock()
    entry.runtime_data = runtime

    async def _run():
        diagnostics = await async_get_config_entry_diagnostics(MagicMock(), entry)
        flattened = str(diagnostics)
        for sensitive_key in SENSITIVE_KEYS:
            assert sensitive_key not in diagnostics
        assert entry.data["email"] not in flattened
        assert entry.data["password"] not in flattened
        assert entry.data["device_id"] not in flattened
        assert entry.entry_id not in flattened
        assert "entry_id" not in diagnostics
        assert diagnostics["integration"] == "cosmo"
        assert diagnostics["version"] == "0.5.5"
        assert diagnostics["last_error_class"] == "CosmoApiError"
        assert diagnostics["device_id_present"] is True
        assert "active_tracking_on_demand" in diagnostics["supported_capabilities"]

    asyncio.run(_run())


def test_defensive_redaction_contract_covers_all_sensitive_key_classes():
    assert SENSITIVE_KEYS <= set(TO_REDACT)


def test_diagnostics_uses_governed_coordinator_rate_limit_streak(
    mock_hass, mock_entry
):
    _reset_governors_for_testing()
    response = MagicMock()
    response.status = 429
    response.headers = {}
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    request_context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.request.return_value = request_context
    client = CosmoClient(session, "synthetic-account", "placeholder")
    client._access = "synthetic"
    coordinator = CosmoCoordinator(
        mock_hass,
        mock_entry,
        client,
        "synthetic-device",
        timedelta(minutes=2),
    )
    mock_entry.runtime_data = SimpleNamespace(
        coordinator=coordinator,
        client=client,
    )

    async def _run():
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()
        diagnostics = await async_get_config_entry_diagnostics(
            MagicMock(), mock_entry
        )
        assert diagnostics["backoff_class"] == "rate_limit"
        assert diagnostics["backoff_streak"] == 1

    asyncio.run(_run())
