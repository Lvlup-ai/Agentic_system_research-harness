"""The shell sequence printed in the README works as written.

The block under "Driving it from a shell" is extracted from README.md and
replayed command by command in a temporary lab. A README that drifts from
the CLIs fails here, not in a reader's terminal.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "dataset_audit"

NOTE = {
    "track": "email",
    "mechanism": "The signup form lost its address validation at one deployment, so malformed emails cluster after it.",
    "prediction": "At least 80 % of malformed emails are from 2023 or later.",
    "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}],
    "refutation": "Fewer than 40 % of malformed emails are from 2023 or later.",
    "measures": "Rule email contains @; share_after 2023-01-01.",
    "cites": ["form_validation_regression"],
}


def readme_shell_commands() -> list[str]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## Driving it from a shell", 1)[1]
    block = re.search(r"```bash\n(.*?)```", section, re.S)
    assert block, "the README has no bash block under 'Driving it from a shell'"
    return [l.strip() for l in block.group(1).splitlines() if l.strip() and not l.strip().startswith("#")]


def test_the_readme_shell_sequence_runs_as_written(tmp_path: Path) -> None:
    shutil.copy(EXAMPLE / "idea.md", tmp_path / "idea.md")
    shutil.copytree(EXAMPLE / "library", tmp_path / "library")
    (tmp_path / ".secret").write_text("ab" * 32)
    theory = tmp_path / "runs" / "r1" / "theories" / "t01"
    theory.mkdir(parents=True)
    (theory / "note.json").write_text(json.dumps(NOTE))
    (tmp_path / "values.json").write_text(json.dumps({"share_after_2023": 0.95}))

    commands = readme_shell_commands()
    assert len(commands) >= 10
    env = dict(os.environ)
    for cmd in commands:
        if cmd.startswith("export "):
            key, _, value = cmd[len("export "):].partition("=")
            env[key] = value
            continue
        assert cmd.startswith("python -m agent_harness."), cmd
        argv = [sys.executable, *shlex.split(cmd)[1:]]     # quoted arguments stay whole
        proc = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, f"{cmd}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        out = json.loads(proc.stdout)
        assert out.get("ok", True) is True, cmd

    assert (theory / "seal.json").exists() and (theory / "verdict.json").exists()
    assert json.loads((theory / "verdict.json").read_text())["verdict"] == "confirmed"
    steps = [json.loads(l)["step"] for l in (theory / "passport.json").read_text().splitlines()]
    assert steps == ["cleared", "sealed", "consumed", "measured"]
    assert (tmp_path / "knowledge" / "s.jsonl").exists()
    assert json.loads((tmp_path / "budget" / "s.json").read_text())["spent"] == {"_": 1}
    # every command left its line in the journal named by the environment
    from agent_harness.journal import Journal
    entries = Journal(tmp_path / "runs" / "r1" / "journal.jsonl").entries()
    actions = [e["action"] for e in entries]
    for expected in ("idea.frozen", "trial.cleared", "theory.sealed", "trial.consumed", "theory.measured",
                     "knowledge.recorded", "budget.commit", "note"):
        assert expected in actions, expected
