"""The journal keeps every line, chains them, and renders them for review."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness import journal
from agent_harness.journal import Journal, JournalBroken


@pytest.fixture
def j(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "runs" / "r1" / "journal.jsonl")


# ── Writing and chaining ─────────────────────────────────────────────────────

def test_lines_are_numbered_timestamped_and_chained(j: Journal) -> None:
    a = j.record("harness", "state.next_action", "run", directive="propose", iteration=1)
    b = j.note("orchestrator", "in scope: the email track is the idea's first track", subject="t01")
    assert (a["n"], b["n"]) == (1, 2)
    assert a["prev"] == "" and b["prev"] == a["hash"]
    assert len(a["hash"]) == 64 and a["at"].endswith("Z")
    assert j.verify() == 2
    assert j.by_subject("t01")[0]["data"]["text"].startswith("in scope")


def test_an_edited_line_breaks_the_chain(j: Journal) -> None:
    j.record("harness", "trial.refused", "t03", cause="budget")
    j.record("harness", "state.record_failure", "t03", kind="refused")
    lines = j.path.read_text().splitlines()
    e = json.loads(lines[0]); e["data"]["cause"] = "library"
    j.path.write_text(json.dumps(e, sort_keys=True) + "\n" + lines[1] + "\n")
    with pytest.raises(JournalBroken, match="line 1"):
        j.verify()


def test_a_removed_line_breaks_the_chain(j: Journal) -> None:
    for i in range(3):
        j.record("harness", "x", "s", i=i)
    lines = j.path.read_text().splitlines()
    j.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(JournalBroken, match="does not chain"):
        j.entries()


def test_a_note_needs_a_text_and_a_line_needs_an_actor(j: Journal) -> None:
    with pytest.raises(ValueError):
        j.note("orchestrator", "  ")
    with pytest.raises(ValueError):
        j.record("", "x")


def test_data_is_made_serialisable(j: Journal, tmp_path: Path) -> None:
    from agent_harness.research.theory import Verdict
    e = j.record("harness", "theory.measure", "t01", verdict=Verdict.REFUTED,
                 path=tmp_path / "x", ids=("a", "b"), nested={"k": frozenset({"z"})})
    assert e["data"] == {"verdict": "refuted", "path": str(tmp_path / "x"), "ids": ["a", "b"],
                         "nested": {"k": ["z"]}}


# ── The active journal ───────────────────────────────────────────────────────

def test_emit_is_silent_without_a_journal_and_writes_with_one(tmp_path: Path) -> None:
    assert journal.emit("x", "s") is None
    with journal.using(tmp_path / "j.jsonl") as j:
        assert journal.emit("guard.enforce", "t01", ok=False, violations=["state.json"])["n"] == 1
        assert journal.note("orchestrator", "abandoning", subject="t01")["n"] == 2
    assert journal.active() is None
    assert j.verify() == 2


def test_resolve_uses_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(journal.ENV_VAR, raising=False)
    assert journal.resolve(None) is None
    monkeypatch.setenv(journal.ENV_VAR, str(tmp_path / "env.jsonl"))
    assert journal.resolve(None).path == tmp_path / "env.jsonl"
    assert journal.resolve(tmp_path / "explicit.jsonl").path == tmp_path / "explicit.jsonl"


# ── Rendering ────────────────────────────────────────────────────────────────

def test_render_groups_by_subject_and_keeps_notes(j: Journal) -> None:
    j.record("harness", "idea.freeze", "run", digest="abc")
    j.record("harness", "trial.refused", "t03", cause="budget", detail="1 track has no trial")
    j.note("orchestrator", "declined; will serve the age track first", subject="t03")
    j.note("orchestrator", "run opened with 4 known theories")
    text = j.render()
    assert text.startswith("# Run journal")
    assert "4 lines, chain intact" in text
    assert "| 2 |" in text and "`trial.refused`" in text and "cause=budget" in text
    assert "## t03" in text and "**note** — declined; will serve the age track first" in text
    assert "## Notes on the run" in text and "run opened with 4 known theories" in text


def test_render_of_an_empty_journal(j: Journal) -> None:
    assert "Empty." in j.render()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args: str, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "agent_harness.journal", *args],
                          capture_output=True, text=True, env={**os.environ, **(env or {})})
    return proc.returncode, proc.stdout


def test_cli_note_verify_render_and_env(tmp_path: Path) -> None:
    path = str(tmp_path / "j.jsonl")
    code, out = _cli("note", "--actor", "orchestrator", "--text", "hello")
    assert code == 2 and json.loads(out)["error"] == "NoJournal"
    code, out = _cli("note", "--journal", path, "--actor", "orchestrator", "--text", "hello", "--subject", "t01")
    assert code == 0 and json.loads(out)["n"] == 1
    code, out = _cli("note", "--actor", "orchestrator", "--text", "via env", env={journal.ENV_VAR: path})
    assert code == 0 and json.loads(out)["n"] == 2
    code, out = _cli("verify", "--journal", path)
    assert code == 0 and json.loads(out)["lines"] == 2
    code, out = _cli("render", "--journal", path)
    assert code == 0 and "## t01" in out and "via env" in out
    code, out = _cli("tail", "--journal", path, "--n", "1")
    assert json.loads(out)["data"]["text"] == "via env"
