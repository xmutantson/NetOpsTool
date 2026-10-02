"""Check copyable examples and demo script guards without starting a service."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_readme_ingest_example_matches_schema():
    from netops.schemas import IngestSnapshot

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = [json.loads(text) for text in re.findall(r"-d '(\{.*?\})'", readme, re.S)]
    snapshots = [example for example in examples if "generated_at" in example]
    assert len(snapshots) == 1
    snapshot = IngestSnapshot.parse_obj(snapshots[0])
    assert len(snapshot.flows) == 2
    assert all(row.direction == "outbound" for row in snapshot.flows)


def bash_executable():
    candidates = [r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Bash unavailable; shell checks require Bash or Git Bash")


@pytest.mark.parametrize("script,args", [
    ("smoke_netops.sh", []),
    ("smoke_netops_hubbed.sh", ["-p", "synthetic-demo-password"]),
])
def test_demo_requires_explicit_target_before_external_commands(script, args):
    environment = os.environ.copy()
    environment.pop("HOST", None)
    result = subprocess.run(
        [bash_executable(), str(ROOT / "scripts" / script), *args],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "disposable demo server" in result.stdout + result.stderr


@pytest.mark.parametrize("script", ["entrypoint.sh", *[
    "scripts/" + name for name in (
        "smoke_netops.sh", "smoke_netops_hubbed.sh", "bulk_set_station_passwords.sh"
    )
]])
def test_shell_syntax(script):
    result = subprocess.run(
        [bash_executable(), "-n", str(ROOT / script)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
