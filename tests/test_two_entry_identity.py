#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two config entries must not sign as each other's device (issue #11).

The device id is what a tenant's key pool is sealed against and what every
request is signed as, so it is per pair. The REST helpers are called with
``config=None``, and the integration published the resolved id through one
process-wide override — meaning that on a two-entry install the entry that set
up last won, and the other car's mint/REST calls signed as the wrong device.
XPENG answers ``12010104 "Login status expired"`` to that, the same error as
genuinely dead tokens, so the second entry looked like it had expired
credentials and the two entries kept replacing each other.

What is pinned here: a tenant's own ``deviceId`` beats that override, while a
caller's explicit id (the config flow's, before a tenant exists) still wins.

    python3 tests/test_two_entry_identity.py
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import calendar  # noqa: F401  (cache stdlib before the component dir shadows it)
import importlib.util
import pathlib
import select  # noqa: F401  (component select.py must not shadow stdlib select)
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

API_PATH = _component.module_path("xpeng_api.py")

TENANT_DEVICE = "a" * 32
OTHER_ENTRY_DEVICE = "b" * 32


def _stub_cryptography() -> None:
    """Stand in for `cryptography`, which xpeng_api imports at module scope."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        pass

    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    class _Any(types.ModuleType):
        def __getattr__(self, name):
            child = _Any(f"{self.__name__}.{name}")
            setattr(self, name, child)
            return child

    def fake_import(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            return _Any(name)
        return real_import(name, *args, **kwargs)

    if isinstance(__builtins__, dict):
        __builtins__["__import__"] = fake_import
    else:
        __builtins__.__import__ = fake_import


def _load_api():
    api_dir = str(API_PATH.parent)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    spec = importlib.util.spec_from_file_location("xpeng_api_two_entries", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_stub_cryptography()
api = _load_api()


class TenantIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        # What entry setup does: whichever entry ran last publishes its id.
        api.set_configured_device_id(OTHER_ENTRY_DEVICE)

    def tearDown(self) -> None:
        api.set_configured_device_id("")

    def _resolve(self, tenant, caller_config=None):
        """The config the signing helpers actually use for a tenant-signed call."""
        merged = api._cfg_with_tenant_identity(
            api._get_config(caller_config), tenant
        )
        return merged

    def test_the_tenant_identity_beats_the_other_entry(self):
        tenant = {"deviceId": TENANT_DEVICE}
        cfg = self._resolve(tenant)
        self.assertEqual(cfg["ConfiguredDeviceId"], TENANT_DEVICE)
        # What the headers would sign as.
        self.assertEqual(api._get_device_id(cfg), TENANT_DEVICE)

    def test_the_other_entry_is_not_used_for_this_one(self):
        """The regression: the override used to win and produce 12010104."""
        tenant = {"deviceId": TENANT_DEVICE}
        cfg = self._resolve(tenant)
        self.assertNotEqual(cfg["ConfiguredDeviceId"], OTHER_ENTRY_DEVICE)

    def test_an_explicit_caller_id_still_wins(self):
        """The config flow passes its own id before this entry's tenant exists."""
        cfg = self._resolve(
            {"deviceId": TENANT_DEVICE}, {"ConfiguredDeviceId": "flow-device"}
        )
        self.assertEqual(cfg["ConfiguredDeviceId"], "flow-device")

    def test_a_tenant_without_an_id_changes_nothing(self):
        cfg = self._resolve({})
        self.assertEqual(cfg["ConfiguredDeviceId"], OTHER_ENTRY_DEVICE)

    def test_with_no_override_the_tenant_identity_is_applied(self):
        api.set_configured_device_id("")
        cfg = self._resolve({"deviceId": TENANT_DEVICE})
        self.assertEqual(cfg["ConfiguredDeviceId"], TENANT_DEVICE)


class BothEntriesKeepTheirOwnIdentityTests(unittest.TestCase):
    """Exercised together, the way the two entries actually run."""

    def tearDown(self) -> None:
        api.set_configured_device_id("")

    def test_each_entry_signs_as_its_own_device(self):
        car_a = {"deviceId": "a" * 32}
        car_b = {"deviceId": "b" * 32}
        for last_started, expected in ((car_a, "b" * 32), (car_b, "a" * 32)):
            api.set_configured_device_id(last_started["deviceId"])
            for tenant, want in ((car_a, "a" * 32), (car_b, "b" * 32)):
                cfg = api._cfg_with_tenant_identity(api._get_config(None), tenant)
                self.assertEqual(
                    api._get_device_id(cfg),
                    want,
                    f"entry {expected} started last but {want} must still be used",
                )


if __name__ == "__main__":
    unittest.main()
