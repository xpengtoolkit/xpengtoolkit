"""The two app slots must stay one worker with two sets of defaults.

``xpeng_renew_2`` is a packaging variant (issue #11): the same worker image, a host
port and a ``pair_dir`` of its own, and nothing else. The failure mode worth a test
is silent drift - an option added to one slot and not the other, a version bumped in
one file so the Supervisor pulls an image tag that was never published, or a copy of
``renew_server.py`` appearing in the second slot and slowly becoming a fork.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402,F401  (keeps the test independent of the cwd)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRST_DIR = ROOT / "xpeng_renew"
SECOND_DIR = ROOT / "xpeng_renew_2"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.M)
    return match.group(1).strip().strip("\"'") if match else None


def _block_keys(text: str, key: str) -> dict[str, str]:
    """The two-space indented ``name: value`` children of a top-level ``key:``."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == f"{key}:")
    block: list[str] = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(("  ", "\t")):
            break
        block.append(line)
    found: dict[str, str] = {}
    for line in block:
        match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if match:
            found[match.group(1)] = match.group(2).strip().strip("\"'")
    return found


class SlotsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first = _text(FIRST_DIR / "config.yaml")
        self.second = _text(SECOND_DIR / "config.yaml")

    def test_slugs_and_names_are_distinct(self):
        self.assertEqual(_scalar(self.first, "slug"), "xpeng_renew")
        self.assertEqual(_scalar(self.second, "slug"), "xpeng_renew_2")
        self.assertNotEqual(_scalar(self.first, "name"), _scalar(self.second, "name"))

    def test_the_second_slot_pulls_the_published_worker(self):
        image = _scalar(self.second, "image")
        self.assertEqual(image, "ghcr.io/xpengtoolkit/xpeng-renew")
        # No tag: the Supervisor appends `version`, which is the tag the Renewal
        # image workflow publishes. A tag here would pin one release forever.
        self.assertNotIn(":", image.split("/")[-1])
        # The first slot still builds its own image from its Dockerfile.
        self.assertIsNone(_scalar(self.first, "image"))

    def test_versions_match_so_the_pulled_image_exists(self):
        first = _scalar(self.first, "version")
        second = _scalar(self.second, "version")
        self.assertIsNotNone(first)
        self.assertRegex(first, r"^\d+\.\d+\.\d+$")
        self.assertEqual(first, second)

    def test_port_and_pair_dir_defaults_differ(self):
        first_port = re.search(r'"(\d+)/tcp":\s*(\d+)', self.first)
        second_port = re.search(r'"(\d+)/tcp":\s*(\d+)', self.second)
        self.assertIsNotNone(first_port)
        self.assertIsNotNone(second_port)
        self.assertEqual(first_port.groups(), ("8090", "8090"))
        # Container port stays 8090 (the worker's PORT); only the host side moves.
        self.assertEqual(second_port.groups(), ("8090", "8091"))
        first_options = _block_keys(self.first, "options")
        second_options = _block_keys(self.second, "options")
        self.assertEqual(first_options["pair_dir"], "/config/xpeng-secrets")
        self.assertEqual(second_options["pair_dir"], "/config/xpeng-secrets-2")

    def test_pair_dirs_are_paths_core_can_read(self):
        """The pair must live under /config: Core reads it from its own container."""
        for text in (self.first, self.second):
            self.assertTrue(
                _block_keys(text, "options")["pair_dir"].startswith("/config/")
            )

    def test_both_slots_declare_the_same_options_and_schema(self):
        self.assertEqual(
            sorted(_block_keys(self.first, "options")),
            sorted(_block_keys(self.second, "options")),
        )
        self.assertEqual(
            _block_keys(self.first, "schema"),
            _block_keys(self.second, "schema"),
        )

    def test_both_slots_ask_for_the_same_supervisor_grants(self):
        for text in (self.first, self.second):
            self.assertIn("homeassistant_api: true", text)
            self.assertIn("- config:rw", text)
            self.assertIn("- amd64", text)
            self.assertIn("- aarch64", text)

    def test_the_second_slot_carries_no_copy_of_the_worker(self):
        """A copied worker or Dockerfile is how two slots drift apart."""
        for name in ("renew_server.py", "Dockerfile", "run.sh", "package.json", "vendor"):
            self.assertFalse((SECOND_DIR / name).exists(), name)


class RunLogTest(unittest.TestCase):
    """Each slot keeps its own last-run log (issue #11).

    Both slots map ``/config``, and the log used to be one fixed filename, so a
    renewal in the second slot overwrote the first slot's evidence. That log is
    the only record of why a login failed, so it has to follow ``pair_dir``.
    """

    @staticmethod
    def _server():
        import importlib.util

        path = FIRST_DIR / "renew_server.py"
        spec = importlib.util.spec_from_file_location(
            "xpeng_renew_server_under_test", path
        )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError as exc:  # pragma: no cover - add-on-only dependency
            raise unittest.SkipTest(f"renew_server.py could not be imported: {exc}")
        return module

    def test_the_log_path_follows_the_pair_dir(self):
        server = self._server()
        original = server.PAIR_DIR
        try:
            server.PAIR_DIR = "/config/xpeng-secrets"
            first = server._run_log_path()
            server.PAIR_DIR = "/config/xpeng-secrets-2"
            second = server._run_log_path()
        finally:
            server.PAIR_DIR = original
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("xpeng-secrets-last-run.log"), first)
        self.assertTrue(second.endswith("xpeng-secrets-2-last-run.log"), second)

    def test_it_is_not_the_old_shared_filename(self):
        server = self._server()
        original = server.PAIR_DIR
        try:
            server.PAIR_DIR = "/config/xpeng-secrets"
            path = server._run_log_path()
        finally:
            server.PAIR_DIR = original
        self.assertNotEqual(path, "/config/xpeng-renew-last-run.log")


if __name__ == "__main__":
    unittest.main(verbosity=2)
