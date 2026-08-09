#!/usr/bin/env python3
"""Repository validation that runs without a Home Assistant checkout."""

from __future__ import annotations

import compileall
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "cosmo"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    if not compileall.compile_dir(COMPONENT, quiet=1):
        raise SystemExit("Python compilation failed")

    manifest = load_json(COMPONENT / "manifest.json")
    strings = load_json(COMPONENT / "strings.json")
    translations = load_json(COMPONENT / "translations" / "en.json")
    load_json(ROOT / "hacs.json")

    assert manifest["domain"] == "cosmo"
    assert manifest["version"]
    assert strings == translations, "English translation drifted from strings.json"

    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in COMPONENT.rglob("*.py")
    ).lower()
    forbidden = ("nominatim", "openstreetmap.org/reverse")
    for token in forbidden:
        assert token not in runtime, f"privacy guard failed: runtime contains {token!r}"

    tracker_source = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    assert "phone_number" not in tracker_source
    assert "gsmNumber" not in tracker_source

    coordinator_source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "always_update=False" in coordinator_source

    button_source = (COMPONENT / "button.py").read_text(encoding="utf-8")
    assert "stop_active_tracking" in button_source or "async_stop_active_tracking" in button_source
    assert "_ACCEPTABLE_FIX_ACCURACY_METERS" in button_source or "100" in button_source
    assert "async_create_background_task" in button_source or "background_task" in button_source.lower() or "create_task" in button_source

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "josiah116/cosmo-homeassistant" in readme
    assert "UPSTREAM.md" in readme
    assert "recorder" in readme.lower()
    assert "exclude" in readme.lower()

    print(f"validation: PASS (cosmo {manifest['version']})")


if __name__ == "__main__":
    main()
