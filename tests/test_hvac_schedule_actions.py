"""Vehicle selection, schedule writes, refresh failures and timezone behavior."""

from _import_isolation import ImportIsolation
_IMPORTS = ImportIsolation()

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_consolidated_controls import Error, load, module

_IMPORTS.use(__import__("test_consolidated_controls"))

api = module(
    "custom_components.xpeng.xpeng_api",
    create_aircondition_appointment=Mock(),
    update_aircondition_appointment=Mock(),
    delete_aircondition_appointment=Mock(),
    list_aircondition_appointments=Mock(),
)
module(
    "custom_components.xpeng.__init__",
    _load_api_context=lambda entry: ({"test": "token"}, {"test": "tenant"}),
)
records = {
    "climate.car_a": SimpleNamespace(platform="xpeng", config_entry_id="a"),
    "climate.car_b": SimpleNamespace(platform="xpeng", config_entry_id="b"),
    "climate.other": SimpleNamespace(platform="other", config_entry_id="a"),
}
module(
    "homeassistant.helpers.entity_registry",
    async_get=lambda hass: SimpleNamespace(async_get=records.get),
)
schedules = load("custom_components.xpeng.hvac_schedules", "hvac_schedules.py")


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def make(self, two=False):
        for fn in vars(api).values():
            if isinstance(fn, Mock):
                fn.reset_mock(return_value=True, side_effect=True)
        api.list_aircondition_appointments.return_value = {
            "list": [{"appointmentId": "new"}],
            "nextStartTime": "07:30",
        }
        a = {
            "entry": object(),
            "coordinator": SimpleNamespace(vin="SYNTHETIC_A", set_rest_data=Mock()),
        }
        b = {
            "entry": object(),
            "coordinator": SimpleNamespace(vin="SYNTHETIC_B", set_rest_data=Mock()),
        }

        async def executor(fn, *args):
            return fn(*args)

        hass = SimpleNamespace(
            data={"xpeng": {"a": a, **({"b": b} if two else {})}},
            config=SimpleNamespace(time_zone="Europe/Athens"),
            async_add_executor_job=executor,
        )
        return hass, a, b

    async def test_create_uses_selected_car_timezone_and_refreshes_only_that_car(self):
        hass, a, b = self.make(two=True)
        await schedules.async_manage_schedule(
            hass,
            {
                "vehicle_entity_id": "climate.car_b",
                "start_time": "07:30",
                "days_of_week": [1, 3, 3, 7],
            },
            "create",
        )
        args = api.create_aircondition_appointment.call_args.args
        self.assertEqual(
            args[3:],
            (
                "SYNTHETIC_B",
                "AIR_CONDITION_OPEN",
                "07:30",
                1,
                69,
                1,
                "Europe/Athens",
                1,
            ),
        )
        api.list_aircondition_appointments.assert_called_once()
        self.assertEqual(
            api.list_aircondition_appointments.call_args.args[3:],
            ("SYNTHETIC_B", "AIR_CONDITION", "Europe/Athens"),
        )
        a["coordinator"].set_rest_data.assert_not_called()
        b["coordinator"].set_rest_data.assert_called_once_with(
            {
                "hvac_schedules": [{"appointmentId": "new"}],
                "hvac_next_start_time": "07:30",
            }
        )

    async def test_update_preserves_action_and_explicit_disabled_state(self):
        hass, a, _ = self.make()
        await schedules.async_manage_schedule(
            hass,
            {
                "appointment_id": "old",
                "start_time": "19:45",
                "enabled": False,
                "action_type": "EXISTING_ACTION",
                "time_zone": "Europe/London",
            },
            "update",
        )
        self.assertEqual(
            api.update_aircondition_appointment.call_args.args[3:],
            (
                "SYNTHETIC_A",
                "old",
                "EXISTING_ACTION",
                "19:45",
                0,
                0,
                0,
                "Europe/London",
                "UPDATE",
            ),
        )
        a["coordinator"].set_rest_data.assert_called_once()

    async def test_delete_and_refresh_are_separate_from_vehicle_hvac_commands(self):
        hass, _, _ = self.make()
        await schedules.async_manage_schedule(hass, {"appointment_id": "old"}, "delete")
        self.assertEqual(
            api.delete_aircondition_appointment.call_args.args[3:],
            ("SYNTHETIC_A", "old", "Europe/Athens"),
        )
        api.create_aircondition_appointment.assert_not_called()
        api.update_aircondition_appointment.assert_not_called()

    async def test_api_rejection_is_reported_and_does_not_fabricate_state(self):
        hass, a, _ = self.make()
        api.create_aircondition_appointment.side_effect = RuntimeError(
            "Synthetic rejection"
        )
        with self.assertRaisesRegex(Error, "rejected"):
            await schedules.async_manage_schedule(
                hass, {"start_time": "07:30"}, "create"
            )
        a["coordinator"].set_rest_data.assert_not_called()
        api.list_aircondition_appointments.assert_not_called()

    async def test_business_rejection_envelope_is_not_a_successful_write(self):
        hass, a, _ = self.make()
        for result in ({"code": 400, "msg": "Synthetic refusal"}, {"success": False}):
            api.create_aircondition_appointment.return_value = result
            with self.assertRaisesRegex(Error, "rejected"):
                await schedules.async_manage_schedule(
                    hass, {"start_time": "07:30"}, "create"
                )
        a["coordinator"].set_rest_data.assert_not_called()
        api.list_aircondition_appointments.assert_not_called()

    async def test_successful_write_failed_refresh_reports_uncertain_state(self):
        hass, a, _ = self.make()
        api.list_aircondition_appointments.side_effect = RuntimeError(
            "Synthetic outage"
        )
        with self.assertRaisesRegex(Error, "completed.*refreshed state"):
            await schedules.async_manage_schedule(
                hass, {"start_time": "07:30"}, "create"
            )
        api.create_aircondition_appointment.assert_called_once()
        a["coordinator"].set_rest_data.assert_not_called()

    async def test_malformed_list_preserves_previous_state(self):
        hass, a, _ = self.make()
        api.list_aircondition_appointments.return_value = {"unexpected": True}
        with self.assertRaisesRegex(Error, "could not be read"):
            await schedules.async_manage_schedule(hass, {}, "list")
        a["coordinator"].set_rest_data.assert_not_called()

    async def test_registered_schedule_gate_uses_selected_entry_policy(self):
        import ast
        from unittest.mock import AsyncMock
        from custom_components.xpeng import command_policy as policy

        hass, a, b = self.make(two=True)
        a["command_policy"] = policy.CommandPolicy.from_preset("full")
        b["command_policy"] = policy.CommandPolicy.from_preset("read_only")
        tree = ast.parse(
            (
                Path(__file__).resolve().parent.parent
                / "custom_components/xpeng/services.py"
            ).read_text(encoding="utf-8")
        )
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_gated"
        )
        seen = []

        async def guard(hass, call, service, group, p, *, entry_id=None):
            seen.append(p)
            if not p.allows(group):
                raise Error("Denied selected policy")

        handler = AsyncMock()
        env = {
            "hass": hass,
            "ServiceCall": object,
            "DOMAIN": "xpeng",
            "HomeAssistantError": Error,
            "service": "create_hvac_schedule",
            "group": policy.GROUP_COMFORT,
            "schedule_entry": schedules.schedule_entry,
            "_get_entry_data": lambda hass: a,
            "default_policy": policy.default_policy,
            "async_guard_service": guard,
            "handler": handler,
        }
        for key, value in (
            ("CREATE_HVAC_SCHEDULE", "create_hvac_schedule"),
            ("UPDATE_HVAC_SCHEDULE", "update_hvac_schedule"),
            ("DELETE_HVAC_SCHEDULE", "delete_hvac_schedule"),
            ("LIST_HVAC_SCHEDULES", "list_hvac_schedules"),
        ):
            env["SERVICE_" + key] = value
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                "services.py",
                "exec",
            ),
            env,
        )
        with self.assertRaisesRegex(Error, "selected policy"):
            await env["_gated"](
                SimpleNamespace(data={"vehicle_entity_id": "climate.car_b"})
            )
        self.assertIs(seen[0], b["command_policy"])
        handler.assert_not_called()

    def test_multiple_cars_require_explicit_target_and_foreign_entities_are_rejected(
        self,
    ):
        hass, a, b = self.make(two=True)
        with self.assertRaises(Error):
            schedules.schedule_entry(hass, {})
        for entity in ("climate.other", "climate.missing"):
            with self.assertRaises(Error):
                schedules.schedule_entry(hass, {"vehicle_entity_id": entity})
        self.assertIs(
            schedules.schedule_entry(hass, {"vehicle_entity_id": "climate.car_b"}), b
        )



_IMPORTS.finish(globals())

if __name__ == "__main__":
    unittest.main()
