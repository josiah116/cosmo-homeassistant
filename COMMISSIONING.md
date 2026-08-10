# JrTrack 5 commissioning runbook

This runbook records the remaining Home Assistant work after the physical watch
arrives and is activated. It intentionally contains no credentials, addresses,
or private coordinates.

## Pre-arrival state

- The privacy-hardened integration release `v0.4.2` is installed through HACS.
- Home Assistant has loaded the custom integration successfully.
- No COSMO config entry or watch entities exist yet because the account does not
  yet contain an activated watch.
- The Home Assistant **Locations** dashboard exists at
  `/family-locations/family`.
- The dashboard currently shows current family locations only (no map trails),
  family zones, stateful find-phone controls for Josiah and Sara, and a JrTrack
  pairing/readiness section.
- Public reverse geocoding is disabled. Exact coordinates are not sent to
  Nominatim or another third-party geocoder by this integration.

## Activation and pairing

1. Activate and fully provision the JrTrack 5 in COSMO Mission Control.
2. Confirm that the watch appears and reports a current location in the COSMO
   app.
3. In Home Assistant, open **Locations → Pair JrTrack 5**.
4. Enter COSMO credentials directly into Home Assistant; never paste them into
   chat, logs, issues, or this repository.
5. Select the activated watch and complete the config flow.
6. Record the generated entity IDs locally at commissioning time. Do not guess
   them in advance.

## Entity and dashboard commissioning

1. Verify the watch creates the expected entities:
   - GPS `device_tracker`
   - Request-location control
   - Watch battery sensor
   - Last location-fix timestamp
   - SOS/emergency binary sensor
   - Powered-off binary sensor
   - Active Tracking, cloud-health, and command diagnostics
2. Associate the watch tracker with `person.asher` in Home Assistant.
3. Confirm `person.asher` changes location from the watch tracker.
4. Replace the dashboard readiness cards with live watch status and controls.
5. Preserve the map's current-location-only behavior.
6. Exercise **Request location** once and verify an updated fix reaches HA.
7. For a current-location-only deployment, exclude both the generated watch
   `device_tracker` and `person.asher` from Home Assistant Recorder. If either
   was already recorded, run `recorder.purge_entities` for those two entities.
   The live map, zones, and automations continue to work without Recorder.

## School arrival and departure notifications

The current integration reads COSMO's `/v2/map` server cache every ten minutes.
It does **not** import COSMO Mission Control push notifications. Home Assistant
must derive school arrival/departure from the watch location.

After pairing:

1. Create `zone.school` in Home Assistant without committing its coordinates to
   this public repository.
2. Build HA-native arrival and departure automations for `person.asher`.
3. Notify both verified mobile-app targets:
   - `notify.mobile_app_josiah_s26_ultra`
   - `notify.mobile_app_sara_sm_s926u1`
4. Add short dwell/debounce handling to suppress GPS boundary chatter.
5. Limit notifications to school days and appropriate hours. Prefer a real
   school calendar when available; otherwise use a weekday schedule helper.
   Gate monitoring to the active school year and suppress district events named
   `School Closed:` or `No School:`.
6. Require a recent watch location fix before accepting a zone transition.
7. Suppress duplicate arrival/departure notifications.
8. Add an actionable stale-location warning during relevant hours when the
   watch has stopped reporting.
9. Keep COSMO's native safe-zone alerts enabled during initial burn-in and
   compare timing/reliability before relying on HA alone.

## Expected timing and battery behavior

- HA polls COSMO's cached watch state every ten minutes.
- Backup alert latency is normally up to ten minutes but can be longer when the watch has
  not uploaded a fresh fix.
- **Request location** enables high-frequency active tracking and should remain
  an on-demand control rather than a scheduled action because it consumes watch
  battery. Release `v0.4.2` stops turbo early after a new sufficiently accurate
  fix; COSMO's five-minute duration remains the fallback timeout.

## End-to-end acceptance checks

- Current watch location is plausible and timestamped recently.
- An on-demand location request produces a new fix.
- School entry generates exactly one arrival notification on both phones.
- School exit generates exactly one departure notification on both phones.
- Brief GPS boundary movement does not create duplicate alerts.
- Powered-off and SOS state changes are represented correctly.
- A stale-location condition produces only an actionable alert.
- COSMO native alerts remain available during burn-in.
- Recorder history returns no retained rows for the watch tracker or linked
  person after exclusions are active and the one-time purge has completed.

## Rollback

- Disable the school automations first if location behavior is noisy.
- Remove the watch tracker from `person.asher` without deleting the person.
- Remove the COSMO config entry to stop API polling.
- Uninstall the HACS integration and restart HA only if full removal is needed.
- The separate Locations dashboard can remain or be deleted independently.


## v0.5.0 Phase 0 notes
- New diagnostic sensors and Stop button added. Active tracking now has explicit stop and cooldown. Use Request location only when needed. Diagnostics available under integration for redacted health.

## v0.5.2 cleanup notes

- Active Tracking is initialized with one authoritative settings read at startup
  only when the initial map response omits its state. Normal polling remains
  map-only and does not wake the watch.
- Unsupported charger-battery and stale firmware entities are removed. They must
  not be restored to dashboards or automations without validated vendor data.
