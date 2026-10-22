#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for xpeng_vin. Standard library only — no pytest required.

    python3 tests/test_xpeng_vin.py
    python3 -m unittest discover -s tests -v

Also runs the module's own embedded self-test.
"""

from __future__ import annotations

# Load the stdlib module before the component directory can shadow it.
import calendar  # noqa: F401
import json
import os
import pathlib
# Cache the stdlib extension before component select.py can shadow it.
import select  # noqa: F401
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _component  # noqa: E402

# `xpeng_vin` at the repo root is a vendored copy of the shipped
# `vin_descriptor.py`, kept in sync by sync_vin_module.sh. Import the shipped
# module so this tests what actually ships, rather than a duplicate that can
# silently drift. (The two were byte-identical when this was changed.)
sys.path.insert(0, str(_component.component_dir()))
import vin_descriptor as xpeng_vin  # noqa: E402

#: This project's own vehicle — the one VIN we can verify end to end.
OUR_VIN = "L1NNSGHA1SB000001"


class TestPrimitives(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(xpeng_vin.normalise(" l1nnsgha1sb000001 "), OUR_VIN)
        self.assertEqual(xpeng_vin.normalise("L1N-NSGHA-1SB000001"), OUR_VIN)

    def test_format_validation(self):
        self.assertTrue(xpeng_vin.is_valid_format(OUR_VIN))
        for bad in ("", "TOOSHORT", "L1NNSGHA1SB00000I", "L1NNSGHA1SB00000O",
                    "L1NNSGHA1SB00000Q", "L1NNSGHA1SB0000011"):
            with self.subTest(bad=bad):
                self.assertFalse(xpeng_vin.is_valid_format(bad))

    def test_check_digit_known_good(self):
        # XPENG uses ISO 3779 / GB 16735 mod-11 — verified on all real samples.
        for vin in (OUR_VIN, "L1NSPGHBXNA145934", "L1NBSHTP8TB041733",
                    "L1NBSRTY2TB025574"):
            with self.subTest(vin=xpeng_vin.mask_vin(vin)):
                self.assertIs(xpeng_vin.check_digit_ok(vin), True)

    def test_check_digit_x_is_remainder_ten(self):
        """'X' is the legal encoding of remainder 10, not an error."""
        vin = "L1NSPGHBXNA145934"
        self.assertEqual(vin[8], "X")
        self.assertEqual(xpeng_vin.check_digit(vin), "X")
        self.assertIs(xpeng_vin.check_digit_ok(vin), True)

    def test_check_digit_catches_redaction(self):
        """Redacted VINs pass format validation and are caught only here.

        'X' is a legal VIN character, so the masked forum fixtures look
        well-formed — the check digit is what proves they were altered.
        """
        for redacted in ("L1NNSGHA2SB18XXXX", "L1NNSGHA1TDXXXXXX",
                         "L1NNSGHAXTDXXXXXX"):
            with self.subTest(vin=redacted):
                self.assertTrue(xpeng_vin.is_valid_format(redacted))
                self.assertIs(xpeng_vin.check_digit_ok(redacted), False)

    def test_check_digit_uncomputable(self):
        self.assertIsNone(xpeng_vin.check_digit("nonsense"))

    def test_model_year_cycle(self):
        cases = [("S", 2025), ("T", 2026), ("N", 2022), ("R", 2024), ("A", 2010)]
        for code, expected in cases:
            with self.subTest(code=code):
                self.assertEqual(
                    xpeng_vin.model_year_from_code(code, reference_year=2026), expected
                )

    def test_model_year_prefers_recent_past_not_future(self):
        """A "nearest candidate" rule would read 'A' as 2040; that is wrong."""
        self.assertEqual(
            xpeng_vin.model_year_from_code("A", reference_year=2026), 2010
        )
        self.assertEqual(
            xpeng_vin.model_year_from_code("Y", reference_year=2026), 2000
        )
        # The one-year lookahead keeps genuine next-model-year vehicles working.
        self.assertEqual(
            xpeng_vin.model_year_from_code("T", reference_year=2025), 2026
        )

    def test_model_year_rejects_illegal_codes(self):
        for code in ("I", "O", "Q", "U", "Z", "", "SS"):
            with self.subTest(code=code):
                self.assertIsNone(
                    xpeng_vin.model_year_from_code(code, reference_year=2026)
                )

    def test_mask_vin(self):
        self.assertEqual(xpeng_vin.mask_vin(OUR_VIN), "L1NNSGHA1SB******")
        self.assertEqual(xpeng_vin.mask_vin("SHORT"), "SHORT")


class TestDecode(unittest.TestCase):
    def test_our_vehicle(self):
        info = xpeng_vin.decode(OUR_VIN)
        self.assertTrue(info.valid)
        self.assertEqual(info.warnings, ())
        self.assertEqual(info.wmi, "L1N")
        self.assertEqual(info.make, "XPENG")
        self.assertEqual(info.vds, "NSGHA")
        self.assertEqual(info.model_family, "G6")
        self.assertEqual(info.model_name, "XPENG G6")
        self.assertEqual(info.model_year, 2025)
        self.assertEqual(info.model_year_code, "S")
        self.assertEqual(info.plant, "Guangzhou")
        self.assertEqual(info.plant_country, "China")
        self.assertEqual(info.serial, "000001")
        self.assertIs(info.check_digit_valid, True)
        self.assertEqual(info.vds_source, "builtin")

    def test_display_name_fallbacks(self):
        self.assertEqual(xpeng_vin.decode(OUR_VIN).display_name, "XPENG G6")
        unknown = xpeng_vin.decode("L1NZZZZZ3SB000001")
        self.assertIn("unmapped", unknown.display_name)

    def test_known_models(self):
        expectations = {
            "L1NSPGHBXNA145934": ("P7", "NHQ7000BEVEG"),
            "L1NBSHTP8TB041733": ("X9", "NHQ6530SHEVHA"),
            "L1NBSRTY2TB025574": (None, "NHQ6510REEVRB"),
        }
        for vin, (family, miit) in expectations.items():
            with self.subTest(vin=vin):
                info = xpeng_vin.decode(vin)
                self.assertEqual(info.model_family, family)
                self.assertEqual(info.miit_model_code, miit)
                self.assertTrue(info.valid)

    def test_g9_vds_is_known(self):
        """NSGHB: reported by a G9 owner, beside the confirmed NSGHA/NSGHC."""
        info = xpeng_vin.decode("L1NNSGHB2SD000005")
        self.assertTrue(info.valid)
        self.assertEqual(info.model_family, "G9")
        self.assertEqual(info.model_name, "XPENG G9")
        self.assertEqual(info.vds_source, "builtin")
        self.assertEqual(info.warnings, ())

    def test_the_nsgh_series_is_recorded_whole(self):
        """The G9 row rests on the series, so all three siblings must stay."""
        for vds in ("NSGHA", "NSGHB", "NSGHC"):
            with self.subTest(vds=vds):
                self.assertIn(vds, xpeng_vin.VDS_TABLE)

    def test_the_documented_cli_is_the_one_that_ships(self):
        """The module was renamed; a user was told to run the old filename.

        It shipped no such file, so the instruction could not be followed. The
        banner names the module too, and both are easy to miss in a rename.
        """
        source = pathlib.Path(xpeng_vin.__file__).read_text(encoding="utf-8")
        command_lines = [
            line for line in source.splitlines()
            if "xpeng_vin.py" in line and ("python" in line or "run" in line.lower())
        ]
        self.assertEqual(command_lines, [], "docs name an unshipped CLI")
        self.assertIn("vin_descriptor.py --selftest", source)

    def test_vds_never_claims_trim_or_battery(self):
        """The core correctness guarantee of this module."""
        for vds, entry in xpeng_vin.VDS_TABLE.items():
            with self.subTest(vds=vds):
                self.assertEqual(entry["encodes"], ["model_family"])
        info = xpeng_vin.decode(OUR_VIN)
        self.assertIsNone(info.powertrain)  # unknowable from a G6 VIN
        self.assertEqual(info.vds_encodes, ("model_family",))

    def test_falsified_claims_are_recorded(self):
        claims = xpeng_vin.VDS_TABLE["NSGHA"]["falsified_claims"]
        self.assertGreaterEqual(len(claims), 3)
        self.assertTrue(any("AWD" in c for c in claims))

    def test_nsgha_spans_variants(self):
        """NSGHA must record that it covers multiple variants, not one."""
        observed = " ".join(xpeng_vin.VDS_TABLE["NSGHA"]["observed_variants"])
        self.assertIn("LongRange", observed)
        self.assertIn("Performance", observed)

    def test_malformed_input_degrades_gracefully(self):
        for bad in ("", "   ", "TOOSHORT", "L1NNSGHA1SB00000I"):
            with self.subTest(bad=bad):
                info = xpeng_vin.decode(bad)   # must not raise
                self.assertFalse(info.valid)
                self.assertTrue(info.errors)

    def test_unknown_vds_warns_rather_than_guesses(self):
        info = xpeng_vin.decode("L1NZZZZZ3SB000001")
        self.assertTrue(info.valid)          # format is fine
        self.assertIsNone(info.model_family)
        self.assertEqual(info.vds_source, "unknown")
        self.assertTrue(any("not in the table" in w for w in info.warnings))

    def test_strict_check_digit(self):
        # Flip the check digit to a different legal character.
        tampered = OUR_VIN[:8] + "4" + OUR_VIN[9:]
        lenient = xpeng_vin.decode(tampered)
        self.assertTrue(lenient.valid)
        self.assertTrue(any("check digit" in w for w in lenient.warnings))
        strict = xpeng_vin.decode(tampered, strict_check_digit=True)
        self.assertFalse(strict.valid)

    def test_unknown_wmi_warns(self):
        info = xpeng_vin.decode("XYZNSGHA3SB000001")
        self.assertTrue(any("WMI" in w for w in info.warnings))

    def test_unknown_plant_warns(self):
        info = xpeng_vin.decode("L1NNSGHA3SZ000001")
        self.assertIsNone(info.plant)
        self.assertTrue(any("plant code" in w for w in info.warnings))

    def test_reference_year_disambiguation(self):
        self.assertEqual(xpeng_vin.decode("L1NNSGHA1SB000001").model_year, 2025)
        # 30 years earlier, the same code resolves to 1995.
        self.assertEqual(
            xpeng_vin.decode("L1NNSGHA1SB000001", reference_year=1996).model_year, 1995
        )

    def test_to_dict_is_json_serialisable(self):
        payload = json.loads(xpeng_vin.decode(OUR_VIN).to_json())
        self.assertEqual(payload["vin"], OUR_VIN)
        self.assertIsInstance(payload["vds_evidence"], list)
        self.assertIsInstance(payload["vds_encodes"], list)

    def test_explain_hides_serial_by_default(self):
        text = xpeng_vin.explain(OUR_VIN)
        self.assertNotIn("000001", text)
        self.assertIn("L1NNSGHA1SB******", text)
        self.assertIn("do NOT encode", text)
        self.assertIn("000001", xpeng_vin.explain(OUR_VIN, mask=False))


class TestOverlayAndLearn(unittest.TestCase):
    def test_overlay_overrides_builtin(self):
        overlay = {"NSGHA": {"model_family": "G6", "model_name": "Custom Name",
                             "confidence": "reported"}}
        info = xpeng_vin.decode(OUR_VIN, overlay=overlay)
        self.assertEqual(info.model_name, "Custom Name")
        self.assertEqual(info.vds_source, "overlay")

    def test_overlay_disagreement_warns(self):
        overlay = {"NSGHA": {"model_family": "NOT-A-G6", "model_name": "X"}}
        info = xpeng_vin.decode(OUR_VIN, overlay=overlay)
        self.assertTrue(any("overrides builtin" in w for w in info.warnings))

    def test_overlay_can_add_unknown_vds(self):
        overlay = {"ZZZZZ": {"model_family": "TestModel", "model_name": "Test",
                             "confidence": "reported"}}
        info = xpeng_vin.decode("L1NZZZZZ3SB000001", overlay=overlay)
        self.assertEqual(info.model_family, "TestModel")

    def test_learn_does_not_fabricate(self):
        result = xpeng_vin.learn("L1NZZZZZ3SB000001", model_name="XPENG Test",
                                 source="unittest")
        self.assertEqual(result["vds"], "ZZZZZ")
        # VIN cannot supply these — they must stay None rather than be guessed.
        self.assertIsNone(result["entry"]["powertrain"])
        self.assertIsNone(result["entry"]["miit_model_code"])
        self.assertEqual(result["entry"]["encodes"], ["model_family"])
        self.assertIn("ZZZZZ", result["overlay"])

    def test_learn_rejects_unusable_vin(self):
        with self.assertRaises(ValueError):
            xpeng_vin.learn("nonsense")

    def test_learn_merges_evidence(self):
        result = xpeng_vin.learn(OUR_VIN, model_name="XPENG G6", source="unittest")
        self.assertTrue(any("unittest" in e for e in result["entry"]["evidence"]))
        # Existing corroboration must survive.
        self.assertGreaterEqual(len(result["entry"]["evidence"]), 4)

    def test_overlay_round_trip(self):
        overlay = {"ZZZZZ": {"model_family": "TestModel", "model_name": "Test"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "overlay.json")
            xpeng_vin.save_overlay(path, overlay)
            self.assertEqual(xpeng_vin.load_overlay(path), overlay)

    def test_load_overlay_accepts_wrapped_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "overlay.json")
            pathlib.Path(path).write_text(
                json.dumps({"vds": {"ZZZZZ": {"model_family": "TestModel"}}}),
                encoding="utf-8",
            )
            self.assertEqual(
                xpeng_vin.load_overlay(path)["ZZZZZ"]["model_family"], "TestModel"
            )

    def test_load_overlay_normalises_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "overlay.json")
            pathlib.Path(path).write_text(
                json.dumps({"zzzzz": {"model_family": "TestModel"}}), encoding="utf-8"
            )
            self.assertIn("ZZZZZ", xpeng_vin.load_overlay(path))

    def test_merge(self):
        merged = xpeng_vin.merge(
            {"A": {"model_family": "One", "confidence": "reported"}},
            {"A": {"model_family": "Two"}},
        )
        self.assertEqual(merged["A"]["model_family"], "Two")
        self.assertEqual(merged["A"]["confidence"], "reported")


class TestBulkHelpers(unittest.TestCase):
    def test_decode_many_preserves_order(self):
        vins = [OUR_VIN, "L1NSPGHBXNA145934"]
        infos = xpeng_vin.decode_many(vins)
        self.assertEqual([i.vin for i in infos], vins)

    def test_group_by_vds(self):
        grouped = xpeng_vin.group_by_vds(
            [OUR_VIN, "L1NNSGHA1TD123456", "L1NSPGHBXNA145934"]
        )
        self.assertEqual(len(grouped["NSGHA"]), 2)
        self.assertEqual(len(grouped["SPGHB"]), 1)

    def test_unknown_vds(self):
        self.assertEqual(xpeng_vin.unknown_vds([OUR_VIN]), [])
        self.assertEqual(
            xpeng_vin.unknown_vds(["L1NZZZZZ3SB000001"]), ["ZZZZZ"]
        )


class TestSeedDataIntegrity(unittest.TestCase):
    def test_all_seed_entries_decode(self):
        for observation in xpeng_vin.SEED_OBSERVATIONS:
            with self.subTest(vin=observation["vin"]):
                info = xpeng_vin.decode(observation["vin"])
                self.assertTrue(info.valid)
                self.assertEqual(info.model_year, observation["my"])
                if observation.get("miit"):
                    self.assertEqual(info.miit_model_code, observation["miit"])

    def test_redacted_fixtures_are_flagged(self):
        redacted = [o for o in xpeng_vin.SEED_OBSERVATIONS if o.get("redacted")]
        self.assertEqual(len(redacted), 3)
        for observation in redacted:
            self.assertIs(xpeng_vin.check_digit_ok(observation["vin"]), False)

    def test_synthetic_fixtures_are_flagged(self):
        synthetic = [o for o in xpeng_vin.SEED_OBSERVATIONS if o.get("synthetic")]
        self.assertEqual(len(synthetic), 1)
        self.assertIs(xpeng_vin.check_digit_ok(synthetic[0]["vin"]), False)


class TestRoverApprovals(unittest.TestCase):
    """Australia's ROVER portal is the AU-market authority for XPENG."""

    def test_only_two_approvals_known(self):
        self.assertEqual(len(xpeng_vin.ROVER_APPROVALS), 2)
        models = {a["model"] for a in xpeng_vin.ROVER_APPROVALS}
        self.assertEqual(len(models), 2)
        # No G9 or P7 is type-approved in Australia — record that explicitly.
        self.assertFalse(any("G9" in m or "P7" in m for m in models))

    def test_rover_check_digit_expectations_hold(self):
        for approval in xpeng_vin.ROVER_APPROVALS:
            with self.subTest(approval=approval["approval"]):
                self.assertIs(
                    xpeng_vin.check_digit_ok(approval["typical_vin"]),
                    approval["check_digit_valid"],
                )

    def test_g6_rover_vin_corroborates_nsgha(self):
        """ROVER's real G6 VIN uses the same VDS as this project's own car."""
        g6 = next(a for a in xpeng_vin.ROVER_APPROVALS if a["model"] == "G6")
        self.assertEqual(g6["vds"], "NSGHA")
        self.assertEqual(xpeng_vin.decode(g6["typical_vin"]).model_family, "G6")
        self.assertEqual(xpeng_vin.decode(OUR_VIN).vds, g6["vds"])

    def test_x9_and_g6_differ_only_at_position_eight(self):
        g6, x9 = xpeng_vin.ROVER_APPROVALS
        self.assertEqual(g6["typical_vin"][:7], x9["typical_vin"][:7])
        self.assertEqual((g6["typical_vin"][7], x9["typical_vin"][7]), ("A", "C"))

    def test_x9_has_two_vds_codes(self):
        """The X9 nameplate spans NSGHC (BEV, AU) and BSHTP (EREV, CN)."""
        self.assertEqual(xpeng_vin.VDS_TABLE["NSGHC"]["model_family"], "X9")
        self.assertEqual(xpeng_vin.VDS_TABLE["BSHTP"]["model_family"], "X9")
        self.assertNotEqual(
            xpeng_vin.VDS_TABLE["NSGHC"]["powertrain"],
            xpeng_vin.VDS_TABLE["BSHTP"]["powertrain"],
        )
        # The BSHTP entry must carry the caveat rather than silently disagree.
        self.assertIn("caveat", xpeng_vin.VDS_TABLE["BSHTP"])

    def test_nsghc_decodes_to_x9(self):
        info = xpeng_vin.decode("L1NNSGHCXRB123456")
        self.assertEqual(info.model_family, "X9")
        self.assertEqual(info.model_year, 2024)
        self.assertEqual(info.vds_source, "builtin")

    def test_every_table_entry_has_evidence(self):
        for vds, entry in xpeng_vin.VDS_TABLE.items():
            with self.subTest(vds=vds):
                self.assertTrue(entry.get("evidence"), f"{vds} has no evidence")
                self.assertIn(entry["confidence"], ("confirmed", "reported"))

    def test_every_plant_entry_has_evidence(self):
        for key, entry in xpeng_vin.PLANT_TABLE.items():
            with self.subTest(plant=key):
                self.assertTrue(entry.get("evidence"))
                self.assertIn(entry["confidence"], ("confirmed", "reported"))

    def test_selftest_passes(self):
        self.assertTrue(xpeng_vin.selftest(verbose=False))

    def test_module_has_no_third_party_imports(self):
        """The module must stay copy-pasteable and stdlib-only."""
        source = pathlib.Path(xpeng_vin.__file__).read_text(encoding="utf-8")
        for forbidden in ("import homeassistant", "from homeassistant",
                          "import requests", "import voluptuous"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class TestRavRegister(unittest.TestCase):
    """Australia's Register of Approved Vehicles — the VIN -> variant bridge.

    Parsing is tested offline against a trimmed real response. Live tests are
    opt-in via ``XPENG_VIN_LIVE_TESTS=1`` so the suite stays hermetic.
    """

    FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "rav_L1NNSGHA0SB126544.html"
    REAL_AU_G6 = "L1NNSGHA0SB126544"

    def test_fixture_parses(self):
        html = self.FIXTURE.read_text(encoding="utf-8")
        pairs = xpeng_vin._rav_pairs(html)
        self.assertEqual(len(pairs), 17)
        self.assertEqual(pairs["Approval Number"], "VTA-064130")
        self.assertEqual(pairs["Vehicle Make"], "XPENG")
        self.assertEqual(pairs["Vehicle Model"], "G6")
        self.assertEqual(pairs["Power"], "210")
        self.assertEqual(pairs["Build Date"], "04/2025")
        self.assertEqual(pairs["GVM/ATM (kg)"], "2553")

    def test_parser_ignores_escaped_empty_state_template(self):
        """Every response ships a 'does not contain a record' template.

        It is HTML-escaped, so the unescaped label regex must not match it and
        a real record must still be found.
        """
        html = self.FIXTURE.read_text(encoding="utf-8")
        self.assertIn("does not contain a record", html)
        pairs = xpeng_vin._rav_pairs(html)
        self.assertIn("Approval Number", pairs)
        self.assertEqual(pairs["Approval Number"], "VTA-064130")

    def test_template_alone_yields_no_record(self):
        """A response with only the template present must not look like a hit."""
        html = self.FIXTURE.read_text(encoding="utf-8")
        start = html.index("<!-- escaped empty-state template")
        self.assertEqual(xpeng_vin._rav_pairs(html[start:]), {})

    def test_rav_rejects_malformed_vin(self):
        for bad in ("nonsense", "TOOSHORT", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    xpeng_vin.lookup_rav(bad, timeout=0.001)

    def test_au_variants_from_power(self):
        self.assertEqual(xpeng_vin.au_variants_from_power("G6", 190), ("Standard",))
        self.assertEqual(xpeng_vin.au_variants_from_power("G6", 210), ("Long range",))
        # Power alone cannot separate the X9's two 2WD variants.
        self.assertEqual(
            xpeng_vin.au_variants_from_power("X9", 255),
            ("C21A0A_2WD SR", "D21A0A_2WD LR"),
        )
        self.assertEqual(
            xpeng_vin.au_variants_from_power("X9", 395),
            ("E31A0A_AWD LR", "F31A0A_AWD LR"),
        )

    def test_au_variants_never_guesses(self):
        for model, power in (("G6", 999), ("G6", None), (None, 210), ("G9", 210)):
            with self.subTest(model=model, power=power):
                self.assertEqual(xpeng_vin.au_variants_from_power(model, power), ())

    def test_au_variant_table_matches_rover_engines(self):
        """190/210 kW come from the G6 RVD's own engine rows."""
        g6 = xpeng_vin.ROVER_APPROVALS[0]
        self.assertEqual(sorted(xpeng_vin.AU_VARIANTS_BY_POWER["G6"]),
                         sorted(g6["engine_kw"]))

    def test_real_au_g6_vin_is_valid_and_matches_rover_prefix(self):
        info = xpeng_vin.decode(self.REAL_AU_G6)
        self.assertTrue(info.valid)
        self.assertIs(info.check_digit_valid, True)
        self.assertEqual(info.vds, "NSGHA")
        self.assertEqual(info.serial, "126544")
        # Positions 1-8 identical to ROVER's G6 typical VIN.
        self.assertEqual(self.REAL_AU_G6[:8], xpeng_vin.ROVER_APPROVALS[0]["typical_vin"][:8])

    @unittest.skipUnless(
        os.environ.get("XPENG_VIN_LIVE_TESTS") == "1",
        "set XPENG_VIN_LIVE_TESTS=1 to run live RAV lookups",
    )
    def test_live_rav_lookup(self):
        record = xpeng_vin.lookup_rav(self.REAL_AU_G6)
        self.assertTrue(record["found"])
        self.assertEqual(record["model"], "G6")
        self.assertEqual(record["approval_number"], "VTA-064130")
        self.assertEqual(record["power_kw"], 210.0)
        self.assertEqual(record["variant_candidates"], ["Long range"])

    @unittest.skipUnless(
        os.environ.get("XPENG_VIN_LIVE_TESTS") == "1",
        "set XPENG_VIN_LIVE_TESTS=1 to run live RAV lookups",
    )
    def test_live_rav_lookup_no_record_for_non_au_vin(self):
        record = xpeng_vin.lookup_rav("L1NBSHTP8TB041733")
        self.assertFalse(record["found"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
