"""The end-to-end example runs, and every mechanism of the research loop fires as documented."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_harness.prompt_tests import run_checks
from agent_harness.research.library import Library
from examples.dataset_audit import run as example
from examples.dataset_audit.data import N_ANOMALIES, rows

ROOT = Path(__file__).resolve().parent.parent


def test_the_table_has_nine_labelled_anomalies() -> None:
    assert N_ANOMALIES == 9
    assert len(rows()) == 40
    assert len({r["id"] for r in rows()}) == 40


def test_the_library_index_is_current() -> None:
    lib = Library(ROOT / "examples" / "dataset_audit" / "library")
    assert lib.counts() == {"usable": 2, "needs_extension": 1, "out_of_data": 1}
    assert lib.index_is_current(), "run: python -m agent_harness.research.library index --dir examples/dataset_audit/library"


def test_the_run_matches_its_documentation(tmp_path: Path) -> None:
    s = example.run(tmp_path, run_id="t", quiet=True)

    assert s["verdict"] == "max_iterations" and s["iterations"] == 8
    assert s["measured"] == ["t-theory_01", "t-theory_02", "t-theory_04", "t-theory_07"]
    assert s["verdicts"] == {"t-theory_01": "refuted", "t-theory_02": "confirmed",
                             "t-theory_04": "undecided", "t-theory_07": "confirmed"}

    # refused before any spending, each for its own reason
    assert s["refused"] == {"t-theory_03": "budget",       # the age track has no theory yet
                            "t-theory_05": "library",      # a card the library does not have
                            "t-theory_06": "knowledge"}    # the same formulation as theory 1
    # every measured theory carries its four stamps, signed with the lab's secret
    for i in s["measured"]:
        steps = [json.loads(l)["step"] for l in
                 (tmp_path / "t" / "theories" / i / "passport.json").read_text().splitlines()]
        assert steps == ["cleared", "sealed", "consumed", "measured"]
    assert (tmp_path / ".harness_secret").exists() and not (tmp_path / "t" / ".harness_secret").exists()
    # iteration 8: the write outside the jurisdiction was restored
    assert s["violations"] == ["state.json"]
    state = json.loads((tmp_path / "t" / "state.json").read_text())
    assert state["phases"]["theories"]["verdict"] == "max_iterations", "state.json is the machine's"

    # budget: 1 + 1 + 1 + 2, four theories measured
    assert s["budget_spent"] == 5 and s["budget_prior"] == 0

    # boundary: theory 7 was reworded after its seal; caught, restored, then PASS
    assert s["review_decisions"] == ["RETRY", "PASS"] and s["retries"] == 1
    assert s["ledger_counts"] == {"gate": 1, "run": 1, "report": 1, "retry": 1, "escalate": 0}
    t7 = tmp_path / "t" / "theories" / "t-theory_07"
    assert json.loads((t7 / "note.json").read_text())["prediction"].startswith("The rule age in [1, 120]")

    # knowledge: the four measured theories, the refutation saying what exactly was refuted
    assert s["knowledge_entries"] == 4
    entries = [json.loads(l) for l in (tmp_path / "knowledge" / f"{example.dataset_id()}.jsonl").read_text().splitlines()]
    assert entries[0]["verdict"] == "refuted" and "2023 cut-off" in entries[0]["refuted_exactly"]
    assert any("predate 2023" in l for l in s["lessons"])

    # the journal: every move of the orchestrator, written by the harness, chain intact
    from agent_harness.journal import Journal
    j = Journal(tmp_path / "t" / "journal.jsonl")
    entries = j.entries()
    assert len(entries) == s["journal_lines"] and len(entries) > 60
    actions = [e["action"] for e in entries]
    assert actions[0] == "idea.frozen" and "idea.rewritten" in actions
    refusals = [(e["subject"], e["data"]["cause"]) for e in entries if e["action"] == "trial.refused"]
    assert refusals == [("t-theory_03", "budget"), ("t-theory_05", "library"), ("t-theory_06", "knowledge")]
    kinds = [e["data"]["kind"] for e in entries if e["action"] == "state.record_failure"]
    assert kinds == ["refused", "refused", "refused", "jurisdiction"]
    guard = [e for e in entries if e["action"] == "guard.enforce" and not e["data"]["ok"]]
    assert len(guard) == 1 and guard[0]["data"]["violations"] == ["state.json"] and guard[0]["subject"] == "t-theory_08"
    assert [e["data"]["decision"] for e in entries if e["action"] == "review.decided"] == ["RETRY", "PASS"]
    assert any(e["action"] == "theory.integrity" and e["subject"] == "t-theory_07" for e in entries)
    assert any(e["action"] == "note" and e["actor"] == "orchestrator" and "distance declared" in e["data"]["text"]
               for e in entries)
    assert state["phases"]["theories"]["failures"] == [
        {"iteration": 3, "kind": "refused"}, {"iteration": 5, "kind": "refused"},
        {"iteration": 6, "kind": "refused"}, {"iteration": 8, "kind": "jurisdiction"}]
    rendered = (tmp_path / "t" / "journal.md").read_text()
    assert rendered.startswith("# Run journal") and "## t-theory_07" in rendered and "chain intact" in rendered

    # the idea, frozen during the run and rewritten on the verdicts
    assert (tmp_path / "t" / "idea.frozen.json").exists()
    rewritten = (tmp_path / "t" / "idea_rewritten.md").read_text()
    assert "rewritten on 4 measured theories" in rewritten
    assert "**refuted** — t-theory_01" in rewritten and "**confirmed** — t-theory_07" in rewritten
    assert "## Track `signup_date`" in rewritten and "**confirmed** — t-theory_02" in rewritten


def test_the_run_is_deterministic(tmp_path: Path) -> None:
    a = example.run(tmp_path / "a", run_id="t", quiet=True)
    b = example.run(tmp_path / "b", run_id="t", quiet=True)
    assert a["trace"] == b["trace"]


def test_a_second_run_learns_from_the_first(tmp_path: Path) -> None:
    example.run(tmp_path, run_id="first", quiet=True)
    s = example.run(tmp_path, run_id="second", quiet=True)
    # the budget is shared across runs on the same table
    assert s["budget_prior"] == 5 and s["budget_spent"] == 4
    # theory 1 is refused as already refuted, before any spending
    assert s["refused"]["second-theory_01"] == "knowledge"
    # the confirmed and undecided theories may be measured again; they build on their history
    assert s["measured"] == ["second-theory_02", "second-theory_04", "second-theory_07"]
    entries = [json.loads(l) for l in (tmp_path / "knowledge" / f"{example.dataset_id()}.jsonl").read_text().splitlines()]
    second_04 = next(e for e in entries if e["theory_id"] == "second-theory_04")
    assert second_04["builds_on"] == ["first-theory_04", "first-theory_07"]
    assert s["knowledge_entries"] == 7
    assert any("already known" in l and "4 theories" in l for l in
               (tmp_path / "ledger.md").read_text().splitlines())


def test_the_briefs_tell_the_truth() -> None:
    spec = yaml.safe_load((ROOT / "examples" / "dataset_audit" / "prompt_checks.yaml").read_text())
    report = run_checks(spec, ROOT)
    assert report.failures == []
    assert report.checked >= 20
