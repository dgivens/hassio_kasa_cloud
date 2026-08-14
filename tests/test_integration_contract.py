"""Contract tests against the installed Home Assistant.

The unit tests in test_cloud_api.py cover protocol logic in isolation. These
verify the parts that only real Home Assistant can confirm: that every module
imports, that the manifest is well formed, and that we are not relying on APIs
Home Assistant has removed.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "custom_components" / "kasa_cloud"
sys.path.insert(0, str(REPO / "custom_components"))

MODULES = [
    "kasa_cloud",
    "kasa_cloud.cloud_api",
    "kasa_cloud.const",
    "kasa_cloud.coordinator",
    "kasa_cloud.entity",
    "kasa_cloud.config_flow",
    "kasa_cloud.binary_sensor",
    "kasa_cloud.button",
    "kasa_cloud.light",
    "kasa_cloud.sensor",
    "kasa_cloud.switch",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    assert importlib.import_module(name) is not None


def test_declared_platforms_all_have_a_module():
    package = importlib.import_module("kasa_cloud")
    for platform in package.PLATFORMS:
        assert (COMPONENT / f"{platform.value}.py").is_file()


def test_every_platform_module_is_declared():
    """Derived from HA's own Platform enum, so a new module cannot slip past."""
    from homeassistant.const import Platform

    package = importlib.import_module("kasa_cloud")
    declared = {platform.value for platform in package.PLATFORMS}
    platform_names = {platform.value for platform in Platform}
    on_disk = {p.stem for p in COMPONENT.glob("*.py") if p.stem in platform_names}
    assert on_disk == declared


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

@pytest.fixture
def manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text())


def test_manifest_domain_matches_directory(manifest):
    assert manifest["domain"] == COMPONENT.name


def test_iot_class_reflects_that_this_is_a_cloud_integration(manifest):
    assert manifest["iot_class"] == "cloud_polling"


def _imported_top_level_modules() -> set[str]:
    import ast

    imported: set[str] = set()
    for path in COMPONENT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    return imported


def test_no_unused_requirements_are_declared(manifest):
    """Upstream declared python-kasa but never imported it."""
    imported = _imported_top_level_modules()

    # Non-vacuous with an empty list: this talks to the cloud with raw aiohttp,
    # so python-kasa (module `kasa`) must be neither imported nor declared.
    assert "kasa" not in imported
    assert manifest["requirements"] == []

    for requirement in manifest["requirements"]:
        package = requirement.split(">")[0].split("=")[0].replace("-", "_")
        assert package in imported, f"{requirement} is declared but never imported"


def test_codeowners_are_github_handles(manifest):
    assert manifest["codeowners"]
    for owner in manifest["codeowners"]:
        assert owner.startswith("@"), f"{owner!r} is not a GitHub handle"


def test_config_flow_is_declared(manifest):
    assert manifest["config_flow"] is True


# --------------------------------------------------------------------------
# Translations: HA reads translations/en.json, never strings.json
# --------------------------------------------------------------------------

@pytest.fixture
def translations() -> dict:
    return json.loads((COMPONENT / "translations" / "en.json").read_text())


def test_runtime_translations_exist_and_match_strings(translations):
    assert translations == json.loads((COMPONENT / "strings.json").read_text())


def test_every_config_flow_step_is_translated(translations):
    config_flow = importlib.import_module("kasa_cloud.config_flow")
    steps = translations["config"]["step"]
    for step in ("user", "reauth_confirm"):
        assert step in steps, f"missing translation for step {step}"
        assert hasattr(config_flow.KasaCloudConfigFlow, f"async_step_{step}")


def test_every_error_key_used_by_the_flow_is_translated(translations):
    source = (COMPONENT / "config_flow.py").read_text()
    declared = set(translations["config"]["error"])
    for key in ("cannot_connect", "invalid_auth", "no_devices_found", "unknown"):
        assert f'"{key}"' in source, f"{key} unused by config_flow"
        assert key in declared, f"{key} has no translation"


def test_sensor_translation_keys_are_all_defined(translations):
    sensor = importlib.import_module("kasa_cloud.sensor")
    defined = set(translations["entity"]["sensor"])
    used = {
        description.translation_key
        for description in sensor.EMETER_SENSORS + sensor.DIAGNOSTIC_SENSORS
        if description.translation_key
    }
    assert used <= defined, f"untranslated sensor keys: {used - defined}"


# --------------------------------------------------------------------------
# Removed / deprecated Home Assistant APIs
# --------------------------------------------------------------------------

def test_installed_ha_has_removed_the_mired_light_api():
    """Documents the 2026.3 removal against whatever core is installed."""
    from homeassistant.components.light import LightEntity

    if hasattr(LightEntity, "color_temp"):
        pytest.skip("this Home Assistant predates the 2026.3 light cleanup")
    assert not hasattr(LightEntity, "min_mireds")


def test_light_does_not_use_the_mired_api_removed_in_ha_2026_3():
    source = (COMPONENT / "light.py").read_text()
    assert "color_temp_kelvin" in source

    # Look for the removed identifiers themselves, not the word "mired",
    # which legitimately appears in a comment explaining this very change.
    for removed in (
        "color_temperature_kelvin_to_mired",
        "color_temperature_mired_to_kelvin",
        "_attr_color_temp ",
        "min_mireds",
        "max_mireds",
        "def color_temp(",
        "ATTR_COLOR_TEMP,",
    ):
        assert removed not in source, f"uses removed light API: {removed}"


def test_no_module_creates_its_own_aiohttp_session():
    """HA owns the shared session; a per-request session leaks connections."""
    for path in COMPONENT.glob("*.py"):
        assert "aiohttp.ClientSession(" not in path.read_text(), path.name


def test_write_platforms_limit_parallel_cloud_calls():
    for name in ("switch", "light", "button"):
        module = importlib.import_module(f"kasa_cloud.{name}")
        assert getattr(module, "PARALLEL_UPDATES", None) == 1, name


def test_no_broad_handler_silently_swallows_errors():
    """The audited failure mode was 'turn every error into a plausible lie'.

    Parsed rather than string-matched, so reindenting cannot defeat it.
    """
    import ast

    for path in COMPONENT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            where = f"{path.name}:{node.lineno}"

            assert node.type is not None, f"bare except at {where}"

            broad = isinstance(node.type, ast.Name) and node.type.id in (
                "Exception",
                "BaseException",
            )
            if not broad:
                continue

            # A catch-all is acceptable only if it reports or re-raises.
            reports = any(
                isinstance(child, ast.Raise)
                or (isinstance(child, ast.Attribute) and child.attr.startswith(
                    ("debug", "info", "warning", "error", "exception")
                ))
                for child in ast.walk(node)
            )
            assert reports, f"broad handler at {where} neither logs nor re-raises"
