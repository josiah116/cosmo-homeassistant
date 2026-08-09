"""Client for the FiLIP (api.myfilip.com) backend behind COSMO.

Auth: email + password -> POST /v2/token -> {accessToken, refreshToken,
expDate}. Access tokens are short-lived; renew via POST /v2/token/refresh, and
fall back to a full re-login with the stored password if refresh fails.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    APP_BUILD,
    EP_MAP,
    EP_TOKEN,
    EP_TOKEN_REFRESH,
    WHITE_LABEL_ID,
    ep_settings,
)
from .models import CosmoDevice, CosmoSettings, normalize_device, normalize_settings

_LOGGER = logging.getLogger(__name__)


class CosmoAuthError(Exception):
    """Invalid credentials / unrecoverable auth failure."""


class CosmoApiError(Exception):
    """Non-auth API failure."""


def _utc_offset_hours() -> int:
    """Local UTC offset in hours for the x-accept-offset header."""
    off = datetime.now(timezone.utc).astimezone().utcoffset()
    return int(off.total_seconds() // 3600) if off else 0


class CosmoClient:
    """Holds tokens and talks to the FiLIP API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._exp: datetime | None = None

    # --- auth -----------------------------------------------------------------

    async def login(self) -> None:
        data = await self._request(
            "POST",
            EP_TOKEN,
            json={
                "appBuild": APP_BUILD,
                "email": self._email,
                "password": self._password,
                "whiteLabelId": WHITE_LABEL_ID,
            },
            auth=False,
        )
        self._store_tokens(data)

    async def _refresh_token(self) -> None:
        if not self._refresh:
            await self.login()
            return
        try:
            data = await self._request(
                "POST", EP_TOKEN_REFRESH, json={"refreshToken": self._refresh}, auth=False
            )
            self._store_tokens(data)
        except (CosmoAuthError, CosmoApiError):
            # Refresh chain broke — re-login from scratch.
            await self.login()

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        self._access = data.get("accessToken")
        self._refresh = data.get("refreshToken")
        exp = data.get("expDate")
        if exp:
            try:
                parsed_expiry = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as err:
                raise CosmoAuthError("auth response contained an invalid expiry") from err
            if parsed_expiry.tzinfo is None:
                parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
            self._exp = parsed_expiry
        if not self._access:
            raise CosmoAuthError("login/refresh returned no accessToken")

    async def _ensure_token(self) -> None:
        from .const import TOKEN_REFRESH_MARGIN

        if self._access is None:
            await self.login()
            return
        if self._exp and datetime.now(timezone.utc) >= self._exp - TOKEN_REFRESH_MARGIN:
            await self._refresh_token()

    # --- data -----------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """All watches on the account with their last-known location/battery.
        Returns raw for internal, normalized preferred via get_device.
        """
        data = await self._request("GET", EP_MAP)
        body = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(body, dict):
            raise CosmoApiError("map response schema invalid")
        devices = body.get("Devices")
        if not isinstance(devices, list) or not all(
            isinstance(device, dict) for device in devices
        ):
            raise CosmoApiError("map response schema invalid")
        return devices

    async def get_device(self, device_id: int | str) -> CosmoDevice | None:
        """Return normalized device model (or None)."""
        for d in await self.get_devices():
            if str(d.get("id")) == str(device_id):
                return normalize_device(d)
        return None

    async def get_settings(self, device_id: int | str) -> CosmoSettings:
        """Read current settings via GET /v2/settings. Does not swallow auth/api errors.

        Critical: validates presence and type of activeTrackingEnable on readback.
        Schema-invalid (None after normalize) -> CosmoApiError (fail closed, classified).
        """
        data = await self._request("GET", ep_settings(device_id))
        body = data.get("data", data) if isinstance(data, dict) else {}
        settings = normalize_settings(body)
        if settings.active_tracking_enable is None:
            # schema drift or missing critical field -> explicit classified error
            raise CosmoApiError("settings readback missing/invalid activeTrackingEnable")
        return settings

    async def set_active_tracking(
        self, device_id: int | str, enable: bool, duration: int, frequency: int
    ) -> None:
        """Turbo mode: wake the watch to report frequently (or stop)."""
        body: dict[str, Any] = {
            "activeTrackingDuration": duration,
            "activeTrackingEnable": enable,
            "activeTrackingFrequency": frequency,
        }
        await self._request("PUT", ep_settings(device_id), json=body)

    # --- transport ------------------------------------------------------------

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        """Return an endpoint label without account/device identifiers."""
        endpoint = url.removeprefix(API_BASE)
        if endpoint.startswith("/settings/"):
            return "/settings/<device>"
        return endpoint

    async def _request(
        self, method: str, url: str, *, json: Any = None, auth: bool = True
    ) -> Any:
        if auth:
            await self._ensure_token()
        headers = {
            "x-accept-version": "1.0",
            "x-accept-offset": str(_utc_offset_hours()),
            "Content-Type": "application/json; charset=UTF-8",
        }
        if auth and self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        try:
            async with self._session.request(method, url, json=json, headers=headers) as resp:
                text = await resp.text()
                endpoint = self._safe_endpoint(url)
                if resp.status in (401, 403):
                    raise CosmoAuthError(f"{method} {endpoint} -> {resp.status}")
                if resp.status >= 400:
                    # Never include vendor response bodies: they may contain private data.
                    raise CosmoApiError(f"{method} {endpoint} -> {resp.status}")
                body = await resp.json() if text else {}
                # FiLIP signals expired tokens with status 2 in a 200 envelope.
                if isinstance(body, dict) and body.get("status") == 2:
                    raise CosmoAuthError("token expired")
                return body
        except aiohttp.ClientError as err:
            endpoint = self._safe_endpoint(url)
            raise CosmoApiError(
                f"{method} {endpoint} failed: {type(err).__name__}"
            ) from err
