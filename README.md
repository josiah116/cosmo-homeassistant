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
| Location | `device_tracker` | Last-known GPS on the HA map; accuracy from the fix radius. Fix time, phone number, emergency flag as attributes. |
| Request location | `button` | **On-demand live fix** — enables "active tracking" so the watch reports every ~10 s for a few minutes. The only action that wakes the watch. |
| Battery | `sensor` | Watch battery %. |
| Charger battery | `sensor` | Charging cradle/base battery % (diagnostic). |
| Last location fix | `sensor` | Timestamp of the most recent GPS fix. |
| Firmware | `sensor` | Firmware version (diagnostic, disabled by default). |
| SOS / emergency | `binary_sensor` | Watch is in emergency mode. |
| Powered off | `binary_sensor` | Watch has been shut down. |

### Location history
Home Assistant's recorder logs every location update, giving you a **history
trail / timeline the COSMO app itself doesn't offer**. Add a Map card with a
history path to see where the watch has been.

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

## Design: scheduled reads never wake the watch

The two-minute scheduled poll only reads `/v2/map` — COSMO's **server cache**
(last-known location/battery) — which never contacts the watch. The watch is woken only when
you press **Request location** (or call the service), so you decide when to spend
its battery on a live fix.

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

The entity IDs, friendly names, and location history stay put — only the
underlying watch changes.

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
