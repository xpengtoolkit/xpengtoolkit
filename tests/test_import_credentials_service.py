"""The unattended path: xpeng.import_credentials must be as strict as the card.

A scheduled renewal calls this service, so it has to validate the same way the
Repairs flow does - and it must not leak the credentials it handles.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

COMP = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "xpeng"
INIT = (COMP / "__init__.py").read_text(encoding="utf-8")
MODULE = COMP / "credential_import.py"
SOURCE = MODULE.read_text(encoding="utf-8")


def _function(name: str, source: str = SOURCE) -> ast.AST:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _keywords(call: ast.Call) -> dict:
    return {kw.arg: kw.value for kw in call.keywords}


class ServiceRegistrationTests(unittest.TestCase):
    def test_the_service_is_registered_once_in_async_setup(self):
        self.assertIn("async def async_setup(", INIT)
        self.assertIn("hass.services.async_register(", INIT)
        self.assertIn("SERVICE_IMPORT_CREDENTIALS", INIT)
        self.assertIn("has_service(DOMAIN, SERVICE_IMPORT_CREDENTIALS)", INIT)

    def test_it_returns_a_response_and_cannot_run_in_parallel_by_accident(self):
        self.assertIn("SupportsResponse.ONLY", INIT)

    def test_the_schema_accepts_both_contents_and_paths(self):
        schema = ast.parse(SOURCE)
        keys = {
            node.value
            for node in ast.walk(schema)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for key in ("tokens_json", "tokens_path", "tenant_json", "tenant_path", "entry_id", "vin"):
            self.assertIn(key, keys)

    def test_a_missing_entry_is_reported_not_ignored(self):
        self.assertIn("No XPENG config entry to import credentials into", INIT)


class ImportBehaviourTests(unittest.TestCase):
    def test_both_halves_are_required(self):
        self.assertIn('raise _TokenImportError(\n                "invalid_tokens"', SOURCE)
        self.assertIn('raise _TokenImportError(\n                "invalid_tenant"', SOURCE)

    def test_the_pair_is_validated_by_minting_mqtt_credentials(self):
        # This is what stops a stale pair from silently replacing a working one.
        self.assertIn("_import_mqtt_credentials(tokens, tenant)", SOURCE)

    def test_a_renewal_is_applied_without_a_reload(self):
        """A renewal is the same car, so tearing every entity down buys nothing.

        Both reload paths have to be handled: the entry-update listener reloads on
        any update unless the one-shot flag says otherwise, and this function used
        to reload as well - two teardowns of every entity per renewal, which is what
        made the integration flap between ``unavailable`` and its values.
        """
        self.assertIn("_clear_reauth_repair(hass, entry)", SOURCE)
        self.assertIn("async_reload(entry.entry_id)", SOURCE)
        self.assertIn("reload_needed = client is None or _identity_moved(", SOURCE)
        self.assertIn('refresh_state["skip_reload"] = True', SOURCE)
        self.assertIn("await _apply_to_running_client(", SOURCE)

    def test_the_running_client_path_never_reloads(self):
        node = _function("_apply_to_running_client")
        calls = ast.dump(node)
        self.assertIn("stage_credentials", calls)
        self.assertIn("reconnect", calls)
        # The clocks a reload would have refreshed, and the success signal.
        self.assertIn("set_credential_timestamp", calls)
        self.assertIn("set_last_renewal", calls)
        self.assertIn("_record_credential_success", calls)
        self.assertNotIn("async_reload", calls)

    def test_the_device_identity_follows_the_tenant(self):
        self.assertIn("CONF_CONFIGURED_DEVICE_ID", SOURCE)
        self.assertIn('tenant.get("deviceId")', SOURCE)

    def test_the_response_carries_no_credentials(self):
        node = _function("async_import_credentials")
        returns = [
            n for n in ast.walk(node) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
        ]
        self.assertTrue(returns, "expected a dict return")
        keys = {
            k.value for k in returns[-1].value.keys if isinstance(k, ast.Constant)
        }
        self.assertEqual(
            keys,
            {"entry_id", "vin", "device_id", "account", "client_id"},
            "the service response must not include tokens or passwords",
        )
        self.assertNotIn("password", keys)
        self.assertNotIn("tokens", keys)

    def test_the_log_line_does_not_include_the_credentials(self):
        node = _function("async_import_credentials")
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in ("info", "debug", "warning"):
                text = ast.dump(call)
                self.assertNotIn("password", text)
                self.assertNotIn("CONF_LOGIN_TOKENS", text)


class IdentityMovedTests(unittest.TestCase):
    """The rule that separates a renewal from a pair for another car.

    Extracted and executed the way the coordinator helpers are already tested, so
    the decision is exercised without Home Assistant. False means "apply it to the
    running client" (no flap); True means reload, because the VIN is in every
    entity id and the tenant's device id is the key the pair is sealed with.
    """

    @staticmethod
    def _fn():
        import textwrap

        start = SOURCE.index("def _identity_moved(")
        end = start + SOURCE[start:].index("\nasync def ")
        namespace: dict = {}
        exec(
            compile(
                "CONF_VIN = 'vin'\nCONF_CONFIGURED_DEVICE_ID = 'configured_device_id'\n"
                + textwrap.dedent(SOURCE[start:end]),
                "<credential_import>",
                "exec",
            ),
            namespace,
        )
        return namespace["_identity_moved"]

    class _Entry:
        def __init__(self, data, options):
            self.data = data
            self.options = options

    def _entry(self, vin="XPENGVIN0000000000", device="dev-1"):
        return self._Entry({"vin": vin}, {"configured_device_id": device})

    def test_the_same_car_is_not_a_move(self):
        self.assertFalse(
            self._fn()(self._entry(), "XPENGVIN0000000000", "dev-1")
        )

    def test_a_different_vin_is_a_move(self):
        self.assertTrue(self._fn()(self._entry(), "OTHER123456789012", "dev-1"))

    def test_a_different_device_id_is_a_move(self):
        self.assertTrue(
            self._fn()(self._entry(), "XPENGVIN0000000000", "dev-2")
        )

    def test_an_entry_that_has_neither_yet_is_a_move(self):
        # Nothing to compare against: the pair is about to define the identity, so
        # take the reload rather than swap it under entities built without one.
        self.assertTrue(self._fn()(self._entry(vin="", device=""), "XPENGVIN0000000000", "dev-1"))

    def test_case_does_not_make_it_a_move(self):
        self.assertFalse(
            self._fn()(self._entry(vin="xpengvin0000000000"), "XPENGVIN0000000000", "dev-1")
        )

    def test_a_pair_with_no_identity_of_its_own_is_not_a_move(self):
        self.assertFalse(self._fn()(self._entry(), "", ""))


class HelperReuseTests(unittest.TestCase):
    def test_it_reuses_the_config_flow_helpers_rather_than_reimplementing(self):
        for helper in (
            "_import_mqtt_credentials",
            "_read_text_file",
            "_tokens_from_text",
            "_tenant_from_text",
            "_serialize_tenant",
            "_vin_from_tokens",
        ):
            self.assertIn(helper, SOURCE, f"{helper} should come from config_flow")


class LastRenewalClockTests(unittest.TestCase):
    """When a renewal last ran must be visible without anyone building anything.

    The automation in docs/UNATTENDED_RENEWAL.md is optional and needs a helper, a
    `rest_command` and a secret; `sensor.xpeng_<car>_last_renewal` needs none of that,
    but only if every path that installs a pair stamps it - which is what these pin.
    The app renews through `import_credentials`; the config flow covers a pasted pair,
    an email or Google login, a reauth, and the flow the app drives on a first install.
    """

    CF = (COMP / "config_flow.py").read_text(encoding="utf-8")
    COORD = (COMP / "coordinator.py").read_text(encoding="utf-8")
    SENSOR = (COMP / "sensor.py").read_text(encoding="utf-8")

    def test_the_service_path_stamps_it(self):
        """Every renewal the app makes arrives through this function, so it is the clock."""
        self.assertIn("renewed_at = time.time()", SOURCE)
        self.assertIn("CONF_LAST_RENEWAL: renewed_at", SOURCE)

    def test_every_flow_side_arrival_stamps_it(self):
        self.assertIn("def _credential_stamp()", self.CF)
        self.assertEqual(
            self.CF.count("**_credential_stamp(),"),
            5,
            "a new credential-arrival site in the flow must stamp the clock too",
        )

    def test_it_lives_in_entry_data_and_is_read_back_at_setup(self):
        const = (COMP / "const.py").read_text(encoding="utf-8")
        self.assertIn('CONF_LAST_RENEWAL: Final = "last_renewal"', const)
        self.assertIn(
            "coordinator.set_last_renewal(entry.data.get(CONF_LAST_RENEWAL))", INIT
        )

    def test_the_sensor_is_a_timestamp_and_is_registered(self):
        start = self.SENSOR.index("class XPengLastRenewalSensor(")
        body = self.SENSOR[start : self.SENSOR.index("\nclass ", start + 10)]
        self.assertIn("_attr_device_class = SensorDeviceClass.TIMESTAMP", body)
        self.assertIn('_attr_translation_key = "last_renewal"', body)
        self.assertIn("return self.coordinator.last_renewal", body)
        self.assertIn(
            "entities.append(XPengLastRenewalSensor(coordinator))", self.SENSOR
        )


class StampParsingTests(unittest.TestCase):
    """The conversion itself, exercised without Home Assistant or a coordinator.

    `set_last_renewal` takes whatever the entry holds - an epoch written by an import,
    or nothing at all on an entry that predates the key - and a diagnostic entity must
    not be able to fail entry setup because of a malformed value.
    """

    class _Fake:
        last_renewal = "unset"

    @staticmethod
    def _setter():
        import textwrap
        from datetime import datetime, timezone

        source = LastRenewalClockTests.COORD
        start = source.index("    def set_last_renewal(")
        end = source.index("\n    def ", start + 10)
        namespace = {"datetime": datetime, "timezone": timezone}
        exec(
            compile(textwrap.dedent(source[start:end]), "<coordinator>", "exec"),
            namespace,
        )
        return namespace["set_last_renewal"]

    def test_an_epoch_becomes_an_aware_utc_datetime(self):
        from datetime import timezone

        fake = self._Fake()
        self._setter()(fake, 1700000000)
        self.assertEqual(fake.last_renewal.timestamp(), 1700000000)
        self.assertEqual(fake.last_renewal.tzinfo, timezone.utc)

    def test_nothing_means_unknown_and_junk_does_not_raise(self):
        setter = self._setter()
        for value in (None, "", "not-a-number", object()):
            fake = self._Fake()
            setter(fake, value)
            self.assertIsNone(fake.last_renewal, f"{value!r} should read as unknown")


class VinGuardTests(unittest.TestCase):
    """A pair for another car must not be installed into this entry (issue #11).

    The two-install failure this stops: a renewal app with the other car's
    `pair_dir` (or the wrong `entry_id`) imported a G9 pair into the X9 entry.
    The entry kept its own VIN, title and entity ids while carrying the other
    account's tokens, client id and device id, so the account sensor and
    `credentials_valid` started reporting the other car — and the response still
    looked right, because the VIN is read from the entry first.
    """

    @staticmethod
    def _fn():
        import textwrap

        start = SOURCE.index("def _vin_check(")
        end = SOURCE.index("\ndef _identity_moved(")
        namespace: dict = {}
        exec(
            compile(
                textwrap.dedent(SOURCE[start:end]),
                "<credential_import>",
                "exec",
            ),
            namespace,
        )
        return namespace["_vin_check"]

    def test_the_same_vin_in_any_case_is_not_a_mismatch(self):
        check = self._fn()
        self.assertIsNone(check("l1nnsgha1sb000001", "L1NNSGHA1SB000001"))

    def test_a_pair_for_another_car_names_both_vins(self):
        check = self._fn()
        self.assertEqual(
            check("L1NNSGHA1SB000001", "OTHER123456789012"),
            ("OTHER123456789012", "L1NNSGHA1SB000001"),
        )

    def test_a_pair_without_a_vin_is_not_refused(self):
        # An older capture carries no VIN; refusing it would break upgrades.
        check = self._fn()
        self.assertIsNone(check("L1NNSGHA1SB000001", ""))
        self.assertIsNone(check("", ""))

    def test_an_entry_without_a_vin_adopts_the_pair_vin(self):
        # Entries created before the field existed must keep working.
        check = self._fn()
        self.assertIsNone(check("", "L1NNSGHA1SB000001"))

    def test_the_caller_declared_vin_is_checked_too(self):
        check = self._fn()
        self.assertEqual(
            check("", "OTHER123456789012", "L1NNSGHA1SB000001"),
            ("OTHER123456789012", "L1NNSGHA1SB000001"),
        )
        self.assertIsNone(
            check("", "L1NNSGHA1SB000001", "l1nnsgha1sb000001")
        )

    def test_the_import_checks_before_it_mints(self):
        """A mismatch must not spend an API call or touch the entry."""
        self.assertIn("_vin_check(", SOURCE)
        self.assertIn('"vin_mismatch"', SOURCE)
        self.assertLess(
            SOURCE.index("_vin_check("),
            SOURCE.index("_import_mqtt_credentials(tokens, tenant)"),
            "the guard has to run before the minting/import work",
        )


if __name__ == "__main__":
    unittest.main()
