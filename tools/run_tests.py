#!/usr/bin/env python3
"""Run standalone regression scripts in isolated processes."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    failures = []
    files = sorted((ROOT / "tests").glob("test_*.py"))
    for path in files:
        try:
            result = subprocess.run([sys.executable, str(path)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            failures.append(path.name)
            print(f"FAIL {path.name}: timed out after 120 seconds", flush=True)
            continue
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {path.name}", flush=True)
        if result.returncode:
            failures.append(path.name)
            print(result.stdout + result.stderr, flush=True)
    print(f"{len(files) - len(failures)}/{len(files)} test scripts passed")
    return bool(failures)

if __name__ == "__main__":
    sys.exit(main())
