# Cosmo Kids Watch — Home Assistant integration

Privacy-hardened fork of the unofficial Home Assistant integration for the
[COSMO JrTrack](https://cosmotogether.com/) kids smartwatch. COSMO uses the
**FiLIP** platform, so this talks to the same backend as the official COSMO app
(`api.myfilip.com`).

> Not affiliated with or endorsed by COSMO Together / FiLIP. Uses a private API
> that may change or break at any time. Use with your own account, at your own risk.

## What you get

A device per watch, with:

| Entity | Type | Notes |
|---|---|---|
| Location | `device_tracker` | Last-known GPS on the HA map; accuracy from the fix radius. Sensitive watch/account fields are not exposed as attributes. |
| Request location | `button` | **On-demand live fix** — enables "active tracking" so the watch reports every ~10 s. HA stops turbo after a new accurate fix; COSMO's timeout remains the fallback. The only action that wakes the watch. |
| Stop active tracking | `button` | Explicitly ends a user-started Active Tracking session. |
| Battery | `sensor` | Watch battery %. |
| Last location fix | `sensor` | Timestamp of the most recent GPS fix. |
| SOS / emergency | `binary_sensor` | Watch is in emergency mode. |
| Powered off | `binary_sensor` | Watch has been shut down. |

### Location privacy and Recorder

Home Assistant Recorder stores GPS state by default. For a current-location-only
deployment, explicitly exclude both the watch tracker and its linked person from
Recorder. Replace the example IDs with the entities created on your system:

```yaml
recorder:
  exclude:
    entities:
      - device_tracker.child_watch
      - person.child
```

This does not affect the live map, zones, or automations. If those entities were
already recorded, use Home Assistant's `recorder.purge_entities` action once to
remove their existing rows. Deliberately omit these exclusions only when all
authorized users have accepted retaining a child-location trail.

## Commissioning

The pre-arrival state and post-activation checklist are maintained in
[`COMMISSIONING.md`](COMMISSIONING.md). It covers pairing, dashboard completion,
school arrival/departure notifications, acceptance tests, and rollback without
storing credentials or private coordinates.

## Privacy hardening

This fork deliberately removes the upstream public Nominatim reverse-geocoding
request. Exact child-location coordinates stay between COSMO, Home Assistant,
and the clients you authorize; the integration does not forward each fix to a
third-party geocoder. Home Assistant zones provide local, user-controlled place
labels for dashboards and automations.

### v0.4.2 attribute compatibility

Version 0.4.2 removes the tracker attributes `gps_date`, `phone_number`
(the backend's `gsmNumber`), and `emergency_mode`. This is an intentional
privacy and deduplication change. Update existing templates to use the dedicated
**Last location fix** sensor and **SOS / emergency** binary sensor instead.


## v0.5.1 Active Tracking state hotfix

- Preserves validated Active Tracking state when the cached map payload omits
  its optional tracking field.
- Keeps a confirmed stop stable and keeps a confirmed start visible for the
  bounded five-minute vendor duration, so the Stop control does not disappear
  during an active session.
- Explicit boolean map state still regains authority; an expired start fails
  closed to unknown rather than inventing an off state.

## v0.5.2 Startup Active Tracking initialization and entity cleanup

- At startup (after the initial `/v2/map` poll), perform a *one-time* `/v2/settings`
  read **only if** the Active Tracking state is still unknown (map payload often
  omits the optional field). This gives the `binary_sensor.xxx_active_tracking`
  a current value immediately on HA start/restart instead of leaving it unknown.
  Authentication failures enter native Home Assistant reauthentication; transient
  API or malformed-value failures remain unknown without blocking map updates.
- Removes only the unsupported charger-battery and stale firmware sensor
  registrations. No broad registry sweep, device deletion, or supported-entity
  identity change occurs.
- Startup `true` remains bounded to COSMO's five-minute session lifetime; a
  confirmed `false` remains stable when the map payload omits the field.
- Tests cover startup authority, error classification, bounded state, polling,
  and exact registry cleanup.
- Version and docs updated.

## v0.5.0 Phase 0 foundation (reliability, privacy, diagnostics)

- Normalized response models (dataclasses) at API/entity boundaries; tolerant of missing fields; no silent bad location data.
- Full pytest suite with sanitized mocks (no real creds, ids, coords in tests).
- Home Assistant diagnostics (redacted only: version, health, last poll, error class, capabilities; zero PII/coords/IMEI).
- Removed IMEI/serial from DeviceInfo + safe migration to clear legacy serial metadata (no value logged, no device delete).
- New diagnostic/operational entities:
  - Cloud reachability
  - Last successful cloud poll (timestamp)
  - Location fix age (seconds)
  - Dedicated GPS accuracy (meters)
  - Active Tracking state
  - Last locate command outcome/timestamp
- Hardened Active Tracking controls:
  - Explicit "Stop active tracking" button
  - Cooldown (60s) after locate requests
  - Full-workflow duplicate suppression and command locking
  - Early stop only after a newer fix with accuracy >0m and <=100m
  - Fail-closed on auth, API/rate-limit errors, schema drift, and cancellation
  - Immediate `/v2/settings` readback after commands; cached map state cannot immediately overwrite it
  - No scheduling or auto requests
- Coordinator health timestamps, task-managed background polls, unload safe.
- Updated manifest/version metadata, strings/translations, validation, and CI.

All supported entity IDs, person linkages, and v0.4.x behavior are preserved.

## Design: scheduled reads never wake the watch

The two-minute scheduled poll only reads `/v2/map` — COSMO's **server cache**
(last-known location/battery) — which never contacts the watch. The watch is woken only when
you press **Request location**, so you decide when to spend
its battery on a live fix. Once HA sees a new fix with acceptable accuracy, it
stops turbo early; COSMO's five-minute duration remains the fail-safe timeout.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/josiah116/cosmo-homeassistant`, category **Integration**.
2. Install **Cosmo Kids Watch**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Cosmo Kids Watch**.
4. Enter your COSMO parent-account **email and password**.
5. Pick the watch. Done.

## Multiple watches

One account can track more than one kid's watch. Add the integration again
(**Add Integration → Cosmo Kids Watch**) and sign in the same way — the watch
picker only offers watches that aren't already configured, so you'll land
straight on the new one (or a dropdown if more than one is still unclaimed).

## Replacing a broken watch

If a watch is lost or breaks and you set up a new one in the COSMO app, it
shows up on the account as a new watch (new device ID) — the old integration
entry won't pick it up on its own. Instead of deleting and re-adding (which
loses history), use **reconfigure**:

1. **Settings → Devices & Services → Cosmo Kids Watch** → the entry for that
   kid → ⋮ → **Reconfigure**.
2. Sign in again (refreshes the list of watches on the account).
3. Pick the new watch.

The entity IDs, friendly names, and automations stay put — only the underlying
watch changes. Any Recorder history deliberately enabled for those entities also
stays associated with the same entity IDs.

If your COSMO password changes (e.g. you updated it in the official app), Home
Assistant will detect the auth failure on next poll and surface a **Re-authenticate**
prompt on the integration card. Click **Re-authenticate** and enter the current
email and password to restore the entry. This uses the new dedicated reauth flow
added in v0.5.0 (keeps your device_id and history intact). Valid credentials for
an account that does not contain the configured watch are rejected without
changing the entry.

## How auth works

Email + password → `POST /v2/token` → short-lived access token + refresh token.
The integration renews the access token via `/v2/token/refresh` and falls back to
a full re-login with the stored password if the refresh chain ever breaks — so it
keeps working across restarts without re-prompting.

Use a dedicated COSMO Guardian account where practical. The password is stored in
Home Assistant's protected config-entry storage because the private API requires
it for fallback reauthentication.

## Tracking upstream

The fork keeps `kunalkhosla/cosmo-homeassistant` as the `upstream` remote. A
scheduled GitHub Action reports when upstream changes are available, and
`scripts/sync-upstream.sh` performs a reviewed merge plus local validation. See
[`UPSTREAM.md`](UPSTREAM.md) for the exact update and release procedure.

## License

MIT
