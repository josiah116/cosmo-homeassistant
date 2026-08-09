# COSMO Mission Control replacement roadmap

## Objective

Make Home Assistant the normal control plane for the JrTrack 5 so Mission
Control is not needed on daily-use phones. Keep a protected break-glass path
for pairing, guardian/account changes, billing, vendor support, and any API
whose contract is not proven.

The current-location-only privacy model remains mandatory: no trails, public
reverse geocoding, message content, call history, or unnecessary identifiers.

## Current production baseline

Home Assistant already provides current location, battery, last fix, SOS,
powered-off state, on-demand locate, four-zone arrival/departure alerts,
school-calendar gates, stale-fix detection, low-battery alerts, independent
parent delivery, and a routine-alert pause control. Cached cloud state is read
every two minutes; on-demand Active Tracking is battery-affecting.

## Capability decisions

| Capability | Decision |
|---|---|
| Current location, zones, battery, SOS, power | Keep in HA; production baseline |
| Integration/cloud/watch health | Add first; required to diagnose failures |
| Timed notification pause | Add; auto-resume prevents forgotten pauses |
| Dynamic monitored zones | Add using HA labels instead of hardcoded branches |
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
| Firmware | Monitor version; leave installation vendor/watch-owned |

## Phase 0 — harden the integration

1. Add mocked API fixtures and tests for authentication, token refresh,
   coordinator errors, unchanged payloads, locate success/timeout/early-stop,
   unload cancellation, entity parsing, and config-entry migration.
2. Add a redacted diagnostics export with endpoint status, payload schema keys,
   last successful poll, and error class—never credentials, coordinates, phone
   numbers, IMEI, or message/call data.
3. Remove the IMEI from Home Assistant device-registry metadata and clean the
   stale firmware entity-registry entry.
4. Add entities for cloud reachability, last successful poll, fix age, GPS
   accuracy, Active Tracking, and last locate-command outcome.
5. Add request cooldown, duplicate suppression, explicit Stop Tracking control,
   and readback after every write.

**Exit:** tests pass, diagnostics are redacted, failures are distinguishable,
and no command can silently remain active.

## Phase 1 — high-value HA quality of life

1. Replace indefinite-only pause with 30-minute, one-hour, until-tomorrow, and
   Resume controls while keeping SOS/power/battery unpausable.
2. Select monitored zones by an HA label such as `Asher location alert`; one
   generic automation handles additions without YAML duplication.
3. Add a school-morning readiness check that stays silent unless the watch is
   powered off, stale, unavailable, or unlikely to last the school day.
4. Add an evening charge reminder only when tomorrow is a school day and
   battery is below a learned threshold.
5. Add actionable stale alerts: Open Locations and Request Fresh Location. The
   locate action must have a cooldown and remain user-initiated.
6. Add an SOS acknowledgement/escalation workflow so both parents can see that
   one has acknowledged; never auto-call emergency services.
7. Rework the dashboard into Status, Safety, Location Controls, School, and
   Diagnostics sections, with normal state concise and faults prominent.

**Exit:** the app is unnecessary for normal location, alert, battery, and
school-day operations.

## Phase 2 — passive API discovery and read-only parity

Capture Mission Control traffic only on the family's authorized account. Do
not blindly mutate the live child account. Record endpoint, method, redacted
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
readback, rollback, audit logging without sensitive payloads, and an unavailable
state when COSMO behavior changes.

Contact/Guardian writes remain out of routine HA control unless a separate
review proves backup, validation, rollback, and recovery. App installs, billing,
and communications remain vendor/web/phone functions.

## Safety invariants

- Routine pause never stops tracking, SOS, power, or battery monitoring.
- Active Tracking is never scheduled continuously.
- No automatic 911 call, contact removal, factory reset, firmware update, or
  destructive account operation.
- One failed parent notification never blocks the other.
- No exact coordinates, credentials, phone numbers, IMEI, or message content in
  diagnostics, logs, issues, or repository fixtures.
- COSMO remains the system of record for emergency protocol and account access.

## App-removal exit criteria

Mission Control may be removed from daily phones after real-world arrival,
departure, stale/fresh, low-battery/recovery, powered-off/recovery, and
coordinated SOS tests pass; guardian/contact configuration is verified; the
break-glass recovery method is documented and tested; and at least one school
week completes without requiring the app.

## Official evidence

- JrTrack 5 guide: https://cosmotogether.com/pages/jrtrack-5-user-guide
- Battery guidance: https://cosmotogether.com/pages/battery-life
- Product updates: https://cosmotogether.com/pages/updates
