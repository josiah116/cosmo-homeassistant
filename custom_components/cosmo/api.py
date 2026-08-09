"""Client for the FiLIP (api.myfilip.com) backend behind COSMO.

Auth: email + password -> POST /v2/token -> {accessToken, refreshToken,
expDate}. Access tokens are short-lived; renew via POST /v2/token/refresh, and
fall back to a full re-login with the stored password if refresh fails.

v0.5.3: process-local account-scoped rate governor. All login/refresh/map/settings/commands
serialized per normalized account (SHA-256 digest key only). 429 increments streak
with 5/15/30/60m local + jitter; effective = max(server, local) + jitter (server >60m
never shortened). Only valid /map schema success resets rate streak. Local gate
raises CosmoRateLimitError (privacy safe, no body/headers/ids/payloads/urls/creds).
Cancellation propagates. No disk. Test-only reset helper.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

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

T = TypeVar("T")


class CosmoAuthError(Exception):
    """Authentication failure (401/403 or explicit token expired)."""



class CosmoApiError(Exception):
    """Non-auth API or transport error."""



class CosmoRateLimitError(CosmoApiError):
    """Rate limit (429). retry_after_seconds is effective delay (int seconds).

    Privacy-safe: NEVER includes error body, raw headers (except parsed RA for calc),
    account/device values, URL identifiers, credentials, or response payload.
    Message uses only safe endpoint label.
    """

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


# --- process-local account governor (strong, not GC'able across reloads) ---

_RATE_LIMIT_GOVERNORS: dict[str, _AccountRateGovernor] = {}


def _account_digest(email: str) -> str:
    """Unlogged SHA-256 of normalized (lower, stripped) account. Never expose."""
    norm = (email or "").strip().lower().encode("utf-8")
    return hashlib.sha256(norm).hexdigest()


def _get_governor(email: str) -> _AccountRateGovernor:
    key = _account_digest(email)
    if key not in _RATE_LIMIT_GOVERNORS:
        _RATE_LIMIT_GOVERNORS[key] = _AccountRateGovernor()
    return _RATE_LIMIT_GOVERNORS[key]


def _reset_governors_for_testing() -> None:
    """Test-only helper: clear strong registry. Cold restart may probe once.
    After first 429, all same-account paths gate locally. Do not use in prod.
    """
    _RATE_LIMIT_GOVERNORS.clear()


class _AsyncReentrantLock:
    """Async reentrant lock for same-task re-acquire during nested auth calls."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth: int = 0

    async def acquire(self) -> None:
        current = asyncio.current_task()
        if self._owner is current:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = current
        self._depth = 1

    def release(self) -> None:
        if self._depth > 0:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._lock.release()

    @asynccontextmanager
    async def __call__(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()


class _AccountRateGovernor:
    """Per-account (digest keyed) governor. Serializes, gates cooldown, 429 handling."""

    def __init__(self) -> None:
        self._rlock = _AsyncReentrantLock()
        self._streak: int = 0
        self._cooldown_until: float | None = None  # monotonic seconds; inf is allowed
        self._cooldown_started_at: float | None = None
        self._cooldown_seconds: int | None = None

    @asynccontextmanager
    async def serialized(self):
        """Fail fast, serialize, then recheck cooldown after lock acquisition."""
        self._recheck_cooldown_and_raise()
        await self._rlock.acquire()
        try:
            self._recheck_cooldown_and_raise()
            yield
        finally:
            self._rlock.release()

    async def _apply_inter_request_spacing(self) -> None:
        """Small cancellable spacing to prevent same-account request bursts."""
        await asyncio.sleep(0.08)

    def _recheck_cooldown_and_raise(self) -> None:
        if self._cooldown_until is not None:
            now = time.monotonic()
            if now < self._cooldown_until:
                if (
                    math.isinf(self._cooldown_until)
                    and self._cooldown_started_at is not None
                    and self._cooldown_seconds is not None
                ):
                    elapsed = max(0, int(now - self._cooldown_started_at))
                    remaining = max(1, self._cooldown_seconds - elapsed)
                else:
                    remaining = max(1, int(self._cooldown_until - now + 0.999))
                raise CosmoRateLimitError(
                    "account rate-limited (local gate)",
                    retry_after_seconds=remaining,
                )
            self._clear_cooldown()

    def _clear_cooldown(self) -> None:
        self._cooldown_until = None
        self._cooldown_started_at = None
        self._cooldown_seconds = None

    def note_429(self, server_retry_seconds: int | None) -> int:
        """Atomic inc streak, compute eff = max(server, local_seq) + non-neg jitter.
        Local seq: 5/15/30/60 min. Server >60m never shortened. Return eff secs.
        """
        self._streak += 1
        local = self._local_delay(self._streak)
        server = server_retry_seconds if server_retry_seconds and server_retry_seconds > 0 else 0
        eff = max(server, local)
        jitter = 1 + int(random.random() * 30)  # 1-30s bounded, always positive
        eff += jitter
        now = time.monotonic()
        self._cooldown_started_at = now
        self._cooldown_seconds = eff
        try:
            self._cooldown_until = now + eff
        except OverflowError:
            # Preserve the full integer server floor; infinity is only a float sentinel.
            self._cooldown_until = math.inf
        return eff

    def _local_delay(self, streak: int) -> int:
        seq = [300, 900, 1800, 3600]  # 5,15,30,60 min
        idx = min(streak - 1, len(seq) - 1)
        return seq[idx]

    def note_success(self, *, is_valid_map: bool = False) -> None:
        """Only schema-valid /map resets rate streak/cooldown.
        Normal login/settings/command success MUST NOT reset.
        """
        if is_valid_map:
            self._streak = 0
            self._clear_cooldown()

    def _local_delay_for_test(self, streak: int) -> int:
        """Exposed for deterministic test calc (no side effect)."""
        return self._local_delay(streak)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse Retry-After as delta-seconds or HTTP-date. Return non-neg int or None.
    Never logs value or includes in error objects.
    """
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    # delta seconds
    try:
        secs = int(v)
        if secs >= 0:
            return secs
    except (ValueError, TypeError):
        pass
    # HTTP-date
    try:
        dt = parsedate_to_datetime(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = (dt - now).total_seconds()
        if delta > 0:
            return int(delta + 0.999)
        return 0
    except (OverflowError, TypeError, ValueError):
        return None


class CosmoClient:
    """Holds tokens and talks to the FiLIP API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._exp: datetime | None = None

    def _get_governor(self) -> _AccountRateGovernor:
        return _get_governor(self._email)

    @property
    def rate_limit_streak(self) -> int:
        """Return the privacy-safe account rate-limit streak count."""
        return self._get_governor()._streak

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        """Return endpoint label without account/device/URL identifiers."""
        if EP_MAP in url or url.rstrip("/").endswith("/map"):
            return "/map"
        if "/settings/" in url:
            return "/settings/<device>"
        if EP_TOKEN in url and "refresh" not in url:
            return "/token"
        if EP_TOKEN_REFRESH in url:
            return "/token/refresh"
        return "endpoint"

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
        """Fall back to login ONLY on CosmoAuthError.
        Rate/generic transport failures propagate (no amplified retry).
        """
        if not self._refresh:
            await self.login()
            return
        try:
            data = await self._request(
                "POST", EP_TOKEN_REFRESH, json={"refreshToken": self._refresh}, auth=False
            )
            self._store_tokens(data)
        except CosmoAuthError:
            await self.login()
        # rate or CosmoApiError (incl transport) propagate as-is

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
        """All watches... Only valid schema /map resets shared rate streak."""
        data = await self._request("GET", EP_MAP)
        body = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(body, dict):
            raise CosmoApiError("map response schema invalid")
        devices = body.get("Devices")
        if not isinstance(devices, list) or not all(
            isinstance(device, dict) for device in devices
        ):
            raise CosmoApiError("map response schema invalid")
        # schema-valid /map success -> reset rate streak (client governor)
        gov = self._get_governor()
        gov.note_success(is_valid_map=True)
        return devices

    async def get_device(self, device_id: int | str) -> CosmoDevice | None:
        for d in await self.get_devices():
            if str(d.get("id")) == str(device_id):
                return normalize_device(d)
        return None

    async def get_settings(self, device_id: int | str) -> CosmoSettings:
        data = await self._request("GET", ep_settings(device_id))
        body = data.get("data", data) if isinstance(data, dict) else {}
        settings = normalize_settings(body)
        if settings.active_tracking_enable is None:
            raise CosmoApiError("settings readback missing/invalid activeTrackingEnable")
        return settings

    async def set_active_tracking(
        self, device_id: int | str, enable: bool, duration: int, frequency: int
    ) -> None:
        body: dict[str, Any] = {
            "activeTrackingDuration": duration,
            "activeTrackingEnable": enable,
            "activeTrackingFrequency": frequency,
        }
        await self._request("PUT", ep_settings(device_id), json=body)

    # --- transport (governed, privacy safe) -----------------------------------

    async def _request(
        self, method: str, url: str, *, json: Any = None, auth: bool = True
    ) -> Any:
        gov = self._get_governor()
        async with gov.serialized():  # type: ignore[attr-defined]
            if auth:
                await self._ensure_token()
            await gov._apply_inter_request_spacing()
            headers = {
                "x-accept-version": "1.0",
                "x-accept-offset": str(_utc_offset_hours()),
                "Content-Type": "application/json; charset=UTF-8",
            }
            if auth and self._access:
                headers["Authorization"] = f"Bearer {self._access}"
            try:
                async with self._session.request(
                    method, url, json=json, headers=headers
                ) as resp:
                    endpoint = self._safe_endpoint(url)
                    if resp.status in (401, 403):
                        # auth precedence
                        raise CosmoAuthError(f"{method} {endpoint} -> {resp.status}")
                    if resp.status == 429:
                        ra = resp.headers.get("Retry-After")
                        server_secs = _parse_retry_after(ra)
                        eff = gov.note_429(server_secs)
                        # raise with effective (no body read, no raw header, no ids)
                        raise CosmoRateLimitError(
                            f"{method} {endpoint} -> 429",
                            retry_after_seconds=eff,
                        )
                    if resp.status >= 400:
                        # NEVER read body for error paths
                        raise CosmoApiError(f"{method} {endpoint} -> {resp.status}")
                    # success only: read payload
                    text = await resp.text()
                    body = await resp.json() if text else {}
                    if isinstance(body, dict) and body.get("status") == 2:
                        raise CosmoAuthError("token expired")
                    return body
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                endpoint = self._safe_endpoint(url)
                raise CosmoApiError(
                    f"{method} {endpoint} failed: {type(err).__name__}"
                ) from err
            except asyncio.CancelledError:
                raise


def _utc_offset_hours() -> int:
    off = datetime.now(timezone.utc).astimezone().utcoffset()
    return int(off.total_seconds() // 3600) if off else 0
