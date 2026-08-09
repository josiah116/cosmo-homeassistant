"""Tests for the privacy-safe Home Assistant diagnostics export."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

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
        assert diagnostics["integration"] == "cosmo"
        assert diagnostics["version"] == "0.5.0"
        assert diagnostics["last_error_class"] == "CosmoApiError"
        assert diagnostics["device_id_present"] is True
        assert "active_tracking_on_demand" in diagnostics["supported_capabilities"]

    asyncio.run(_run())


def test_defensive_redaction_contract_covers_all_sensitive_key_classes():
    assert SENSITIVE_KEYS <= set(TO_REDACT)
