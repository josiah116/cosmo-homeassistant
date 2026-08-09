"""Config flow: email + password -> pick watch.

Supports multiple watches (add the integration again to add a second kid's
watch — the picker excludes watches already configured elsewhere) and
reconfiguring an existing entry to point at a different watch, e.g. after a
broken watch is replaced with a new one under a new FiLIP device_id.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CosmoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email/password login against the FiLIP backend."""

    VERSION = 2

    def __init__(self) -> None:
        self._email: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []

    async def _authenticate(
        self, email: str, password: str
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Log in and list the account's watches. Returns (devices, errors)."""
        client = CosmoClient(async_get_clientsession(self.hass), email, password)
        try:
            await client.login()
            devices = await client.get_devices()
        except CosmoAuthError:
            return [], {"base": "invalid_auth"}
        except CosmoApiError:
            return [], {"base": "cannot_connect"}
        return devices, {}

    def _other_entry_device_ids(self, *, skip_entry_id: str | None = None) -> set[str]:
        """device_ids already claimed by other Cosmo entries."""
        return {
            str(entry.data[CONF_DEVICE_ID])
            for entry in self._async_current_entries()
            if entry.entry_id != skip_entry_id
        }

    def _device_options(self, devices: list[dict[str, Any]]) -> dict[str, str]:
        return {str(d["id"]): d.get("firstName") or f"Watch {d['id']}" for d in devices}

    # --- initial setup: sign in, then pick a not-yet-configured watch -------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            self._password = user_input[CONF_PASSWORD]
            devices, errors = await self._authenticate(self._email, self._password)
            if not errors:
                already = self._other_entry_device_ids()
                self._devices = [d for d in devices if str(d["id"]) not in already]
                if not self._devices:
                    reason = "no_devices" if not devices else "all_devices_configured"
                    return self.async_abort(reason=reason)
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if len(self._devices) == 1:
            return await self._create(self._devices[0])
        if user_input is not None:
            chosen = next(
                d for d in self._devices if str(d["id"]) == user_input[CONF_DEVICE_ID]
            )
            return await self._create(chosen)
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): vol.In(self._device_options(self._devices))}
            ),
        )

    async def _create(self, device: dict[str, Any]) -> ConfigFlowResult:
        device_id = str(device["id"])
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        name = device.get("firstName") or "Cosmo Watch"
        return self.async_create_entry(
            title=name,
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_DEVICE_ID: device_id,
                "name": name,
                "model": device.get("hardwareName"),
            },
        )

    # --- reconfigure: swap which watch an existing entry talks to (e.g. the
    # watch broke and got replaced) without losing entities/history, which
    # are keyed off entry_id, not device_id. See custom_components/cosmo/entity.py.

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        self._email = entry.data[CONF_EMAIL]
        self._password = entry.data[CONF_PASSWORD]
        return await self.async_step_reconfigure_auth()

    async def async_step_reconfigure_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            self._password = user_input[CONF_PASSWORD]
            devices, errors = await self._authenticate(self._email, self._password)
            if not errors:
                if not devices:
                    return self.async_abort(reason="no_devices")
                self._devices = devices
                return await self.async_step_reconfigure_device()

        return self.async_show_form(
            step_id="reconfigure_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        chosen: dict[str, Any] | None = None
        if user_input is not None:
            chosen = next(
                d for d in self._devices if str(d["id"]) == user_input[CONF_DEVICE_ID]
            )
        elif len(self._devices) == 1:
            chosen = self._devices[0]

        if chosen is not None:
            device_id = str(chosen["id"])
            claimed = self._other_entry_device_ids(skip_entry_id=entry.entry_id)
            if device_id in claimed:
                return self.async_abort(reason="already_configured")
            name = chosen.get("firstName") or entry.data.get("name") or "Cosmo Watch"
            return self.async_update_reload_and_abort(
                entry,
                unique_id=device_id,
                title=name,
                data={
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_DEVICE_ID: device_id,
                    "name": name,
                    "model": chosen.get("hardwareName"),
                },
            )

        return self.async_show_form(
            step_id="reconfigure_device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): vol.In(self._device_options(self._devices))}
            ),
        )

    # --- reauth: triggered by ConfigEntryAuthFailed (bad/expired creds, pw change
    # in the COSMO app, etc). Updates the stored email/password for this entry
    # (device_id stays the same; reconfigure is used to change which watch).
    # We re-use _authenticate so the same error mapping and login logic applies.

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication for an existing config entry."""
        email = entry_data.get(CONF_EMAIL)
        self._email = email if isinstance(email, str) else None
        # do not carry over the old password into the form; force re-entry
        self._password = None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt the user to re-enter credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            password = user_input[CONF_PASSWORD]
            self._email = email
            self._password = password
            devices, errors = await self._authenticate(email, password)
            if not errors:
                # Harden reauth: valid creds for a different account (no matching
                # watch) must fail closed here with a user-visible error. Do not
                # blindly update the entry; let coordinator surface UpdateFailed
                # only for transient "device vanished" after a legitimate reauth.
                entry_device_id = str(entry.data.get(CONF_DEVICE_ID, ""))
                has_watch = any(
                    str(d.get("id")) == entry_device_id for d in devices
                )
                if not has_watch:
                    errors = {"base": "device_not_on_account"}
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                        },
                    )

        default_email = self._email or ""
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=default_email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )
