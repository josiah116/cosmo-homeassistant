# COSMO Mission Control replacement roadmap

## Objective

Make Home Assistant the normal control plane for the JrTrack 5 so Mission
Control is not needed on daily-use phones. Keep a protected break-glass path
for pairing, guardian/account changes, billing, vendor support, and any API
whose contract is not proven.

The current-location-only privacy model remains mandatory: no trails, public
reverse geocoding, message content, call history, or unnecessary identifiers.

## Current production baseline

The `cosmo` integration supplies current GPS and accuracy, watch battery,
last-fix timestamp, SOS, powered-off state, and an on-demand location request
that stops early after an accurate fix. The HA layer adds Home,
Pinewoods, Bus Stop, and Clubhouse arrival/departure alerts; school calendar and
schedule gates; a 30-minute stale-fix guard; 20% low/10% critical battery alerts
with recovery; independent dual-parent delivery; and an indefinite routine-alert
pause with per-alert latches. The pause never suppresses safety alerts. A two-
minute `/v2/map` poll reads COSMO's server cache; only on-demand Active Tracking
commands the watch.

## Capability decisions

| Capability | Decision |
|---|---|
| Current location, zones, battery, SOS, power | Keep in HA; production baseline |
| Integration/cloud/watch health | Add first; required to diagnose failures |
| Timed notification pause | Add; auto-resume prevents forgotten pauses |
| Dynamic monitored zones | Add using HA labels instead of hardcoded branches |
| School calendar and alert window | Keep in HA; no COSMO API required |
| School-day readiness and charge reminder | Add; notify only on actionable faults |
| Active Tracking state/stop/result | Add with cooldown and explicit confirmation |
| GPS accuracy and fix-age diagnostics | Add as dedicated entities |
| Focus/Lockdown schedule | High-value guarded API write after capture/readback |
| Alarms/reminders | High-value guarded CRUD after API capture |
| Find My Watch | Add confirmed button after endpoint validation |
| Steps and daily goal | Add read-only before considering goal writes |
| Tracking interval/active window | Read first; later guarded profiles with auto-revert |
| Approved contacts/Guardians | Read-only inventory first; writes remain break-glass |
| Preset messages | Optional guarded CRUD; do not store message history |
| SafeZones | HA remains authoritative; native zones retained as backup |
| Location history | Deliberately excluded |
| Calls/text/media | Use normal phone/SMS; do not proxy through HA |
| App Station, usage limits, billing | Use COSMO web portal, not HA |
| Pairing, guardian invites, recovery | Retain vendor break-glass path |
| Firmware | Vendor-owned; expose no sensor until the API proves a live value |

## Phase 0 — harden the integration

1. Add mocked API fixtures and tests for authentication, token refresh,
   coordinator errors, unchanged payloads, locate success/timeout/early-stop,
   unload cancellation, entity parsing, and config-entry migration.
   Replace untyped payload access with validated response models so vendor field
   drift becomes an explicit unavailable/error state rather than silent bad data.
2. Add Home Assistant diagnostics support and a redacted export with endpoint
   status, payload schema keys, last successful poll, and error class—never
   credentials, coordinates, phone
   numbers, IMEI, or message/call data.
3. Remove the IMEI assignment from `CosmoEntity.device_info` and clear existing
   device-registry serial metadata without recording the identifier. Clean stale
   firmware, unsupported charger-battery, and old post-migration registry entries.
4. Add entities for cloud reachability, last successful poll, fix age, GPS
   accuracy, Active Tracking, and last locate-command outcome.
5. Add request cooldown, duplicate suppression, explicit Stop Tracking control,
   and readback after every write.

**Exit:** tests pass, diagnostics are redacted, all health entities and an
explicit Stop Tracking control are live, failures are distinguishable, and no
command can silently remain active.

## Phase 1 — high-value HA quality of life

1. Extend the existing `input_boolean.pause_asher_location_notifications` and
   tag-clearing automation with 30-minute, one-hour, until-tomorrow, and Resume
   controls. Preserve its exact clearing behavior while keeping SOS, power,
   battery, polling, zone state, and manual locate unpausable.
2. Select monitored zones by an HA label such as `Asher location alert`; one
   generic automation handles additions without YAML duplication.
3. Add a school-morning readiness check that stays silent unless the watch is
   powered off, stale, unavailable, or unlikely to last the school day.
4. Add an evening charge reminder only when tomorrow is a school day and
   battery is below a learned threshold.
5. Enhance the existing 30-minute stale-location guard and Open Locations action
   with Request Fresh Location. The locate action must have a cooldown and remain
   user-initiated.
6. Add an SOS acknowledgement/escalation workflow so both parents can see that
   one has acknowledged; never auto-call emergency services.
7. Rework `/family-locations/family` into Status, Safety, Location Controls,
   School, and Diagnostics sections, with normal state concise and faults prominent.
8. Remove routine notification copy that tells parents to open Mission Control;
   point to Home Assistant and identify the vendor app only as break-glass.
9. Add school-calendar convention validation so renamed first-day, last-day, or
   closure events create one actionable configuration fault instead of silently
   disabling school gates.

**Exit:** after burn-in, the app is unnecessary for normal location, alert,
battery, and school-day operations. Full removal still waits for the exit
criteria below.

## Phase 2 — passive API discovery and read-only parity

COSMO publishes no supported developer API or webhook contract. Treat this
phase as experimental and fail closed if contracts cannot be proven. Capture
Mission Control traffic only through an authorized guardian session on a
dedicated test device/session. Do not blindly mutate production settings or use
the watch as an endpoint-discovery target. Record endpoint, method, redacted
request/response schema, idempotency behavior, and readback source for:

- Focus/Lockdown schedules
- Alarms/reminders
- Find My Watch
- Steps/goals
- Tracking interval and active window
- Contact/Guardian inventory
- Preset-message inventory
- Native SafeZones
- App/update status

Convert captures into sanitized fixtures and mock tests. Expose read-only HA
entities first and compare against Mission Control for at least one week.

## Phase 3 — guarded controls

Implement writes in this order: Find My Watch; Focus/Lockdown; alarms/reminders;
tracking profiles; preset messages. Every write requires schema validation,
confirmation for battery/noise-affecting actions, idempotency, immediate
readback, rollback, audit logging without sensitive payloads, and a supported/
available state that fails closed on 4xx responses or schema drift.

Contact/Guardian writes remain out of routine HA control unless a separate
review proves backup, validation, rollback, and recovery. App installs, billing,
and communications remain vendor/web/phone functions.

## Safety invariants

- Routine pause never stops tracking, SOS, power, or battery monitoring.
- Timed-pause replacements and all future controls must preserve the existing
  routine-versus-safety separation and notification-tag clearing behavior.
- Active Tracking is never scheduled continuously.
- No automatic 911 call, contact removal, factory reset, firmware update, or
  destructive account operation.
- One failed parent notification never blocks the other.
- No exact coordinates, credentials, phone numbers, IMEI, or message content in
  diagnostics, logs, issues, or repository fixtures.
- No location trails, message history, or call content in HA; Recorder exclusions
  for the tracker and person remain mandatory and verified.
- COSMO remains the system of record for emergency protocol and account access.
- SOS live-tracking, listen-in/call behavior, and the optional 911 setting remain
  vendor-owned even when Home Assistant mirrors and escalates the SOS state.

## App-removal exit criteria

Mission Control may be removed from daily phones only after:

1. Phase 0 health/diagnostics and Phase 1 controls run for at least seven days
   without regression.
2. Real end-to-end tests pass for all four zones; 30-minute stale/fresh; 20% and
   10% battery plus recovery; powered-off/recovery; and coordinated SOS with
   acknowledgement visible to both parents and no automatic 911 action.
3. Guardian/contact read-only state is verified.
4. App re-login, pairing, billing, and vendor-support break-glass procedures are
   documented and tested.
5. At least one full school week completes with no Mission Control use for
   routine operations.
6. Recorder exclusions and purge are verified with zero retained tracker/person
   rows.

During burn-in, compare HA arrival, departure, and SOS timing against COSMO's
native SafeZone/emergency behavior; do not remove the fallback until HA is at
least as operationally reliable.

## Risks and boundaries

- `api.myfilip.com` is an unofficial FiLIP/COSMO API with no compatibility
  guarantee; pairing, guardian changes, billing, firmware, and unproven writes
  always retain a full vendor break-glass path.
- COSMO may retain location and account data in its cloud even though HA retains
  only current location.
- Location trails, reverse geocoding, and message/call proxying are intentionally
  excluded from HA.
- Active Tracking remains on-demand, cooldown-protected, and early-stopped; it is
  never continuously scheduled.

## Official evidence

- JrTrack 5 guide: https://cosmotogether.com/pages/jrtrack-5-user-guide
- Battery guidance: https://cosmotogether.com/pages/battery-life
- Product updates: https://cosmotogether.com/pages/updates
