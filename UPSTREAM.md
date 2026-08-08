# Upstream maintenance

This repository is a narrow privacy-hardening fork of
[`kunalkhosla/cosmo-homeassistant`](https://github.com/kunalkhosla/cosmo-homeassistant).
Keep the patch small so upstream fixes can be merged rather than reimplemented.

## Remote layout

```text
origin    git@github.com:josiah116/cosmo-homeassistant.git
upstream  https://github.com/kunalkhosla/cosmo-homeassistant.git
```

Verify at any time:

```bash
git remote -v
git status --short --branch
```

## Routine update

1. Start from a clean `main` branch.
2. Run `scripts/sync-upstream.sh`.
3. Review the merge and especially these privacy-sensitive files:
   - `custom_components/cosmo/coordinator.py`
   - `custom_components/cosmo/sensor.py`
   - any new HTTP clients or geocoding code
4. Confirm `python3 scripts/validate.py` passes.
5. Exercise login, cached polling, the request-location button, and every entity in a non-critical Home Assistant environment.
6. Increment the fork version in `custom_components/cosmo/manifest.json` when publishing changed runtime code.
7. Commit and push only after review.
8. Tag and release the exact tested commit; point HACS at that release rather than an unreviewed branch tip.

## Conflict handling

If the merge conflicts, abort rather than guessing:

```bash
git merge --abort
```

Reconcile upstream behavior against the fork's two invariants:

- No child coordinates are sent to an external reverse-geocoding provider.
- Scheduled refreshes read COSMO's server cache and never enable active tracking.

## Rollback

HACS can reinstall the previous release. Keep every deployed release tag immutable.
After rollback, restart Home Assistant and verify the tracker, last-fix timestamp,
battery, SOS, powered-off, and request-location entities.
