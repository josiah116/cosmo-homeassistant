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
            self._exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
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
        return body.get("Devices", []) or []

    async def get_device(self, device_id: int | str) -> CosmoDevice | None:
        """Return normalized device model (or None)."""
        for d in await self.get_devices():
            if str(d.get("id")) == str(device_id):
                return normalize_device(d)
        return None

    async def get_settings(self, device_id: int | str) -> CosmoSettings | None:
        """Optional: read current settings (rate-safe use only, e.g. after command)."""
        try:
            data = await self._request("GET", ep_settings(device_id))
            body = data.get("data", data) if isinstance(data, dict) else {}
            return normalize_settings(body)
        except Exception:
            # fail closed, do not raise for optional read
            return None

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
                if resp.status in (401, 403):
                    raise CosmoAuthError(f"{method} {url} -> {resp.status}")
                if resp.status >= 400:
                    raise CosmoApiError(f"{method} {url} -> {resp.status}: {text[:200]}")
                body = await resp.json() if text else {}
                # FiLIP signals expired tokens with status 2 in a 200 envelope.
                if isinstance(body, dict) and body.get("status") == 2:
                    raise CosmoAuthError(body.get("message", "token expired"))
                return body
        except aiohttp.ClientError as err:
            raise CosmoApiError(f"{method} {url} failed: {err}") from err
