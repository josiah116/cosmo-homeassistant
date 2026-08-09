"""Tests for config flow including reauth.

Covers reauth flow for ConfigEntryAuthFailed recovery.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.cosmo.config_flow import (
    CosmoConfigFlow,
    CosmoOptionsFlowHandler,
)
from custom_components.cosmo.const import (
    CONF_ADAPTIVE_POLLING,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TRUSTED_ZONES,
)


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


def test_reauth_confirm_device_not_on_account():
    """Valid credentials for another account must not replace entry credentials."""

    async def _run():
        flow = CosmoConfigFlow()
        with patch.object(
            flow,
            "_authenticate",
            new=AsyncMock(return_value=([{"id": "different-watch"}], {})),
        ):
            result = await flow.async_step_reauth_confirm(
                {CONF_EMAIL: "other@example.invalid", CONF_PASSWORD: "placeholder"}
            )
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "device_not_on_account"}

    asyncio.run(_run())


def test_reauth_confirm_empty_device_list_fails_closed():
    """Valid login without watches must remain on the reauth form."""

    async def _run():
        flow = CosmoConfigFlow()
        with patch.object(
            flow,
            "_authenticate",
            new=AsyncMock(return_value=([], {})),
        ):
            result = await flow.async_step_reauth_confirm(
                {CONF_EMAIL: "account@example.invalid", CONF_PASSWORD: "placeholder"}
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "device_not_on_account"}

    asyncio.run(_run())


def test_options_handler_uses_framework_config_entry_and_one_reload_mechanism():
    entry = type("Entry", (), {"options": {}})()
    handler = CosmoConfigFlow.async_get_options_flow(entry)
    assert isinstance(handler, CosmoOptionsFlowHandler)
    assert handler.automatic_reload is True
    assert "config_entry" not in handler.__dict__
    assert "_config_entry" not in handler.__dict__


def test_options_defaults_are_disabled_and_no_trusted_zones():
    async def _run():
        handler = CosmoOptionsFlowHandler()
        entry = type("Entry", (), {"options": {}})()
        handler.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_known_entry=lambda entry_id: entry,
            )
        )
        handler.handler = "synthetic-entry"

        def _marker(kind):
            return lambda key, default=None: (
                kind,
                key,
                tuple(default) if isinstance(default, list) else default,
            )

        with (
            patch(
                "custom_components.cosmo.config_flow.vol.Required",
                side_effect=_marker("required"),
            ),
            patch(
                "custom_components.cosmo.config_flow.vol.Optional",
                side_effect=_marker("optional"),
            ),
            patch(
                "custom_components.cosmo.config_flow.vol.Schema",
                side_effect=lambda value: value,
            ),
        ):
            result = await handler.async_step_init()

        schema = result["data_schema"]
        assert ("required", CONF_ADAPTIVE_POLLING, False) in schema
        assert ("optional", CONF_TRUSTED_ZONES, ()) in schema
        assert result["type"] == "form"

    asyncio.run(_run())


def test_options_submission_persists_only_selected_options():
    async def _run():
        handler = CosmoOptionsFlowHandler()
        submitted = {
            CONF_ADAPTIVE_POLLING: True,
            CONF_TRUSTED_ZONES: ["zone.synthetic"],
        }
        result = await handler.async_step_init(submitted)
        assert result == {"type": "create_entry", "title": "", "data": submitted}

    asyncio.run(_run())
