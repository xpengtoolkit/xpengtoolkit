"""Exercise identity I/O threading and watchdog diagnostics without HA or a car."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _component

SOURCE = (_component.component_dir() / "__init__.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def functions(*names):
    """Execute the real function bodies without importing HA's package setup."""
    nodes = [node for node in ast.walk(TREE)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    namespace = {"__package__": "custom_components.xpeng", "_LOGGER": logging.getLogger("xpeng.test")}
    exec(compile(ast.fix_missing_locations(module), "<xpeng_init>", "exec"), namespace)
    return namespace


class IdentityIOTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_thread = threading.get_ident()
        self.reads = []
        self.writes = []
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tenant = Path(self.tmp.name) / "tenant.json"
        self.tokens = Path(self.tmp.name) / "tokens.json"
        self.api = ModuleType("custom_components.xpeng.xpeng_api")

        def read(path):
            self.assertNotEqual(threading.get_ident(), self.loop_thread)
            self.reads.append(Path(path).name)
            return json.loads(Path(path).read_text(encoding="utf-8"))

        self.api.load_tenant_cache = read
        self.api.load_login_tokens = read
        self.api.deserialize_tenant = lambda data: dict(data)
        self.api.mint_device_id = lambda: "minted-device"
        self.api.DEVICE_ID_MAX_LENGTH = 32
        self.patcher = patch.dict(sys.modules, {self.api.__name__: self.api})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.ns = functions("_available_tenants", "_tenant_device_id", "_tokens_device_id",
                            "_async_resolve_configured_device_id", "_warn_on_device_id_mismatch")
        self.ns.update(CONF_TENANT_DATA="tenant_data", CONF_TOKEN_FILE="token_file",
                       CONF_CONFIGURED_DEVICE_ID="configured_device_id")

        async def executor(fn, *args):
            return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

        def update(entry, **kwargs):
            self.assertEqual(threading.get_ident(), self.loop_thread)
            self.writes.append(kwargs)
            entry.options = kwargs["options"]

        self.hass = SimpleNamespace(async_add_executor_job=executor,
                                    config_entries=SimpleNamespace(async_update_entry=update))
        self.entry = SimpleNamespace(entry_id="test", data={}, options={"unchanged": True})

    async def resolve(self, configured=""):
        return await self.ns["_async_resolve_configured_device_id"](
            self.hass, self.entry, str(self.tenant), configured)

    async def test_tenant_read_is_off_loop_and_entry_write_is_on_loop(self):
        self.tenant.write_text(json.dumps({"deviceId": "tenant-device"}), encoding="utf-8")
        self.assertEqual(await self.resolve(), "tenant-device")
        self.assertEqual(self.reads, ["tenant.json"])
        self.assertEqual(self.entry.options, {"unchanged": True, "configured_device_id": "tenant-device"})
        self.assertEqual(len(self.writes), 1)

    async def test_existing_option_wins_and_mismatch_is_still_reported(self):
        self.tenant.write_text(json.dumps({"deviceId": "other-device"}), encoding="utf-8")
        with self.assertLogs("xpeng.test", level="WARNING") as logs:
            self.assertEqual(await self.resolve("configured-device"), "configured-device")
        self.assertIn("Device id mismatch", " ".join(logs.output))
        self.assertEqual(self.writes, [])

    async def test_entry_tenant_keeps_priority_over_file_and_tokens(self):
        self.entry.data["tenant_data"] = {"deviceId": "entry-device"}
        self.tenant.write_text(json.dumps({"deviceId": "file-device"}), encoding="utf-8")
        self.entry.options["token_file"] = str(self.tokens)
        with self.assertLogs("xpeng.test", level="WARNING"):
            self.assertEqual(await self.resolve(), "entry-device")
        self.assertEqual(self.reads, ["tenant.json"])

    async def test_token_fallback_also_reads_off_loop(self):
        self.tokens.write_text(json.dumps({"mqttAccount": {"clientId": "xmart:app:123:" + "a" * 32}}), encoding="utf-8")
        self.entry.options["token_file"] = str(self.tokens)
        self.assertEqual(await self.resolve(), "a" * 32)
        self.assertEqual(self.reads, ["tenant.json", "tokens.json"])

    async def test_missing_or_broken_files_allow_first_run_to_mint(self):
        self.entry.options["token_file"] = str(self.tokens)
        for contents in (None, "broken json"):
            if contents is not None:
                self.tenant.write_text(contents, encoding="utf-8")
                self.tokens.write_text(contents, encoding="utf-8")
            self.assertEqual(await self.resolve(), "minted-device")

    def test_setup_awaits_the_identity_resolver(self):
        setup = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "async_setup_entry")
        self.assertTrue(any(isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name)
                            and node.value.func.id == "_async_resolve_configured_device_id"
                            for node in ast.walk(setup)))


class WatchdogDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def tick(self, *, parked=True, charging=False, connected=True, stale=350,
                   motion_age=None, can_wake=True, park_during_save=False):
        now = 100_000.0
        calls = []
        ns = functions("_silence_watchdog")
        spec = importlib.util.spec_from_file_location("watchdog_test_policy", _component.module_path("watchdog_policy.py"))
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        async def executor(fn, *args):
            return fn(*args)
        async def save(data):
            calls.append(("save", data))
            if park_during_save:
                ns["coordinator"].last_motion_at = 0.0
        ns.update(
            time=SimpleNamespace(time=lambda: now), vin="TESTVIN", entry=object(),
            refresh_state={},
            watchdog_state={"tick_count": 0, "last_reconnect": 0.0, "last_escalation": 0.0, "escalations": 0},
            SILENCE_THRESHOLD=300, RECONNECT_BACKOFF=600, PARKED_RECONNECT_BACKOFF=1800,
            ESCALATE_AFTER_SECONDS=policy.ESCALATE_AFTER_SECONDS,
            ESCALATE_BACKOFF_SECONDS=policy.ESCALATE_BACKOFF_SECONDS,
            should_escalate=policy.should_escalate, should_escalate_moving=policy.should_escalate_moving,
            poll_status=SimpleNamespace(is_escalated=lambda *args, **kwargs: False),
            coordinator=SimpleNamespace(vehicle_parked=parked, is_charging=charging,
                last_motion_at=now - motion_age if motion_age is not None else 0.0,
                command_policy=SimpleNamespace(can_wake=lambda: can_wake), set_poll_status=lambda **kwargs: None),
            mqtt_client=SimpleNamespace(connected=connected, last_state_msg_ts=now - 350,
                last_msg_ts=now - 350, last_meaningful_state_ts=now - stale,
                reconnect=lambda *args: calls.append(("reconnect",)) or True,
                send_query=lambda *args: calls.append(("query",)),
                send_command=lambda *args: calls.append(("command", *args))),
            hass=SimpleNamespace(async_add_executor_job=executor), watchdog_store=SimpleNamespace(async_save=save),
            _refresh_creds_sync=lambda *args: None,
        )
        with self.assertLogs("xpeng.test", level="INFO") as logs:
            await ns["_silence_watchdog"](None)
        self.assertFalse(any(r.levelno >= logging.ERROR for r in logs.records))
        return logs.records, calls

    async def test_known_parked_reconnect_is_info_and_still_queries(self):
        logs, calls = await self.tick()
        recovery = [r for r in logs if "reconnecting" in r.getMessage()]
        self.assertEqual([r.levelno for r in recovery], [logging.INFO])
        self.assertIn(("reconnect",), calls)
        self.assertIn(("query",), calls)
        self.assertNotIn("cloud-side", " ".join(r.getMessage() for r in logs))

    async def test_active_unknown_and_disconnected_connections_still_warn(self):
        for kwargs in ({"parked": False}, {"parked": None}, {"charging": True}, {"connected": False}):
            logs, _ = await self.tick(**kwargs)
            self.assertTrue(any(r.levelno == logging.WARNING for r in logs), kwargs)

    async def test_old_motion_diagnostic_acknowledges_uncertainty(self):
        logs, calls = await self.tick(stale=1860, motion_age=1860)
        text = " ".join(r.getMessage() for r in logs)
        self.assertIn("motion evidence 31min old", text)
        self.assertIn("current motion is unconfirmed", text)
        self.assertNotIn("cannot wake", text)
        self.assertEqual(sum(c[0] == "command" for c in calls), 1)

    async def test_disabled_waking_logs_only_the_passive_action(self):
        logs, calls = await self.tick(stale=1860, motion_age=1860, can_wake=False)
        text = " ".join(r.getMessage() for r in logs)
        self.assertNotIn("sending ONE force-upload", text)
        self.assertIn("waking is disabled", text)
        self.assertFalse(any(c[0] == "command" for c in calls))
        self.assertTrue(any(c[0] == "query" for c in calls))

    async def test_motion_age_describes_the_evidence_that_triggered_the_decision(self):
        logs, _ = await self.tick(stale=1860, motion_age=1860, park_during_save=True)
        self.assertIn("motion evidence 31min old", " ".join(r.getMessage() for r in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
