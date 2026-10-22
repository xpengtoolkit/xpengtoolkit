"""REST feature ingestion remains optional and excludes unrelated vehicle data."""

import ast
import json
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

PKG = Path(__file__).resolve().parents[1] / "custom_components/xpeng"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_vehicle_capabilities as setup


class RestFeatureTests(unittest.TestCase):
    def make_fetch(self, local):
        tree = ast.parse((PKG / "__init__.py").read_text())
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_fetch_rest_data_sync"
        )
        api = types.ModuleType("custom_components.xpeng.xpeng_api")
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and node.module == "xpeng_api":
                for alias in node.names:
                    setattr(api, alias.name, Mock(return_value=None))
        api.get_vehicles.return_value = [
            {
                "vin": "SYNTHETIC",
                "vehicleTypeCode": "FJ",
                "vehicleTypeName": "F57a",
                "function": {
                    "REAR_SEAT_WIND": False,
                    "AVM": "true",
                    "private_identifier": "synthetic",
                },
            }
        ]
        api.get_remote_capabilities.return_value = {"capabilities": ["SENTINEL_MODE"]}
        if isinstance(local, Exception):
            api.get_authenticated_data.side_effect = local
        else:
            api.get_authenticated_data.return_value = local
        sys.modules[api.__name__] = api
        ns = {
            "__name__": "custom_components.xpeng.rest_test",
            "__package__": "custom_components.xpeng",
            "ConfigEntry": object,
            "Any": object,
            "_LOGGER": logging.getLogger(__name__),
            "_load_api_context": lambda e: ({"test": True}, {"test": True}),
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "rest_test", "exec"), ns)
        return ns[fn.name], api

    def test_retains_only_capability_booleans_and_uses_read_only_overlay(self):
        fetch, api = self.make_fetch(
            {"isSupportChargePortControl": "false", "private": "synthetic"}
        )
        result = fetch(types.SimpleNamespace(options={}), "SYNTHETIC")
        self.assertEqual(
            result["vehicle_hardware"], {"REAR_SEAT_WIND": False, "AVM": True}
        )
        self.assertEqual(
            result["vehicle_local_config"], {"isSupportChargePortControl": False}
        )
        call = api.get_authenticated_data.call_args.args
        self.assertEqual(call[3:5], ("/ocm/app/config/get", "POST"))
        self.assertEqual(
            json.loads(call[5]),
            {
                "biz": "app",
                "module": "onlineVehicle",
                "vin": "SYNTHETIC",
                "vehicleTypeCode": "FJ",
                "vehicleTypeName": "F57a",
            },
        )

    def test_absent_or_failed_overlay_does_not_break_other_rest_data(self):
        for response in ({"code": 500}, {}, None, ConnectionError("offline")):
            fetch, api = self.make_fetch(response)
            result = fetch(types.SimpleNamespace(options={}), "SYNTHETIC")
            self.assertNotIn("vehicle_local_config", result)
            self.assertEqual(result["vehicle_type_code"], "FJ")
            self.assertIn("remote_capabilities", result)


if __name__ == "__main__":
    unittest.main()
