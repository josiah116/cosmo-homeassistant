"""Tests for config flow including reauth.

Covers reauth flow for ConfigEntryAuthFailed recovery.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.cosmo.config_flow import CosmoConfigFlow
from custom_components.cosmo.const import CONF_EMAIL, CONF_PASSWORD


def test_reauth_step_init():
    """Reauth step sets up and delegates to confirm."""
    async def _run():
        flow = CosmoConfigFlow()
        # ensure _get_reauth_entry etc from dummy
        result = await flow.async_step_reauth({"email": "user@example.com", "password": "secret"})
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert flow._email == "user@example.com"
        assert flow._password is None
    asyncio.run(_run())


def test_reauth_confirm_success_updates_entry():
    """Successful reauth with new creds updates and reloads."""
    async def _run():
        flow = CosmoConfigFlow()
        # pre-set like step would
        flow._email = "old@example.com"
        with patch.object(flow, "_authenticate", new=AsyncMock(return_value=([{"id": "12345"}], {}))):
            result = await flow.async_step_reauth_confirm(
                {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "newpass"}
            )
        assert result["type"] == "update_reload_and_abort"
        updates = result.get("updates", {})
        assert updates["data_updates"][CONF_EMAIL] == "new@example.com"
        assert updates["data_updates"][CONF_PASSWORD] == "newpass"
    asyncio.run(_run())


def test_reauth_confirm_invalid_auth_shows_error():
    """Bad creds in reauth shows invalid_auth error."""
    async def _run():
        flow = CosmoConfigFlow()
        with patch.object(flow, "_authenticate", new=AsyncMock(return_value=([], {"base": "invalid_auth"}))):
            result = await flow.async_step_reauth_confirm(
                {CONF_EMAIL: "bad@example.com", CONF_PASSWORD: "wrong"}
            )
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_auth"}
    asyncio.run(_run())


def test_reauth_confirm_cannot_connect():
    """Network error shows cannot_connect."""
    async def _run():
        flow = CosmoConfigFlow()
        with patch.object(flow, "_authenticate", new=AsyncMock(return_value=([], {"base": "cannot_connect"}))):
            result = await flow.async_step_reauth_confirm(
                {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "pw"}
            )
        assert result["type"] == "form"
        assert result.get("errors") == {"base": "cannot_connect"}
    asyncio.run(_run())
