"""Knowledge is append-only, refuses to re-pay a refutation, and makes the next note build on it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.research.knowledge import (
    AlreadyRefuted,
    IgnoresHistory,
    Knowledge,
    NotRecordable,
)
from agent_harness.research.theory import Theory, Verdict

NOTE = {
    "track": "email",
    "mechanism": "The signup form lost its address validation in 2023, so malformed emails cluster after that date.",
    "prediction": "At least 80 % of malformed emails were created in 2023 or later.",
    "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}],
    "refutation": "Malformed emails spread evenly across years: the form was never the cause.",
    "measures": "Share of malformed emails with signup_date >= 2023-01-01.",
    "cites": ["form_validation_regression"],
}


def measured(tmp_path: Path, name: str, value: float, note: dict = NOTE) -> Theory:
    t = Theory(tmp_path / "theories" / name)
    t.draft(note)
    t.seal()
    t.record_measure({"share_after_2023": value})
    return t


@pytest.fixture
def k(tmp_path: Path) -> Knowledge:
    return Knowledge(tmp_path / "knowledge", "dataset_a")


# ── Recording ────────────────────────────────────────────────────────────────

def test_a_measured_theory_is_recorded_and_reloaded(tmp_path: Path, k: Knowledge) -> None:
    t = measured(tmp_path, "theory_01", 0.9)
    e = k.record(t, "run_1", lessons=["A share below 0.4 would have refuted the form hypothesis."])
    assert e.verdict is Verdict.CONFIRMED and e.digest == t.seal_digest()
    again = Knowledge(k.store, "dataset_a")
    assert [x.theory_id for x in again.entries] == ["theory_01"]
    assert again.lessons() == ("A share below 0.4 would have refuted the form hypothesis.",)


def test_an_unmeasured_theory_is_not_knowledge(tmp_path: Path, k: Knowledge) -> None:
    t = Theory(tmp_path / "theories" / "t")
    t.draft(NOTE)
    t.seal()
    with pytest.raises(NotRecordable, match="no verdict yet"):
        k.record(t, "run_1")
    assert not k.path.exists()


def test_a_reworded_theory_is_not_knowledge(tmp_path: Path, k: Knowledge) -> None:
    t = measured(tmp_path, "theory_01", 0.9)
    raw = json.loads(t.note_path.read_text())
    raw["prediction"] = "Whatever we saw."
    t.note_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="reworded after the fact"):
        k.record(t, "run_1")


def test_a_refutation_must_say_what_was_refuted(tmp_path: Path, k: Knowledge) -> None:
    t = measured(tmp_path, "theory_01", 0.3)
    with pytest.raises(NotRecordable, match="what exactly was refuted"):
        k.record(t, "run_1")
    e = k.record(t, "run_1", refuted_exactly="the 2023 cut-off; malformed emails are spread across years")
    assert e.verdict is Verdict.REFUTED and "2023 cut-off" in e.refuted_exactly


def test_the_store_is_append_only(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.9), "run_1")
    before = k.path.read_text()
    k.record(measured(tmp_path, "theory_02", 0.6, {**NOTE, "builds_on": ["theory_01"]}), "run_1")
    after = k.path.read_text()
    assert after.startswith(before) and after.count("\n") == 2


# ── Checks before a new trial ────────────────────────────────────────────────

def test_the_same_theory_is_not_refuted_twice(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.3), "run_1", refuted_exactly="the 2023 cut-off")
    with pytest.raises(AlreadyRefuted, match="refuted as theory_01 \\(run_1\\): the 2023 cut-off"):
        k.check_note({**NOTE, "builds_on": ["theory_01"]})


def test_a_confirmed_theory_may_be_measured_again(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.9), "run_1")
    k.check_note({**NOTE, "builds_on": ["theory_01"]})  # a replication is allowed


def test_a_note_on_a_track_with_history_must_build_on_it(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.3), "run_1", refuted_exactly="the 2023 cut-off")
    fresh = {**NOTE, "prediction": "At least 80 % were created in 2024 or later."}
    with pytest.raises(IgnoresHistory, match="track 'email' already has 1 verdict\\(s\\) \\(theory_01\\)"):
        k.check_note(fresh)
    k.check_note({**fresh, "builds_on": ["theory_01"]})


def test_builds_on_must_name_real_theories(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.9), "run_1")
    with pytest.raises(IgnoresHistory, match="never measured: \\['theory_99'\\]"):
        k.check_note({**NOTE, "builds_on": ["theory_01", "theory_99"]})


def test_a_new_track_has_no_history_to_build_on(k: Knowledge) -> None:
    k.check_note({**NOTE, "track": "age"})


def test_a_fresh_subject_accepts_anything_falsifiable(k: Knowledge) -> None:
    k.check_note(NOTE)


# ── Reading ──────────────────────────────────────────────────────────────────

def test_tracks_and_briefing(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "theory_01", 0.3), "run_1",
             refuted_exactly="the 2023 cut-off", lessons=["Dates alone do not explain malformed emails."])
    k.record(measured(tmp_path, "theory_02", 0.6, {**NOTE, "builds_on": ["theory_01"]}), "run_1")
    assert k.tracks() == {"email": {"confirmed": 0, "refuted": 1, "undecided": 1}}
    text = k.briefing("email")
    assert "theory_01 (run_1, email): refuted." in text
    assert "Refuted exactly: the 2023 cut-off" in text
    assert "theory_02 (run_1, email): undecided." in text
    assert text.endswith("- Dates alone do not explain malformed emails.")
    assert k.briefing("age").startswith("No theory measured yet")


def test_lessons_are_deduplicated_in_order(tmp_path: Path, k: Knowledge) -> None:
    k.record(measured(tmp_path, "t1", 0.9), "r", lessons=["a", "b"])
    k.record(measured(tmp_path, "t2", 0.9, {**NOTE, "builds_on": ["t1"]}), "r", lessons=["b", "c"])
    assert k.lessons() == ("a", "b", "c")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "agent_harness.research.knowledge", *args],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def test_cli_record_check_and_briefing(tmp_path: Path) -> None:
    t = measured(tmp_path, "theory_01", 0.3)
    common = ("--store", str(tmp_path / "knowledge"), "--subject", "dataset_a")
    code, out = _cli("record", *common, "--theory-dir", str(t.dir), "--run-id", "run_1")
    assert code == 2 and out["error"] == "NotRecordable"
    code, out = _cli("record", *common, "--theory-dir", str(t.dir), "--run-id", "run_1",
                     "--refuted-exactly", "the 2023 cut-off", "--lesson", "Dates alone do not explain it.")
    assert code == 0 and out["verdict"] == "refuted" and out["entries"] == 1

    note = tmp_path / "note.json"
    note.write_text(json.dumps({**NOTE, "builds_on": ["theory_01"]}))
    code, out = _cli("check", *common, "--note", str(note))
    assert code == 2 and out["error"] == "AlreadyRefuted"
    note.write_text(json.dumps({**NOTE, "prediction": "At least 80 % were created in 2024 or later."}))
    code, out = _cli("check", *common, "--note", str(note))
    assert code == 2 and out["error"] == "IgnoresHistory"

    code, out = _cli("lessons", *common)
    assert out["lessons"] == ["Dates alone do not explain it."]
    code, out = _cli("briefing", *common, "--track", "email")
    assert "refuted" in out["briefing"]
