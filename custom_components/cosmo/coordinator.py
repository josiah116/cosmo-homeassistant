"""Data coordinator: polls /v2/map for the watch's last-known state."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CosmoApiError, CosmoAuthError, CosmoClient, CosmoRateLimitError
from .const import (
    ACTIVE_TRACKING_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SCAN_INTERVAL_AWAY,
    SCAN_INTERVAL_BASELINE,
    SCAN_INTERVAL_HOME,
    SCAN_INTERVAL_TRUSTED,
)
from .models import CosmoDevice

_SETTINGS_READBACK_MAP_GRACE = timedelta(minutes=3)
_MAX_RATE_LIMIT_SCHEDULE_SECONDS = 31_536_000  # one-year HA scheduling slice
_MAX_ZONE_RADIUS_METERS = 10_000_000_000  # reject absurd finite geometry


class CosmoCoordinator(DataUpdateCoordinator[CosmoDevice | None]):
    """Polls /v2/map (server cache) — never wakes the watch.

    Tracks health timestamps for diagnostics and sensors (cloud reachability,
    last successful poll). Uses always_update=False for unchanged payload behavior.
    Active state initialized conservatively from map data at poll time,
    explicit settings readback after commands, *and* one-time settings read at
    startup (when map omits the optional field). Never polls /settings on the
    2min cycle.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CosmoClient,
        device_id: int | str,
        scan_interval: timedelta,
        *,
        adaptive_enabled: bool = False,
        trusted_zone_entity_ids: list[str] | None = None,
    ) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=scan_interval,
            config_entry=entry,
            # /v2/map returns stable JSON-derived dicts; skip listener callbacks
            # when the server cache has not changed.
            always_update=False,
        )
        self.client = client
        self.entry_id = entry.entry_id
        self.device_id = device_id
        self.adaptive_enabled = adaptive_enabled
        self.trusted_zone_entity_ids = tuple(
            entity_id
            for entity_id in (trusted_zone_entity_ids or ())
            if isinstance(entity_id, str) and entity_id.startswith("zone.")
        )
        self._not_before: float | None = None
        self._generic_backoff_streak = 0
        self._backoff_class: str | None = None
        self._last_fresh_fix_at: datetime | None = None
        self._candidate_zone: str | None = None
        self._candidate_fix_count = 0
        self._cadence_name = "disabled" if not adaptive_enabled else "baseline"
        # Health/diagnostics timestamps (in-memory)
        self.last_successful_poll: datetime | None = None
        self.last_error: Exception | None = None
        self.last_error_class: str | None = None
        # Active tracking control state (updated after commands + readback, or map poll)
        # Starts unknown (None); binary sensor unavailable when unknown/schema-invalid.
        # Initialized once at first poll or explicit readback. Not settings GET every poll.
        self.active_tracking: bool | None = None
        self.last_locate_outcome: str | None = None
        self.last_locate_time: datetime | None = None
        self._locate_lock = asyncio.Lock()  # for duplicate suppression
        self._last_locate_attempt: datetime | None = None
        self._active_readback_protected_until: datetime | None = None
        self._active_tracking_expires_at: datetime | None = None

    async def _async_update_data(self) -> CosmoDevice | None:
        if self._not_before is not None:
            remaining = self._not_before - time.monotonic()
            if remaining > 0:
                raise UpdateFailed(
                    "COSMO cloud poll deferred by backoff",
                    retry_after=remaining,
                )
            self._not_before = None

        try:
            device = await self.client.get_device(self.device_id)
        except CosmoAuthError as err:
            self.last_error = err
            self.last_error_class = "CosmoAuthError"
            raise ConfigEntryAuthFailed(str(err)) from err
        except CosmoRateLimitError as err:
            delay = self._arm_rate_limit(err)
            raise UpdateFailed(
                "COSMO cloud rate limited",
                retry_after=delay,
            ) from err
        except CosmoApiError as err:
            self.last_error = err
            self.last_error_class = "transient"
            delay = self._next_transient_delay()
            self._arm_cooldown(delay, "transient")
            raise UpdateFailed(
                "COSMO cloud request failed",
                retry_after=delay,
            ) from err
        if device is None:
            delay = self._next_transient_delay()
            err = UpdateFailed(
                "configured watch not found on account",
                retry_after=delay,
            )
            self.last_error = err
            self.last_error_class = "transient"
            self._arm_cooldown(delay, "transient")
            raise err
        self.last_successful_poll = datetime.now(timezone.utc)
        self.last_error = None
        self.last_error_class = None
        self._generic_backoff_streak = 0
        self._not_before = None
        self._backoff_class = None
        self._set_adaptive_interval(device)
        self.update_active_from_data(device)  # pass fresh to avoid stale self.data
        # Force listener update for health timestamps (last_successful_poll, location_fix_age)
        # even when device payload equality would suppress under always_update=False.
        # This keeps diagnostic sensors fresh without changing the payload optimization intent.
        self.async_update_listeners()
        return device

    @property
    def cloud_reachable(self) -> bool:
        """True if last coordinator update succeeded (cloud reachability health)."""
        # `_async_update_data` notifies health listeners before the coordinator base
        # updates `last_update_success`; use our own already-updated health fields so
        # recovery renders correctly even when an unchanged payload suppresses the
        # base listener callback.
        return self.last_successful_poll is not None and self.last_error is None

    @property
    def last_poll_age(self) -> int | None:
        """Seconds since last successful *cloud poll* (distinct from GPS fix age)."""
        if not hasattr(self, "last_successful_poll") or self.last_successful_poll is None:
            return None
        return int((datetime.now(timezone.utc) - self.last_successful_poll).total_seconds())

    def _parse_gps_timestamp(self, ts: str | None) -> datetime | None:
        """Parse GPS fix timestamp safely; return None for invalid/missing."""
        if not ts or not isinstance(ts, str):
            return None
        try:
            dt = dt_util.parse_datetime(ts)
            if dt is None or dt.tzinfo is None:
                return None
            return dt
        except (ValueError, TypeError, AttributeError, OverflowError):
            return None

    @property
    def location_fix_age(self) -> int | None:
        """Age (seconds) of the GPS fix from parsed device gps_date.

        Never uses last cloud poll time. Invalid, future or missing -> None.
        Timezone safe.
        """
        if not self.data:
            return None
        gps_ts = getattr(self.data, "gps_date", None)
        fix_dt = self._parse_gps_timestamp(gps_ts)
        if fix_dt is None:
            return None
        now = datetime.now(timezone.utc)
        if fix_dt > now:
            return None
        age = (now - fix_dt).total_seconds()
        if age < 0:
            return None
        return int(age)

    def _arm_cooldown(self, delay: float, backoff_class: str) -> None:
        """Arm a local no-request-before gate for all coordinator refresh paths."""
        safe_delay = max(1.0, float(delay))
        self._not_before = time.monotonic() + safe_delay
        self.update_interval = timedelta(seconds=safe_delay)
        self._backoff_class = backoff_class

    def _arm_rate_limit(self, err: CosmoRateLimitError) -> int:
        """Mirror account cooldown using a finite HA scheduling slice."""
        account_delay = max(300, err.retry_after_seconds or 0)
        schedule_delay = min(account_delay, _MAX_RATE_LIMIT_SCHEDULE_SECONDS)
        self.last_error = err
        self.last_error_class = "rate_limit"
        self._arm_cooldown(schedule_delay, "rate_limit")
        return schedule_delay

    def _next_transient_delay(self) -> int:
        """Return 2/4/8/15-minute transient backoff with non-negative jitter."""
        self._generic_backoff_streak += 1
        bases = (120, 240, 480, 900)
        base = bases[min(self._generic_backoff_streak - 1, len(bases) - 1)]
        return min(900, base + int(random.random() * 30))

    def _set_adaptive_interval(self, device: CosmoDevice) -> None:
        """Select the next successful-poll cadence without storing a trail."""
        if not self.adaptive_enabled:
            self.update_interval = DEFAULT_SCAN_INTERVAL
            self._cadence_name = "disabled"
            return

        fix_at = self._fresh_fix_datetime(device)
        if fix_at is None:
            self._reset_zone_candidate()
            self.update_interval = SCAN_INTERVAL_BASELINE
            self._cadence_name = "baseline"
            return

        if self._last_fresh_fix_at is not None and fix_at < self._last_fresh_fix_at:
            # Out-of-order cloud data is uncertain: keep timestamp high-water but
            # never let pre-uncertainty zone confidence survive.
            self._reset_zone_candidate()
            self.update_interval = SCAN_INTERVAL_BASELINE
            self._cadence_name = "baseline"
            return

        is_distinct = self._last_fresh_fix_at is None or fix_at > self._last_fresh_fix_at
        if is_distinct:
            self._last_fresh_fix_at = fix_at

        zone_class = self._classify_confident_zone(device)
        if zone_class == "unknown":
            self._reset_zone_candidate()
            self.update_interval = SCAN_INTERVAL_BASELINE
            self._cadence_name = "baseline"
            return

        if zone_class == "away":
            self._candidate_zone = None
            self._candidate_fix_count = 0
            self.update_interval = SCAN_INTERVAL_AWAY
            self._cadence_name = "away"
            return

        if zone_class != self._candidate_zone:
            if is_distinct:
                self._candidate_zone = zone_class
                self._candidate_fix_count = 1
            else:
                self._reset_zone_candidate()
        elif is_distinct:
            self._candidate_fix_count += 1

        if self._candidate_fix_count < 2:
            self.update_interval = SCAN_INTERVAL_BASELINE
            self._cadence_name = "baseline"
        elif zone_class == "home":
            self.update_interval = SCAN_INTERVAL_HOME
            self._cadence_name = "home"
        else:
            self.update_interval = SCAN_INTERVAL_TRUSTED
            self._cadence_name = "trusted"

    def _fresh_fix_datetime(self, device: CosmoDevice) -> datetime | None:
        """Validate current-point fields and return a fresh source-fix timestamp."""
        if (
            device.latitude is None
            or device.longitude is None
            or device.radius is None
            or device.radius <= 0
        ):
            return None
        fix_at = self._parse_gps_timestamp(device.gps_date)
        if fix_at is None:
            return None
        now = datetime.now(timezone.utc)
        if fix_at > now or now - fix_at > timedelta(minutes=10):
            return None
        return fix_at

    def _reset_zone_candidate(self) -> None:
        """Clear stabilization without erasing the source-timestamp high-water mark."""
        self._candidate_zone = None
        self._candidate_fix_count = 0

    def _classify_confident_zone(self, device: CosmoDevice) -> str:
        """Classify from the current point and live HA zone geometry only."""
        if device.latitude is None or device.longitude is None or device.radius is None:
            return "unknown"
        device_latitude = float(device.latitude)
        device_longitude = float(device.longitude)
        device_accuracy = float(device.radius)
        home = self.hass.states.get("zone.home")
        home_geometry = self._zone_geometry(home)
        if home_geometry is None:
            return "unknown"

        zones: list[tuple[str, tuple[float, float, float]]] = [
            ("zone.home", home_geometry)
        ]
        for entity_id in sorted(set(self.trusted_zone_entity_ids)):
            if entity_id == "zone.home" or not entity_id.startswith("zone."):
                continue
            geometry = self._zone_geometry(self.hass.states.get(entity_id))
            if geometry is None:
                return "unknown"
            zones.append((entity_id, geometry))

        matches: list[str] = []
        boundary_uncertain = False
        for entity_id, (zone_lat, zone_lon, zone_radius) in zones:
            distance = self._haversine_meters(
                device_latitude,
                device_longitude,
                zone_lat,
                zone_lon,
            )
            if self._uncertainty_fits(distance, device_accuracy, zone_radius):
                matches.append(entity_id)
            elif self._uncertainty_overlaps(distance, device_accuracy, zone_radius):
                boundary_uncertain = True

        if boundary_uncertain or len(matches) > 1:
            return "unknown"
        if matches == ["zone.home"]:
            return "home"
        if matches:
            return f"trusted:{matches[0]}"
        return "away"

    @staticmethod
    def _zone_geometry(state: Any) -> tuple[float, float, float] | None:
        if state is None:
            return None
        if getattr(state, "state", None) in ("unknown", "unavailable"):
            return None
        attributes = getattr(state, "attributes", None)
        if not isinstance(attributes, dict):
            return None
        raw_latitude: Any = attributes.get("latitude")
        raw_longitude: Any = attributes.get("longitude")
        raw_radius: Any = attributes.get("radius")
        if any(
            isinstance(value, bool)
            for value in (raw_latitude, raw_longitude, raw_radius)
        ):
            return None
        try:
            latitude = float(raw_latitude)
            longitude = float(raw_longitude)
            radius = float(raw_radius)
        except (TypeError, ValueError, OverflowError):
            return None
        if not (
            all(math.isfinite(value) for value in (latitude, longitude, radius))
            and -90 <= latitude <= 90
            and -180 <= longitude <= 180
            and 0 < radius < _MAX_ZONE_RADIUS_METERS
        ):
            return None
        return latitude, longitude, radius

    @staticmethod
    def _uncertainty_fits(distance: float, accuracy: float, radius: float) -> bool:
        """Return true only when the positive uncertainty circle fits in the zone."""
        return accuracy > 0 and radius > 0 and distance + accuracy < radius

    @staticmethod
    def _uncertainty_overlaps(distance: float, accuracy: float, radius: float) -> bool:
        """Return true when uncertainty overlaps a boundary, so away is not certain."""
        return accuracy > 0 and radius > 0 and distance - accuracy <= radius

    @staticmethod
    def _haversine_meters(
        latitude_a: float,
        longitude_a: float,
        latitude_b: float,
        longitude_b: float,
    ) -> float:
        earth_radius_m = 6_371_000.0
        lat_a = math.radians(latitude_a)
        lat_b = math.radians(latitude_b)
        delta_lat = math.radians(latitude_b - latitude_a)
        delta_lon = math.radians(longitude_b - longitude_a)
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
        )
        value = min(1.0, max(0.0, value))
        return earth_radius_m * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @property
    def cadence_name(self) -> str:
        return self._cadence_name

    @property
    def backoff_class(self) -> str | None:
        return self._backoff_class

    @property
    def backoff_streak(self) -> int:
        if self._backoff_class == "rate_limit":
            return self.client.rate_limit_streak
        return self._generic_backoff_streak

    @property
    def generic_backoff_streak(self) -> int:
        return self._generic_backoff_streak

    @property
    def trusted_zones_configured(self) -> bool:
        return bool(self.trusted_zone_entity_ids)

    async def async_request_locate(self) -> bool:
        """User-initiated locate: enforce cooldown, no-auto, duplicate suppress, fail closed.

        Command readback uses GET /v2/settings (via client.get_settings) to validate
        active state after PUT. PUT success without validated readback does not claim success.
        """
        from .const import (
            ACTIVE_TRACKING_DURATION,
            ACTIVE_TRACKING_FREQUENCY,
            LOCATE_COOLDOWN,
        )

        async with self._locate_lock:
            now = datetime.now(timezone.utc)
            if self._last_locate_attempt and (now - self._last_locate_attempt) < LOCATE_COOLDOWN:
                self.last_locate_outcome = "cooldown"
                self.last_locate_time = now
                self.async_update_listeners()
                return False

            self._last_locate_attempt = now
            try:
                await self.client.set_active_tracking(
                    self.device_id, enable=True,
                    duration=ACTIVE_TRACKING_DURATION,
                    frequency=ACTIVE_TRACKING_FREQUENCY,
                )
                # Use settings readback (not map) for validated active state post-PUT
                settings = await self.client.get_settings(self.device_id)
                if settings is None or settings.active_tracking_enable is not True:
                    # No validated state readback -> do not claim success
                    self.last_locate_outcome = "error:tracking_not_enabled"
                    self.last_locate_time = now
                    self.active_tracking = (
                        settings.active_tracking_enable if settings is not None else None
                    )
                    self._active_tracking_expires_at = None
                    self.async_update_listeners()
                    return False
                self.active_tracking = settings.active_tracking_enable
                self._active_tracking_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=ACTIVE_TRACKING_DURATION
                )
                self._active_readback_protected_until = (
                    datetime.now(timezone.utc) + _SETTINGS_READBACK_MAP_GRACE
                )
                self.last_locate_outcome = "success"
                self.last_locate_time = now
                self.async_update_listeners()
                # Also refresh map data for location etc (rate ok after command)
                await self.async_request_refresh()
                return True
            except (CosmoApiError, CosmoAuthError) as err:
                if isinstance(err, CosmoRateLimitError):
                    self._arm_rate_limit(err)
                self.last_locate_outcome = f"error:{type(err).__name__}"
                self.last_locate_time = now
                self.active_tracking = None
                self._active_tracking_expires_at = None
                self.async_update_listeners()
                raise
            except asyncio.CancelledError:
                self.last_locate_outcome = "cancelled"
                self.last_locate_time = now
                self.active_tracking = None
                self._active_tracking_expires_at = None
                self.async_update_listeners()
                raise

    async def async_stop_active_tracking(self) -> bool:
        """Explicit stop for active tracking. Fail closed.

        Uses settings readback to validate the stop took effect.
        """
        from .const import ACTIVE_TRACKING_FREQUENCY
        try:
            await self.client.set_active_tracking(
                self.device_id, enable=False, duration=0, frequency=ACTIVE_TRACKING_FREQUENCY
            )
            settings = await self.client.get_settings(self.device_id)
            if settings is None or settings.active_tracking_enable is not False:
                # not validated off
                self.last_locate_outcome = "stop_error:settings_readback_invalid"
                self.last_locate_time = datetime.now(timezone.utc)
                self.active_tracking = None
                self._active_tracking_expires_at = None
                self.async_update_listeners()
                return False
            self.active_tracking = settings.active_tracking_enable
            self._active_tracking_expires_at = None
            self._active_readback_protected_until = (
                datetime.now(timezone.utc) + _SETTINGS_READBACK_MAP_GRACE
            )
            self.last_locate_outcome = "stopped"
            self.last_locate_time = datetime.now(timezone.utc)
            self.async_update_listeners()
            # refresh map too
            await self.async_request_refresh()
            return True
        except (CosmoApiError, CosmoAuthError) as err:
            if isinstance(err, CosmoRateLimitError):
                self._arm_rate_limit(err)
            self.last_locate_outcome = f"stop_error:{type(err).__name__}"
            self.last_locate_time = datetime.now(timezone.utc)
            self.active_tracking = None
            self._active_tracking_expires_at = None
            self.async_update_listeners()
            raise
        except asyncio.CancelledError:
            self.last_locate_outcome = "stop_cancelled"
            self.async_update_listeners()
            raise

    def update_active_from_data(self, device: CosmoDevice | None = None) -> None:
        """Sync active state from provided (or current) poll data.

        Uses map data here; command paths use explicit settings readback.
        Conservative None when unknown.
        A validated command readback temporarily outranks cached map data so the
        immediate refresh cannot undo authoritative command confirmation.
        """
        now = datetime.now(timezone.utc)
        if (
            self._active_readback_protected_until is not None
            and now < self._active_readback_protected_until
        ):
            return
        self._active_readback_protected_until = None
        dev = device or self.data
        if dev:
            val = getattr(dev, "active_tracking_enable", None)
            if val is not None:
                self.active_tracking = val
                if val:
                    from .const import ACTIVE_TRACKING_DURATION

                    self._active_tracking_expires_at = now + timedelta(
                        seconds=ACTIVE_TRACKING_DURATION
                    )
                else:
                    self._active_tracking_expires_at = None
                return

        # Missing map state must not erase a validated command result. Keep a
        # confirmed stop indefinitely; keep a confirmed start only for the
        # bounded vendor duration, then fail closed to unknown.
        if (
            self.active_tracking is True
            and self._active_tracking_expires_at is not None
            and now >= self._active_tracking_expires_at
        ):
            self.active_tracking = None
            self._active_tracking_expires_at = None

    async def async_initialize_active_tracking(self) -> None:
        """One-time Active Tracking state initialization at integration startup.

        The /v2/map payload may omit the optional activeTrackingEnable field
        (see v0.5.1 preserve logic). When the in-memory state is still unknown
        after the initial map poll, perform a single authoritative /v2/settings
        read. Never called on the recurring 2-minute poll cycle. Authentication
        failures start Home Assistant's native reauthentication flow; transient
        API failures and malformed state remain unknown without blocking setup.
        """
        if self.active_tracking is not None:
            return
        try:
            settings = await self.client.get_settings(self.device_id)
        except CosmoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CosmoRateLimitError as err:
            self._arm_rate_limit(err)
            return
        except CosmoApiError:
            return

        if settings is None or not isinstance(settings.active_tracking_enable, bool):
            return

        now = datetime.now(timezone.utc)
        self.active_tracking = settings.active_tracking_enable
        self._active_readback_protected_until = now + _SETTINGS_READBACK_MAP_GRACE
        self._active_tracking_expires_at = (
            now + timedelta(seconds=ACTIVE_TRACKING_DURATION)
            if settings.active_tracking_enable
            else None
        )
        self.async_update_listeners()
