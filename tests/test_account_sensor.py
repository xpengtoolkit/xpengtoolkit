#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Account sensor: which XPENG account these credentials belong to.

    python3 tests/test_account_sensor.py
    python3 -m unittest discover -s tests -v

The tokens cannot answer this. An accessToken is a tenant-sealed blob whose
plaintext is only the teeId and this install's deviceId (verified by decoding
one), and the tenant holds keys, not account data. So the email has to be
recorded when it is known - at onboarding, where the user just typed it - and
persisted somewhere durable.

Durability is the point of these tests: the value must come from the config
ENTRY, so it survives a reboot and survives the token file being deleted or
replaced by a later re-onboard that did not record it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest

COMPONENT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(COMPONENT))
import _component  # noqa: E402

COMPONENT = _component.component_dir()
_SRC = _component.module_path("sensor.py")


class _Any:
    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, _n):
        return _Any()


def _load_sensor_module():
    class _Auto:
        def __getattr__(self, name):
            return name

    class _EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    _mod("homeassistant")
    _mod("homeassistant.components")
    _mod("homeassistant.components.sensor",
         SensorDeviceClass=_Auto(), SensorEntity=object, SensorStateClass=_Auto())
    _mod("homeassistant.config_entries", ConfigEntry=_Any)
    _mod("homeassistant.const", EntityCategory=_EntityCategory,
         UnitOfTemperature=_Auto(), UnitOfTime=_Auto())
    _mod("homeassistant.core", HomeAssistant=_Any, callback=lambda fn: fn)
    _mod("homeassistant.helpers")
    _mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=_Any)
    # sensor.py prunes registry entries for entities it no longer creates
    # (_prune_removed_entities), so this module has to exist here too.
    _mod("homeassistant.helpers.entity_registry", async_get=lambda hass: _Any())
    _mod("homeassistant.helpers.event", async_track_time_change=_Any,
         async_track_time_interval=_Any)

    pkg = "custom_components.xpeng"
    _mod("custom_components")
    _mod(pkg, __path__=[str(COMPONENT)])
    _mod(f"{pkg}.const", DOMAIN="xpeng", SENSOR_DESCRIPTIONS=(),
         XPengSensorEntityDescription=_Any)
    _mod(f"{pkg}.coordinator", XPengDataCoordinator=_Any)
    error_spec = importlib.util.spec_from_file_location(
        f"{pkg}.xpeng_errors", _component.module_path("xpeng_errors.py")
    )
    error_module = importlib.util.module_from_spec(error_spec)
    sys.modules[error_spec.name] = error_module
    error_spec.loader.exec_module(error_module)
    _mod(f"{pkg}.poll_status", describe=_Any, format_age=_Any, is_active=_Any,
         query_interval_seconds=_Any, next_query_at=_Any)

    class _XPengEntity:
        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator

        @property
        def _vin(self) -> str:
            return "L1NNSGHA1SB000001"

    _mod(f"{pkg}.entity", XPengEntity=_XPengEntity)
    _mod(f"{pkg}.vin_descriptor", mask_vin=lambda vin: vin)

    spec = importlib.util.spec_from_file_location(f"{pkg}.sensor", _SRC)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg}.sensor"] = module
    spec.loader.exec_module(module)
    return module


_sensor = _load_sensor_module()
if not hasattr(_sensor, "XPengAccountSensor"):
    print(f"SKIP: XPengAccountSensor is not implemented in {COMPONENT.name}")
    raise SystemExit(0)

XPengAccountSensor = _sensor.XPengAccountSensor


class _Coordinator:
    def __init__(self, email="", uid=""):
        self.account_email = email
        self.account_uid = uid


class TestAccountSensor(unittest.TestCase):
    def test_reports_the_email_when_known(self):
        """The headline value: the account address, in the clear."""
        s = XPengAccountSensor(_Coordinator(email="driver@example.com", uid="123"))
        self.assertEqual(s.native_value, "driver@example.com")

    def test_falls_back_to_the_uid_for_older_entries(self):
        """Entries imported before the tool recorded the email still identify."""
        s = XPengAccountSensor(_Coordinator(email="", uid="12345678"))
        self.assertEqual(s.native_value, "12345678")

    def test_no_identity_at_all_reports_unknown(self):
        s = XPengAccountSensor(_Coordinator())
        self.assertIsNone(s.native_value)

    def test_uid_is_exposed_alongside_the_email(self):
        s = XPengAccountSensor(_Coordinator(email="a@b.c", uid="42"))
        self.assertEqual(s.extra_state_attributes.get("uid"), "42")

    def test_uid_only_says_why_the_email_is_missing(self):
        """Actionable, not mysterious: the attribute says how to fill it in."""
        s = XPengAccountSensor(_Coordinator(email="", uid="42"))
        reason = s.extra_state_attributes.get("email_unknown_reason", "")
        self.assertIn("onboarding tool", reason)

    def test_no_reason_offered_when_the_email_is_known(self):
        s = XPengAccountSensor(_Coordinator(email="a@b.c", uid="42"))
        self.assertNotIn("email_unknown_reason", s.extra_state_attributes)

    def test_it_is_diagnostic_not_vehicle_telemetry(self):
        """It is account metadata, not a reading from the car."""
        self.assertEqual(
            XPengAccountSensor(_Coordinator(email="a@b.c"))._attr_entity_category,
            "diagnostic",
        )


class TestDurableStorage(unittest.TestCase):
    """The email must live on the entry, not only in the token file."""

    def test_entry_data_persists_it(self):
        setup = (_component.component_dir() / "__init__.py").read_text()
        self.assertIn("entry.data.get(CONF_XPENG_EMAIL)", setup,
                      "entry data must be the primary source")
        # entry first, token file only as a fallback
        self.assertLess(
            setup.index("entry.data.get(CONF_XPENG_EMAIL)"),
            setup.index('(_identity_tokens or {}).get("account_email")'),
            "entry data must be consulted before the token file",
        )

    def test_import_persists_it_on_the_entry(self):
        flow = (_component.component_dir() / "config_flow.py").read_text()
        self.assertIn("data[CONF_XPENG_EMAIL] = account_email", flow)

    def test_import_never_blanks_an_existing_email(self):
        flow = (_component.component_dir() / "config_flow.py").read_text()
        self.assertIn("if account_email:", flow,
                      "a re-import without an email must not clear the entry")

    def test_the_tool_records_it_when_saving(self):
        tool = _component.ROOT / "tools" / "onboard_email_login.py"
        if not tool.is_file():
            tool = _component.ROOT / "xpeng-google" / "tools" / "onboard_email_login.py"
        self.assertTrue(tool.is_file(), "onboarding tool not found")
        self.assertIn('tokens["account_email"] = args.email', tool.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
