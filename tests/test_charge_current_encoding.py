"""Official app's 1..27 wire index, independently of Home Assistant stubs."""
import ast
import importlib.util
from pathlib import Path
import unittest

PKG = Path(__file__).resolve().parents[1] / "custom_components/xpeng"
spec = importlib.util.spec_from_file_location("charge_current_encoding", PKG / "charge_current.py")
encoding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(encoding)


class CurrentEncodingTests(unittest.TestCase):
    def test_all_twenty_seven_slider_positions_round_trip(self):
        for amperes in range(6, 33):
            with self.subTest(amperes=amperes):
                raw = encoding.current_limit_index(amperes)
                self.assertEqual(raw, amperes - 5)
                self.assertEqual(encoding.current_limit_amperes(raw), amperes)

    def test_owner_measured_old_commands_explain_the_app_display(self):
        self.assertEqual(encoding.current_limit_amperes(20), 25)
        self.assertEqual(encoding.current_limit_amperes(27), 32)

    def test_unknown_and_invalid_wire_values_do_not_become_six_amps(self):
        for raw in (None, -1, 0, 28, 32, 1.5, True, "bad", float("nan"), float("inf")):
            with self.subTest(raw=raw):
                self.assertIsNone(encoding.current_limit_amperes(raw))

    def test_numeric_wire_strings_are_converted_once(self):
        self.assertEqual(encoding.current_limit_amperes("11"), 16)

    def test_sensor_description_converts_without_changing_the_wire_field(self):
        tree = ast.parse((PKG / "const.py").read_text(encoding="utf-8"))
        description = next(
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and any(k.arg == "key" and isinstance(k.value, ast.Constant)
                    and k.value.value == "charge_limit" for k in node.keywords)
        )
        fields = {k.arg: k.value for k in description.keywords}
        self.assertEqual(ast.literal_eval(fields["value_path"]),
                         "msg_content.instant.charge.electric_current_limit")
        fn = eval(compile(ast.Expression(fields["value_fn"]), "sensor-description", "eval"),
                  {"current_limit_amperes": encoding.current_limit_amperes})
        self.assertEqual(fn(20), 25)
        self.assertEqual(fn(27), 32)


if __name__ == "__main__":
    unittest.main()
