#!/usr/bin/env python3
"""Pin the command-permission policy: the table, the gates, and their limits.

Three kinds of check live here, and the third is the one that keeps the
feature honest:

1. the policy arithmetic (presets, normalisation, the absent-option case);
2. a source-level completeness check - every registered service is in
   ``SERVICE_GROUPS``, and every entity command method guards before it sends -
   so a command added later cannot quietly bypass the policy;
3. the gate itself, against a stubbed Home Assistant, including the two
   exemptions that must not break: ``import_credentials`` (the renewal app's
   only call) and calls with no user context.

Run from the repo root:

    python3 tests/test_command_policy.py
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import pathlib
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _component  # noqa: E402

PKG = _component.component_dir()


# ── module loading ────────────────────────────────────────────────────────
#
# command_policy.py is pure, but command_gate.py imports Home Assistant. Both
# are loaded under a synthetic package so their relative imports resolve
# without installing HA.


def _load(name: str, path: pathlib.Path, package: str | None = None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # ``from __future__ import annotations`` + dataclasses resolves type
    # annotations through sys.modules[cls.__module__], so the module must be
    # registered before exec.
    spec.loader.exec_module(module)
    return module


policy = _load("xpengtest.command_policy", PKG / "command_policy.py")


# ── Home Assistant stubs for command_gate ─────────────────────────────────


class HomeAssistantError(Exception):
    pass


class Unauthorized(HomeAssistantError):
    def __init__(self, **kwargs):
        super().__init__("Unauthorized")
        self.__dict__.update(kwargs)


class UnknownUser(Unauthorized):
    pass


def _install_ha_stubs() -> None:
    ha = types.ModuleType("homeassistant")

    auth = types.ModuleType("homeassistant.auth")
    perms = types.ModuleType("homeassistant.auth.permissions")
    perms_const = types.ModuleType("homeassistant.auth.permissions.const")
    perms_const.POLICY_CONTROL = "control"
    perms_const.POLICY_READ = "read"
    perms_const.POLICY_EDIT = "edit"

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.ServiceCall = type("ServiceCall", (), {})

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.Unauthorized = Unauthorized
    exceptions.UnknownUser = UnknownUser

    helpers = types.ModuleType("homeassistant.helpers")
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: hass.entity_registry
    helpers.entity_registry = entity_registry

    for name, module in {
        "homeassistant": ha,
        "homeassistant.auth": auth,
        "homeassistant.auth.permissions": perms,
        "homeassistant.auth.permissions.const": perms_const,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_registry": entity_registry,
    }.items():
        sys.modules[name] = module

    # The synthetic package command_gate's relative imports resolve against.
    pkg = types.ModuleType("xpengtest")
    pkg.__path__ = [str(PKG)]
    sys.modules["xpengtest"] = pkg
    stub_const = types.ModuleType("xpengtest.const")
    stub_const.DOMAIN = "xpeng"
    sys.modules["xpengtest.const"] = stub_const


_install_ha_stubs()
gate = _load("xpengtest.command_gate", PKG / "command_gate.py")


# ── fakes ─────────────────────────────────────────────────────────────────


class _Registry:
    def __init__(self, entity_ids):
        self.entities = {
            eid: types.SimpleNamespace(entity_id=eid, platform="xpeng")
            for eid in entity_ids
        }


class _Permissions:
    def __init__(self, allowed):
        self._allowed = allowed

    def check_entity(self, entity_id, policy_name):
        return entity_id in self._allowed


class _User:
    def __init__(self, is_admin=False, allowed=()):
        self.is_admin = is_admin
        self.permissions = _Permissions(set(allowed))


class _Bus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class _Auth:
    def __init__(self, user):
        self._user = user

    async def async_get_user(self, user_id):
        return self._user


class _Hass:
    def __init__(self, user=None, entity_ids=(), context_user_id=None):
        self.bus = _Bus()
        self.auth = _Auth(user)
        self.entity_registry = _Registry(entity_ids)
        self.data = {"xpeng": {}}
        self._context_user_id = context_user_id


class _Call:
    def __init__(self, user_id=None):
        self.context = types.SimpleNamespace(user_id=user_id)
        self.data = {}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. the policy table ───────────────────────────────────────────────────


class TestPolicyArithmetic(unittest.TestCase):
    def test_absent_options_mean_full(self):
        """An install that predates the option must behave exactly as before."""
        p = policy.CommandPolicy.from_options(None)
        self.assertEqual(p.mode, policy.PRESET_FULL)
        for group in policy.ALL_GROUPS:
            self.assertTrue(p.allows(group), group)
        self.assertTrue(p.can_wake())

        # Same for an options dict that simply lacks the keys.
        p2 = policy.CommandPolicy.from_options({"scan_interval": 60})
        self.assertEqual(p2.mode, policy.PRESET_FULL)

    def test_unknown_permission_becomes_full_not_disabled(self):
        """Guessing 'disabled' would silently stop a car charging."""
        groups = policy.normalize_groups({"access": "nonsense", "charging": None})
        self.assertEqual(groups["access"], policy.PERM_FULL)
        self.assertEqual(groups["charging"], policy.PERM_FULL)

    def test_partial_map_is_completed_with_full(self):
        """A group added in a later version must not default to off."""
        groups = policy.normalize_groups({"access": policy.PERM_DISABLED})
        self.assertEqual(groups["access"], policy.PERM_DISABLED)
        self.assertEqual(groups["comfort"], policy.PERM_FULL)

    def test_presets_cover_every_group(self):
        for name, table in policy.PRESETS.items():
            self.assertEqual(set(table), set(policy.ALL_GROUPS), name)
            for group, perm in table.items():
                self.assertIn(perm, policy.ALL_PERMISSIONS, (name, group))

    def test_read_only_allows_queries_and_nothing_else(self):
        p = policy.CommandPolicy.from_preset(policy.PRESET_READ_ONLY)
        self.assertEqual(p.mode, policy.PRESET_READ_ONLY)
        self.assertTrue(p.allows(policy.GROUP_QUERIES))
        for group in policy.ALL_GROUPS:
            if group != policy.GROUP_QUERIES:
                self.assertFalse(p.allows(group), group)

    def test_safe_secures_but_never_opens(self):
        """Locking is safe; unlocking is not. That is the whole taxonomy."""
        p = policy.CommandPolicy.from_preset(policy.PRESET_SAFE)
        self.assertTrue(p.allows(policy.GROUP_SECURING))
        self.assertFalse(p.allows(policy.GROUP_ACCESS))
        self.assertFalse(p.allows(policy.GROUP_CHARGING))
        self.assertFalse(p.allows(policy.GROUP_FIRMWARE))
        # The lock entity must still exist, so its state is visible.
        self.assertTrue(p.exposes(policy.GROUP_SECURING))
        self.assertFalse(p.exposes(policy.GROUP_ACCESS))

    def test_safe_plus_adds_charging_and_the_outlet(self):
        p = policy.CommandPolicy.from_preset(policy.PRESET_SAFE_PLUS)
        self.assertTrue(p.allows(policy.GROUP_CHARGING))
        self.assertTrue(p.allows(policy.GROUP_POWER))
        self.assertFalse(p.allows(policy.GROUP_ACCESS))
        self.assertFalse(p.allows(policy.GROUP_ADVANCED))

    def test_the_old_charging_name_still_means_safe_plus(self):
        """An unknown preset falls back to FULL, so an alias is not cosmetic.

        A stored or app-sent `charging` must resolve to the same bundle, not
        silently widen the ceiling to everything.
        """
        old = policy.CommandPolicy.from_preset("charging")
        new = policy.CommandPolicy.from_preset(policy.PRESET_SAFE_PLUS)
        self.assertEqual(old.groups, new.groups)
        self.assertEqual(old.mode, policy.PRESET_SAFE_PLUS)
        self.assertNotEqual(old.groups, policy.PRESETS[policy.PRESET_FULL])
        self.assertFalse(old.allows(policy.GROUP_ACCESS))

    def test_no_two_presets_share_a_group_map(self):
        """`preset_for` returns the first match, so a duplicate makes `mode` lie."""
        seen = {}
        for name in policy.SELECTABLE_PRESETS:
            key = tuple(sorted(policy.PRESETS[name].items()))
            self.assertNotIn(key, seen, f"{name} has the same map as {seen.get(key)}")
            seen[key] = name

    def test_a_new_entry_defaults_to_safe_plus_while_an_absent_policy_is_full(self):
        """The pair that matters, and they are different questions.

        ``DEFAULT_COMMAND_MODE`` answers "what should a new entry be?". An entry
        with no stored policy must still answer "what was this one already
        doing?" - and the only safe answer is Full access, or an upgrade would
        take charging away from an automation.
        """
        const = (PKG / "const.py").read_text()
        defaults = const[const.index("# Defaults"):const.index("# MQTT topics")]
        self.assertIn(
            'DEFAULT_COMMAND_MODE: Final = "safe_plus"',
            defaults,
            "a new entry must start at Safe Plus",
        )

        new_entry = policy.CommandPolicy.from_preset("safe_plus")
        self.assertTrue(new_entry.allows(policy.GROUP_CHARGING))
        self.assertFalse(new_entry.allows(policy.GROUP_ACCESS))

        existing = policy.CommandPolicy.from_options(None)
        self.assertEqual(existing.mode, policy.PRESET_FULL)
        existing_partial = policy.CommandPolicy.from_options({"scan_interval": 60})
        self.assertEqual(existing_partial.mode, policy.PRESET_FULL)

    def test_full_is_everything(self):
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        for group in policy.ALL_GROUPS:
            self.assertEqual(p.permission(group), policy.PERM_FULL, group)

    def test_read_only_exposes_but_refuses(self):
        p = policy.CommandPolicy(
            groups=policy.normalize_groups(
                {policy.GROUP_COMFORT: policy.PERM_READ_ONLY}
            )
        )
        self.assertTrue(p.exposes(policy.GROUP_COMFORT))
        self.assertTrue(p.read_only(policy.GROUP_COMFORT))
        self.assertFalse(p.allows(policy.GROUP_COMFORT))

    def test_options_round_trip(self):
        for preset in policy.SELECTABLE_PRESETS:
            p = policy.CommandPolicy.from_preset(preset, allow_wake=False)
            stored = p.to_options()
            back = policy.CommandPolicy.from_options(stored)
            self.assertEqual(back.groups, p.groups, preset)
            self.assertFalse(back.allow_wake)
            self.assertEqual(back.mode, preset)

    def test_a_hand_edited_map_reads_as_custom(self):
        groups = dict(policy.PRESETS[policy.PRESET_SAFE])
        groups[policy.GROUP_FIRMWARE] = policy.PERM_FULL
        self.assertEqual(policy.preset_for(groups), policy.PRESET_CUSTOM)
        p = policy.CommandPolicy(groups=policy.normalize_groups(groups))
        self.assertEqual(p.mode, policy.PRESET_CUSTOM)
        self.assertTrue(p.allows(policy.GROUP_FIRMWARE))
        self.assertFalse(p.allows(policy.GROUP_ACCESS))

    def test_allow_wake_blocks_only_the_wake(self):
        p = policy.CommandPolicy(
            groups=policy.normalize_groups(None), allow_wake=False
        )
        self.assertTrue(p.action_allowed(policy.GROUP_QUERIES))
        self.assertFalse(
            p.action_allowed(policy.GROUP_QUERIES, policy.ACTION_WAKE)
        )
        self.assertFalse(p.can_wake())

    def test_can_wake_needs_the_queries_group_too(self):
        p = policy.CommandPolicy(
            groups=policy.normalize_groups(
                {policy.GROUP_QUERIES: policy.PERM_DISABLED}
            )
        )
        self.assertFalse(p.can_wake())

    def test_denial_reason_names_the_group_and_the_mode(self):
        p = policy.CommandPolicy.from_preset(policy.PRESET_SAFE)
        reason = policy.denial_reason(
            p, policy.GROUP_ACCESS, policy.ACTION_COMMAND
        )
        self.assertIn("Access actions", reason)
        self.assertIn("safe", reason)

    def test_denial_reason_explains_a_wake_refusal(self):
        p = policy.CommandPolicy(groups=policy.normalize_groups(None), allow_wake=False)
        reason = policy.denial_reason(p, policy.GROUP_QUERIES, policy.ACTION_WAKE)
        self.assertIn("allow wake", reason)


# ── 2. nothing may bypass the policy ──────────────────────────────────────


class TestEveryCommandIsGated(unittest.TestCase):
    def test_every_service_in_services_yaml_has_a_group(self):
        names = re.findall(
            r"^([a-z_]+):$", (PKG / "services.yaml").read_text(), re.M
        )
        self.assertEqual(len(names), 41, "services.yaml changed shape")
        missing = [n for n in names if n not in policy.SERVICE_GROUPS]
        self.assertEqual(missing, [], "services with no declared policy group")

    def test_no_group_is_declared_for_a_service_that_does_not_exist(self):
        names = set(
            re.findall(r"^([a-z_]+):$", (PKG / "services.yaml").read_text(), re.M)
        )
        stale = [s for s in policy.SERVICE_GROUPS if s not in names]
        self.assertEqual(stale, [], "SERVICE_GROUPS names a service that is gone")

    def test_every_registration_in_services_py_goes_through_the_gate(self):
        """A new command registered with hass.services directly would skip it."""
        source = (PKG / "services.py").read_text()

        # ``async_register(`` *contains* ``_register(``, so match the full call
        # or this check passes on every line and proves nothing. The only
        # permitted direct registrations are the two branches inside the gate.
        direct = [
            line
            for line in source.splitlines()
            if "hass.services.async_register(" in line
        ]
        self.assertEqual(
            len(direct),
            2,
            f"a service is registered outside the policy gate: {direct}",
        )

        # Compare the registered service *values*, not the constant names: a
        # constant whose string changed (SERVICE_UNLOCK = "unlock_door") would
        # otherwise keep passing while the policy keyed on the old name.
        constants = dict(
            re.findall(r'^(SERVICE_[A-Z_]+) = "([a-z_]+)"', source, re.M)
        )
        registered_names = re.findall(r"_register\((SERVICE_[A-Z_]+)", source)
        unknown = [n for n in registered_names if n not in constants]
        self.assertEqual(unknown, [], "a registered constant has no definition")
        registered = {constants[n] for n in registered_names}
        self.assertEqual(
            registered,
            set(policy.SERVICE_GROUPS),
            "services.py and SERVICE_GROUPS disagree",
        )

    def test_every_unloaded_service_is_a_known_service(self):
        source = (PKG / "services.py").read_text()
        block = source.split("async def async_unload_services", 1)[1]
        unloaded = set(re.findall(r"SERVICE_([A-Z_]+),", block))
        declared = {s.upper() for s in policy.SERVICE_GROUPS}
        self.assertEqual(
            unloaded - declared,
            set(),
            "a service is unloaded that the policy does not know",
        )

    def test_import_credentials_is_exempt(self):
        self.assertIsNone(policy.service_group("import_credentials"))
        self.assertIn("import_credentials", policy.EXEMPT_SERVICES)

    def test_wake_services_are_a_subset_of_queries(self):
        for service in policy.WAKE_SERVICES:
            self.assertEqual(
                policy.SERVICE_GROUPS.get(service), policy.GROUP_QUERIES, service
            )

    def test_every_entity_command_method_guards_first(self):
        """The data layer is reachable only through a guard.

        Walks each platform with ``ast`` and requires the first statement of
        every command method to be a ``self._guard(...)`` call - or, for the
        climate mode setter, a call to a method that is itself guarded.
        """
        commands = {
            "async_press",
            "async_open_cover",
            "async_close_cover",
            "async_turn_on",
            "async_turn_off",
            "async_set_native_value",
            "async_select_option",
            "async_lock",
            "async_unlock",
            "async_set_temperature",
            "async_set_fan_mode",
            "async_set_preset_mode",
        }
        delegates = {"async_set_hvac_mode": {"async_turn_on", "async_turn_off"}}
        checked = 0
        for filename in (
            "button.py",
            "cover.py",
            "switch.py",
            "number.py",
            "select.py",
            "climate.py",
            "lock.py",
        ):
            tree = ast.parse((PKG / filename).read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for func in node.body:
                    if not (
                        isinstance(func, ast.AsyncFunctionDef)
                        and func.name in commands | set(delegates)
                    ):
                        continue
                    checked += 1
                    where = f"{filename}:{node.name}.{func.name}"
                    if func.name in delegates:
                        called = {
                            n.func.attr
                            for n in ast.walk(func)
                            if isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute)
                        }
                        self.assertTrue(
                            called & delegates[func.name],
                            f"{where} neither guards nor delegates",
                        )
                        continue
                    body = list(func.body)
                    # A leading docstring is not a statement that does work.
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        body = body[1:]
                    self.assertTrue(body, where)
                    first = body[0]
                    self.assertIsInstance(first, ast.Expr, where)
                    call = first.value
                    self.assertIsInstance(call, ast.Call, where)
                    self.assertIsInstance(call.func, ast.Attribute, where)
                    self.assertEqual(call.func.attr, "_guard", where)
                    self.assertEqual(
                        call.func.value.id, "self", where
                    )
        # 30 command methods, plus climate's mode setter, which delegates to
        # the two guarded methods above rather than guarding itself.
        self.assertEqual(checked, 31, "a command method appeared or vanished")

        # And the list above must cover every file that can command the car:
        # a new platform calling the MQTT client directly would otherwise be
        # walked past by this test in silence.
        senders = sorted(
            path.name
            for path in PKG.glob("*.py")
            if "self._mqtt_client.send_command" in path.read_text()
            or (("await async_send_confirmed(" in path.read_text()
                 or "await send_climate(" in path.read_text())
                and "self._guard(" in path.read_text())
        )
        self.assertEqual(
            senders,
            ["button.py", "climate.py", "cover.py", "lock.py", "number.py", "select.py", "switch.py"],
            "a platform commands the car but is not walked by this test",
        )

    def test_every_command_entity_declares_its_group(self):
        """A class that can command the car must say which group it answers to."""
        group_attr = re.compile(r"_policy_group\s*=")
        for filename in (
            "switch.py",
            "number.py",
            "select.py",
            "climate.py",
            "lock.py",
        ):
            source = (PKG / filename).read_text()
            for cls in re.findall(r"^class (\w+)\(XPengEntity, \w+\):", source, re.M):
                body = source.split(f"class {cls}(", 1)[1].split("\nclass ", 1)[0]
                if "self._guard(" not in body:
                    continue
                self.assertRegex(
                    body,
                    group_attr,
                    f"{filename}:{cls} guards commands but declares no group",
                )


class TestDocsMatchPolicy(unittest.TestCase):
    """The reference doc's group table is the user-facing form of SERVICE_GROUPS.

    A table nobody can regenerate rots the moment a command moves group, and a
    stale security table is worse than none: it tells the user something is
    blocked when it is not. The doc moved out of the README into
    docs/COMMAND_PERMISSIONS.md so the README could stay a summary - this test
    moved with it.
    """

    DOC = _component.ROOT / "docs" / "COMMAND_PERMISSIONS.md"

    def _rows(self) -> dict[str, list[str]]:
        text = self.DOC.read_text(encoding="utf-8")
        start = text.index("| Capability group | Services | Entities you will see |")
        rows: dict[str, list[str]] = {}
        for line in text[start:].splitlines():
            if not line.startswith("| **"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            label = cells[0].strip("* ")
            # Only the services cell is authoritative - the entity column is
            # prose and deliberately not parsed.
            services = re.findall(r"`([a-z_]+)`", cells[1])
            rows[label] = sorted(services)
        return rows

    def test_the_doc_exists_and_has_every_group(self):
        self.assertTrue(self.DOC.is_file(), "docs/COMMAND_PERMISSIONS.md is missing")
        rows = self._rows()
        self.assertEqual(sorted(rows), sorted(policy.GROUP_LABELS.values()))

    def test_each_row_lists_the_services_the_policy_assigns_it(self):
        for group in policy.ALL_GROUPS:
            expected = sorted(
                s for s, g in policy.SERVICE_GROUPS.items() if g == group
            )
            self.assertEqual(
                self._rows()[policy.GROUP_LABELS[group]],
                expected,
                f"docs row for {group} does not match SERVICE_GROUPS",
            )

    def test_the_readme_points_at_the_doc(self):
        readme = (_component.ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/COMMAND_PERMISSIONS.md", readme)


# ── 3. the gate ───────────────────────────────────────────────────────────


class TestGate(unittest.TestCase):
    def test_full_policy_allows_a_command(self):
        hass = _Hass()
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        _run(gate.async_guard_service(hass, _Call(), "unlock", policy.GROUP_ACCESS, p))
        self.assertEqual(hass.bus.events, [])

    def test_disabled_group_refuses_and_says_why(self):
        hass = _Hass()
        p = policy.CommandPolicy.from_preset(policy.PRESET_SAFE)
        with self.assertRaises(HomeAssistantError) as caught:
            _run(
                gate.async_guard_service(
                    hass, _Call(), "unlock", policy.GROUP_ACCESS, p
                )
            )
        message = str(caught.exception)
        self.assertIn("xpeng.unlock", message)
        self.assertIn("Access actions", message)
        self.assertIn("Configure", message)

        events = [e for e in hass.bus.events if e[0] == gate.EVENT_COMMAND_DENIED]
        self.assertEqual(len(events), 1)
        data = events[0][1]
        self.assertEqual(data["service"], "unlock")
        self.assertEqual(data["group"], policy.GROUP_ACCESS)
        self.assertEqual(data["mode"], policy.PRESET_SAFE)

    def test_read_only_group_refuses_a_command(self):
        hass = _Hass()
        p = policy.CommandPolicy(
            groups=policy.normalize_groups(
                {policy.GROUP_CHARGING: policy.PERM_READ_ONLY}
            )
        )
        with self.assertRaises(HomeAssistantError):
            _run(
                gate.async_guard_service(
                    hass, _Call(), "start_charge", policy.GROUP_CHARGING, p
                )
            )

    def test_import_credentials_survives_every_group_being_disabled(self):
        """The renewal app's only call, with no user context, must always pass."""
        hass = _Hass()
        p = policy.CommandPolicy(
            groups={g: policy.PERM_DISABLED for g in policy.ALL_GROUPS},
            allow_wake=False,
        )
        _run(
            gate.async_guard_service(
                hass, _Call(), "import_credentials", None, p
            )
        )
        self.assertEqual(hass.bus.events, [])

    def test_allow_wake_off_refuses_the_wake_but_keeps_the_queries(self):
        hass = _Hass()
        p = policy.CommandPolicy(groups=policy.normalize_groups(None), allow_wake=False)
        with self.assertRaises(HomeAssistantError) as caught:
            _run(
                gate.async_guard_service(
                    hass, _Call(), "force_update", policy.GROUP_QUERIES, p
                )
            )
        self.assertIn("allow wake", str(caught.exception))
        _run(
            gate.async_guard_service(
                hass, _Call(), "check_ota", policy.GROUP_QUERIES, p
            )
        )

    def test_user_without_control_is_refused(self):
        hass = _Hass(user=_User(is_admin=False, allowed=()), entity_ids=["lock.car"])
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        with self.assertRaises(Unauthorized):
            _run(
                gate.async_guard_service(
                    hass, _Call(user_id="u1"), "unlock", policy.GROUP_ACCESS, p
                )
            )

    def test_user_with_control_on_one_entity_passes(self):
        hass = _Hass(
            user=_User(is_admin=False, allowed=["switch.charger"]),
            entity_ids=["switch.charger", "lock.car"],
        )
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        _run(
            gate.async_guard_service(
                hass, _Call(user_id="u1"), "unlock", policy.GROUP_ACCESS, p
            )
        )

    def test_admin_passes_without_any_entity_grant(self):
        hass = _Hass(user=_User(is_admin=True), entity_ids=["lock.car"])
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        _run(
            gate.async_guard_service(
                hass, _Call(user_id="u1"), "unlock", policy.GROUP_ACCESS, p
            )
        )

    def test_guard_entity_refuses_read_only(self):
        hass = _Hass()
        p = policy.CommandPolicy(
            groups=policy.normalize_groups(
                {policy.GROUP_COMFORT: policy.PERM_READ_ONLY}
            )
        )
        with self.assertRaises(HomeAssistantError) as caught:
            gate.guard_entity(
                hass, "climate.car", policy.GROUP_COMFORT, p
            )
        self.assertIn("read-only", str(caught.exception))
        events = [e for e in hass.bus.events if e[0] == gate.EVENT_COMMAND_DENIED]
        self.assertEqual(events[0][1]["entity_id"], "climate.car")

    def test_guard_entity_allows_full(self):
        hass = _Hass()
        p = policy.CommandPolicy.from_preset(policy.PRESET_FULL)
        gate.guard_entity(hass, "climate.car", policy.GROUP_COMFORT, p)
        self.assertEqual(hass.bus.events, [])

    def test_policy_for_entry_tolerates_a_missing_options_attribute(self):
        entry = types.SimpleNamespace()
        self.assertEqual(
            gate.policy_for_entry(entry).mode, policy.PRESET_FULL
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
