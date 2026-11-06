"""Exercise the image metadata shell step used by the renewal workflow."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/renewal-image.yml"


class RenewalImageWorkflowTests(unittest.TestCase):
    def test_image_metadata_for_mixed_case_and_lowercase_owners(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = re.search(
            r"        id: app\n        run: \|\n((?:          .*\n)+)",
            workflow,
        )
        self.assertIsNotNone(step, "workflow must have an app metadata step")
        script = textwrap.dedent(step.group(1))
        version = re.search(
            r'^version:\s*"([^"]+)"',
            (ROOT / "xpeng_renew/config.yaml").read_text(encoding="utf-8"),
            re.M,
        ).group(1)
        for owner in ("DrazorV", "xpengtoolkit"):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "GITHUB_REPOSITORY_OWNER": owner,
                        "GITHUB_OUTPUT": str(output),
                    },
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    output.read_text(encoding="utf-8").splitlines(),
                    [
                        f"version={version}",
                        f"image=ghcr.io/{owner.lower()}/xpeng-renew",
                    ],
                )

    def test_both_tags_use_the_normalized_image(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "${{ steps.app.outputs.image }}:${{ steps.app.outputs.version }}",
            workflow,
        )
        self.assertIn("${{ steps.app.outputs.image }}:latest", workflow)
        self.assertNotIn("ghcr.io/${{ github.repository_owner }}", workflow)


if __name__ == "__main__":
    unittest.main()
