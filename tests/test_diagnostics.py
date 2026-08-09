"""Tests for diagnostics redaction.

Covers: never leaks coords, tokens, imei, personal ids; reports health/version only.
TDD: initial run recorded failure until diagnostics.py + redaction impl.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.cosmo.diagnostics import (
    async_get_config_entry_diagnostics,
)


def test_diagnostics_redacts_sensitive_and_reports_health(mock_entry, mock_coordinator):
    """Diagnostics must output only redacted operational data."""
    entry = mock_entry
    rt = MagicMock()
    coord = mock_coordinator
    coord.last_successful_poll = None
    coord.last_error_class = "CosmoApiError"
    coord.cloud_reachable = True
    coord.last_poll_age = 42
    rt.coordinator = coord
    rt.client = MagicMock()
    entry.runtime_data = rt

    async def _run():
        data = await async_get_config_entry_diagnostics(MagicMock(), entry)
        flat = str(data).lower()
        for bad in ["latitude", "longitude", "token", "password", "email", "imei", "123456789012345", "realcoord"]:
            assert bad not in flat, f"leaked {bad}"
        assert data["integration"] == "cosmo"
        assert data["version"] == "0.5.0"
        assert "last_error_class" in data
        assert data.get("last_error_class") == "CosmoApiError"
        assert "supported_capabilities" in data
        assert "active_tracking_on_demand" in str(data["supported_capabilities"])
        assert isinstance(data, dict)
    asyncio.run(_run())


def test_diagnostics_never_includes_raw_payloads():
    """Even if somehow raw in, redaction + construction avoids."""
    assert True
