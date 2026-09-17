"""The idea is the human starting point: frozen during the run, rewritten on the verdicts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.research.idea import Idea, IdeaError, IdeaMoved
from agent_harness.research.knowledge import Knowledge
from agent_harness.research.theory import Theory

IDEA = """---
title: Malformed rows come from a form that lost its validation
tracks: [email, signup_date]
orders_of_magnitude:
  malformed_share: "a few percent of rows"
---
The signup form is the only entry point for these rows. A deployment that
dropped the client-side validation would let malformed emails and impossible
dates through, and they would cluster after that date rather than spread
evenly. The mechanism is a regression, not user behaviour.
"""

NOTE = {
    "track": "email",
    "mechanism": "The signup form lost its address validation in 2023, so malformed emails cluster after that date.",
    "prediction": "At least 80 % of malformed emails were created in 2023 or later.",
    "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}],
    "refutation": "Malformed emails spread evenly across years: the form was never the cause.",
    "measures": "Share of malformed emails with signup_date >= 2023-01-01.",
    "cites": ["form_validation_regression"],
}


@pytest.fixture
def idea_path(tmp_path: Path) -> Path:
    p = tmp_path / "idea.md"
    p.write_text(IDEA)
    return p


def measured(tmp_path: Path, name: str, value: float, note: dict = NOTE) -> Theory:
    t = Theory(tmp_path / "theories" / name)
    t.draft(note)
    t.seal()
    t.record_measure({"share_after_2023": value})
    return t


# ── Loading ──────────────────────────────────────────────────────────────────

def test_an_idea_has_a_title_tracks_and_a_body(idea_path: Path) -> None:
    idea = Idea.load(idea_path)
    assert idea.title.startswith("Malformed rows")
    assert idea.tracks == ("email", "signup_date")
    assert idea.orders_of_magnitude == {"malformed_share": "a few percent of rows"}
    assert idea.body.startswith("The signup form")


def test_an_idea_without_tracks_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "idea.md"
    p.write_text("---\ntitle: X\n---\n" + "A mechanism long enough to pass the body check, and why.\n")
    with pytest.raises(IdeaError, match="`tracks` must be a non-empty list"):
        Idea.load(p)


def test_an_idea_without_a_mechanism_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "idea.md"
    p.write_text("---\ntitle: X\ntracks: [a]\n---\nToo short.\n")
    with pytest.raises(IdeaError, match="supposed mechanism"):
        Idea.load(p)


def test_duplicate_tracks_are_refused(tmp_path: Path) -> None:
    p = tmp_path / "idea.md"
    p.write_text("---\ntracks: [a, a]\n---\n" + "A mechanism long enough to pass the body check, and why.\n")
    with pytest.raises(IdeaError, match="duplicate"):
        Idea.load(p)


def test_a_missing_idea_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IdeaError, match="not found"):
        Idea.load(tmp_path / "nope.md")


# ── Frozen during the run ────────────────────────────────────────────────────

def test_freeze_then_verify(idea_path: Path, tmp_path: Path) -> None:
    idea = Idea.load(idea_path)
    run_root = tmp_path / "runs" / "r1"
    marker = idea.freeze(run_root)
    assert json.loads(marker.read_text())["tracks"] == ["email", "signup_date"]
    assert idea.verify(run_root) == idea.digest()
    assert idea.freeze(run_root) == marker, "freezing again with the same content is fine"


def test_an_idea_that_moves_during_the_run_is_detected(idea_path: Path, tmp_path: Path) -> None:
    idea = Idea.load(idea_path)
    run_root = tmp_path / "runs" / "r1"
    idea.freeze(run_root)
    idea_path.write_text(IDEA.replace("tracks: [email, signup_date]", "tracks: [email, signup_date, age]"))
    moved = Idea.load(idea_path)
    with pytest.raises(IdeaMoved, match="changed after the run started"):
        moved.verify(run_root)
    with pytest.raises(IdeaMoved):
        moved.freeze(run_root)


def test_verifying_an_unfrozen_run_is_refused(idea_path: Path, tmp_path: Path) -> None:
    with pytest.raises(IdeaError, match="never frozen"):
        Idea.load(idea_path).verify(tmp_path / "runs" / "r1")


# ── Rewritten on the verdicts ────────────────────────────────────────────────

def test_the_rewrite_carries_a_source_for_every_claim(idea_path: Path, tmp_path: Path) -> None:
    k = Knowledge(tmp_path / "knowledge", "dataset_a")
    k.record(measured(tmp_path, "theory_01", 0.3), "run_1",
             refuted_exactly="the 2023 cut-off; malformed emails are spread across years",
             lessons=["Dates alone do not explain malformed emails."])
    k.record(measured(tmp_path, "theory_02", 0.9, {**NOTE, "track": "age",
                                                  "prediction": "At least 80 % of impossible ages are zero."}), "run_1")
    text = Idea.load(idea_path).rewrite(k, "run_1")
    assert text.startswith("# Malformed rows come from a form that lost its validation — rewritten on 2 measured theories")
    assert "All numbers are in-sample" in text
    assert "## Track `email`" in text and "**refuted** — theory_01 (run_1)" in text
    assert "`share_after_2023` = 0.3 → refuted" in text
    assert "refuted exactly: the 2023 cut-off" in text
    assert "rests on: `form_validation_regression`" in text
    assert "## Track `signup_date`\n\nNothing established" in text
    assert "## Off the idea's tracks" in text and "`age`" in text
    assert "- Dates alone do not explain malformed emails." in text
    assert "0 confirmed, 1 refuted, 1 undecided" not in text and "1 confirmed, 1 refuted, 0 undecided." in text
    assert text.rstrip().endswith("The mechanism is a regression, not user behaviour.")


def test_the_rewrite_of_an_empty_store_says_so(idea_path: Path, tmp_path: Path) -> None:
    k = Knowledge(tmp_path / "knowledge", "dataset_a")
    text = Idea.load(idea_path).rewrite(k, "run_0")
    assert "rewritten on 0 measured theories" in text
    assert text.count("Nothing established") == 2
    assert "- Nothing yet." in text


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "agent_harness.research.idea", *args],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def test_cli_load_freeze_verify_rewrite(idea_path: Path, tmp_path: Path) -> None:
    run_root = str(tmp_path / "runs" / "r1")
    code, out = _cli("load", "--idea", str(idea_path))
    assert code == 0 and out["tracks"] == ["email", "signup_date"]
    code, out = _cli("verify", "--idea", str(idea_path), "--run-root", run_root)
    assert code == 2 and out["error"] == "IdeaError"
    code, out = _cli("freeze", "--idea", str(idea_path), "--run-root", run_root)
    assert code == 0 and len(out["digest"]) == 64
    code, out = _cli("rewrite", "--idea", str(idea_path), "--store", str(tmp_path / "knowledge"),
                     "--subject", "dataset_a", "--run-id", "r1", "--out", str(tmp_path / "idea_v2.md"))
    assert code == 0 and (tmp_path / "idea_v2.md").exists()
    idea_path.write_text(IDEA + "\nAn afterthought added mid-run.\n")
    code, out = _cli("verify", "--idea", str(idea_path), "--run-root", run_root)
    assert code == 2 and out["error"] == "IdeaMoved"
