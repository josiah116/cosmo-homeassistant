#!/usr/bin/env python3
"""Repository validation that runs without a Home Assistant checkout."""

from __future__ import annotations

import compileall
import json
import re
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
    const_source = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert f'VERSION = "{manifest["version"]}"' in const_source
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
    assert "def available" in tracker_source

    entity_source = (COMPONENT / "entity.py").read_text(encoding="utf-8")
    assert "serial_number" not in entity_source
    assert "__dataclass_fields__" in entity_source

    coordinator_source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "always_update=False" in coordinator_source
    assert "get_settings" in coordinator_source
    assert "location_fix_age" in coordinator_source
    assert "async_initialize_active_tracking" in coordinator_source
    assert "except Exception" not in coordinator_source

    init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "await coordinator.async_initialize_active_tracking()" in init_source
    assert 'f"{entry.entry_id}_charger_battery"' in init_source
    assert 'f"{entry.entry_id}_firmware"' in init_source

    sensor_source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert 'key="charger_battery"' not in sensor_source
    assert 'key="firmware"' not in sensor_source

    button_source = (COMPONENT / "button.py").read_text(encoding="utf-8")
    assert "stop_active_tracking" in button_source or "async_stop_active_tracking" in button_source
    assert "_ACCEPTABLE_FIX_ACCURACY_METERS" in button_source or "100" in button_source
    assert "async_create_background_task" in button_source or "background_task" in button_source.lower() or "create_task" in button_source
    assert "except Exception" not in button_source

    api_source = (COMPONENT / "api.py").read_text(encoding="utf-8")
    assert "<device>" in api_source
    assert "text[:" not in api_source

    diagnostics_source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    for key in (
        "email",
        "password",
        "token",
        "latitude",
        "longitude",
        "radius",
        "gpsDate",
        "phone",
        "imei",
        "serial_number",
        "message",
        "call",
    ):
        assert f'"{key}"' in diagnostics_source

    tests = ROOT / "tests"
    assert (tests / "test_locate_controls.py").exists()
    assert (tests / "test_entities.py").exists()
    fixtures = (tests / "conftest.py").read_text(encoding="utf-8")
    assert "firstName" not in fixtures
    assert not re.search(r'"(?:latitude|longitude)"\s*:\s*-?\d', fixtures)

    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    assert "|| true" not in workflow
    assert "ruff check ." in workflow
    assert "-OO -m pytest" in workflow

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "josiah116/cosmo-homeassistant" in readme
    assert "UPSTREAM.md" in readme
    assert "recorder" in readme.lower()
    assert "exclude" in readme.lower()

    print(f"validation: PASS (cosmo {manifest['version']})")


if __name__ == "__main__":
    main()
