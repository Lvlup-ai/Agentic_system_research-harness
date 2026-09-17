"""The orchestrator: the research loop, wired around three scripted agents.

Run it from the repository root::

    python -m examples.dataset_audit.run

This is what an orchestrating LLM would do step by step through the CLIs.
Here it is a script, so the whole run is reproducible and the test suite can
assert every line of the trace. The loop, per iteration:

    next_action ─► researcher writes note + measurement (under guard)
      ─► knowledge.check_note   (not already refuted, builds on its track)
      ─► library.require_citations
      ─► budget.check           (distance, tracks)
      ─► auditor challenges     (exchange recorded, GO / NO_GO)
      ─► theory.seal
      ─► budget.consume
      ─► measurement            (deterministic)
      ─► theory.record_measure  (verdict computed from the sealed zones)
      ─► researcher's postmortem, knowledge.record
      ─► state_machine.record_result

Then the boundary: the report under contract, the reviewer, the retry cap,
the ledger; and the idea rewritten on the verdicts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from agent_harness.budget import Budget
from agent_harness.jurisdiction import Guard, Matrix
from agent_harness.ledger import Ledger
from agent_harness.research.idea import Idea
from agent_harness.research.knowledge import Knowledge, KnowledgeError
from agent_harness.research.library import CitationRefused, Library
from agent_harness.research.theory import Theory, TheoryError, Verdict, parse_note
from agent_harness.review_loop import BoundaryState, Decision, Review
from agent_harness.state_machine import (
    Audit,
    Directive,
    FailureKind,
    LoopConfig,
    PhaseMachine,
    RunConfig,
    init_run,
    new_run_id,
    save_state,
)

from . import agents
from .data import COLUMNS, dataset_id, rows
from .metrics import run_measurement
from .report import TheoryItem, TheoryReport, registry

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.yaml"
IDEA = HERE / "idea.md"
LIBRARY = HERE / "library"
PHASE = "theories"
BOUNDARY = "theories→report"

MATRIX = Matrix(
    roles={
        "researcher": ("theories/{item}/note.json", "theories/{item}/measurement.json"),
        "auditor": ("theories/{item}/audit.md", "journal.md"),
        "reviewer": ("review/*",),
    },
    protected=(HERE / "briefs", HERE / "library", IDEA),
)


def _say(trace: list[str], line: str, quiet: bool) -> None:
    trace.append(line)
    if not quiet:
        print(line)


def run(runs_root: Path, run_id: str = "example", quiet: bool = False) -> dict:
    """One full run. Returns a summary the tests assert on."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    trace: list[str] = []
    subject = dataset_id()
    table = rows()

    # -- pre-declared everything ---------------------------------------------
    idea = Idea.load(IDEA)
    library = Library(LIBRARY)
    if not library.index_is_current():
        library.write_index()
    knowledge = Knowledge(runs_root / "knowledge", subject)
    ledger = Ledger(runs_root / "ledger.md", tuple(cfg["ledger_events"]))
    ledger.record(subject, "gate", f"{len(table)} rows, {sum(r['anomaly'] for r in table)} labelled anomalies; "
                                   f"{len(knowledge.entries)} theories already known")
    budget = Budget.load(runs_root / "budget", subject, "first_pass",
                         total=cfg["budget"]["total"], costs=cfg["budget"]["costs"],
                         tracks=idea.tracks)
    ctx = init_run(RunConfig(phases=tuple(cfg["phases"]), loop=LoopConfig(**cfg["loop"])),
                   runs_root, run_id)
    idea.freeze(ctx.root)
    machine = PhaseMachine(ctx.state.phase(PHASE), ctx.config.loop)
    ledger.record(subject, "run", f"{run_id}: idea `{idea.title}`, budget {budget.total} "
                                  f"({budget.spent_before()} already spent), max {ctx.config.loop.max_iterations} theories")
    _say(trace, f"idea: {idea.title} — tracks {list(idea.tracks)}; library {library.counts()}; "
                f"known theories {len(knowledge.entries)}; budget {budget.remaining()}/{budget.total}", quiet)

    violations: list[str] = []
    refused: dict[str, str] = {}
    measured: list[str] = []

    def fail(kind: FailureKind) -> None:
        machine.record_failure(kind)
        save_state(ctx)

    # -- the phase ------------------------------------------------------------
    while True:
        directive = machine.next_action()
        save_state(ctx)
        if directive is Directive.PHASE_DONE:
            _say(trace, f"phase done: {machine.state.verdict.value}; {len(measured)} theories measured", quiet)
            break
        n = machine.state.iteration
        item = f"{run_id}-theory_{n:02d}"
        theory = Theory(ctx.root / "theories" / item)

        # researcher, under guard, with what is already known
        with Guard(ctx.root, MATRIX, "researcher", {"item": item}) as g:
            meta = agents.research(n, ctx.root, theory.dir, knowledge)
        if not g.result.ok:
            violations.extend(g.result.violations)
            _say(trace, f"[{n}] {item}: wrote outside its jurisdiction {g.result.violations}; "
                        "restored, failure recorded", quiet)
            fail(FailureKind.JURISDICTION)
            continue

        # the note itself: well-formed and falsifiable
        try:
            note = parse_note(json.loads(theory.note_path.read_text()))
        except (TheoryError, ValueError) as exc:
            _say(trace, f"[{n}] {item}: note refused — {exc}", quiet)
            fail(FailureKind.INVALID_OUTPUT)
            continue

        # what is already known, the library, the budget: all before any spending
        try:
            knowledge.check_note(note)
            library.require_citations(note.cites)
            ok, why = budget.check(meta["distance"])
            if not ok:
                raise ValueError(why)
        except (KnowledgeError, CitationRefused, ValueError) as exc:
            refused[item] = type(exc).__name__
            _say(trace, f"[{n}] {item} ({note.track}, {meta['distance']}): refused before any spending — "
                        f"{type(exc).__name__}: {exc}", quiet)
            fail(FailureKind.REFUSED)
            continue

        # auditor, under guard; the exchange is recorded on the theory
        with Guard(ctx.root, MATRIX, "auditor", {"item": item}) as g:
            verdict, position = agents.audit(theory.dir, library)
        if not g.result.ok:
            fail(FailureKind.JURISDICTION)
            continue
        theory.record_exchange("auditor", position)
        directive = machine.record_audit(Audit(verdict))
        save_state(ctx)
        if directive is not Directive.EXECUTE:
            _say(trace, f"[{n}] {item}: audit NO_GO — {position}", quiet)
            continue

        # seal, spend, measure, judge
        digest = theory.seal()
        trial = budget.consume(item, meta["distance"], track=note.track)
        values = run_measurement(json.loads((theory.dir / "measurement.json").read_text()), table, COLUMNS)
        v, decisions = theory.record_measure(values)
        measured.append(item)

        # the researcher's postmortem; then the verdict becomes knowledge — unless
        # the note was reworded meanwhile: a reworded note is not knowledge, the
        # boundary catches it and it is recorded once restored
        refuted_exactly, lessons = agents.postmortem(theory)
        if theory.integrity_finding() is None:
            knowledge.record(theory, run_id, refuted_exactly, lessons)
        accepted = v is not Verdict.UNDECIDED
        score = sum(1 for d in decisions if d.decision is not Verdict.UNDECIDED) / len(decisions)
        machine.record_result(accepted, score, item)
        save_state(ctx)
        _say(trace, f"[{n}] {item} ({note.track}, {meta['distance']}, cost {trial.cost}, seal {digest[:8]}): "
                    + ", ".join(f"{d.metric}={d.value}→{d.decision.value}" for d in decisions)
                    + f" ⇒ {v.value}", quiet)

    budget.commit(runs_root / "budget")
    _say(trace, "budget: " + budget.summary(), quiet)

    # -- the deliverable, under contract -----------------------------------------
    reg = registry()
    report = TheoryReport.build([
        TheoryItem(id=i, track=Theory(ctx.root / "theories" / i).sealed_note().track,
                   verdict=Theory(ctx.root / "theories" / i).verdict(),
                   digest=Theory(ctx.root / "theories" / i).seal_digest() or "",
                   cites=Theory(ctx.root / "theories" / i).sealed_note().cites)
        for i in measured])
    report_path = reg.dump(report, ctx.root / "report.json")
    ledger.record(subject, "report", f"{report.summary.count} theories: {report.summary.confirmed} confirmed, "
                                     f"{report.summary.refuted} refuted, {report.summary.undecided} undecided")

    # -- the boundary: reviewer, retry cap, ledger -------------------------------
    review = Review(BoundaryState(boundary=BOUNDARY), cfg["review"]["max_retries"],
                    budget=budget, ledger=ledger, subject=subject)
    decisions_taken: list[str] = []
    while True:
        loaded = reg.load(report_path)
        with Guard(ctx.root, MATRIX, "reviewer"):
            decision, findings, redo, reason = agents.review(loaded, ctx.root)
        outcome = review.decide(decision, findings, redo, reason)
        decisions_taken.append(outcome.decision.value)
        _say(trace, f"review {BOUNDARY}: {outcome.decision.value}"
                    + (f" (retry {outcome.retry_number}/{outcome.max_retries})" if outcome.retry_number else "")
                    + f" — {outcome.reason}; findings: "
                    + (", ".join(f"{f.direction.value} {f.claim}" for f in findings) or "none"), quiet)
        if outcome.decision is not Decision.RETRY:
            break
        for f in findings:
            item = f.claim.split(":")[0]
            theory = Theory(ctx.root / "theories" / item)
            with Guard(ctx.root, MATRIX, "researcher", {"item": item}):
                agents.restore(theory)
            if item not in knowledge.ids():
                refuted_exactly, lessons = agents.postmortem(theory, misbehave=False)
                knowledge.record(theory, run_id, refuted_exactly, lessons)

    (ctx.root / "review").mkdir(exist_ok=True)
    (ctx.root / "review" / "boundary.json").write_text(review.state.model_dump_json(indent=2))

    # -- the idea, rewritten on the verdicts ---------------------------------------
    idea.verify(ctx.root)
    rewrite_path = idea.write_rewrite(knowledge, run_id, ctx.root / "idea_rewritten.md")
    _say(trace, f"idea rewritten on {len(knowledge.entries)} theories → {rewrite_path.name}; "
                f"ledger {ledger.total_lines()} lines", quiet)

    return {
        "run_root": str(ctx.root),
        "verdict": machine.state.verdict.value,
        "iterations": machine.state.iteration,
        "measured": measured,
        "verdicts": {i: Theory(ctx.root / "theories" / i).verdict().value for i in measured},
        "refused": refused,
        "violations": violations,
        "budget_spent": budget.spent_this_run(),
        "budget_prior": budget.spent_before(),
        "review_decisions": decisions_taken,
        "retries": review.state.retries,
        "knowledge_entries": len(knowledge.entries),
        "lessons": list(knowledge.lessons()),
        "ledger_lines": ledger.total_lines(),
        "ledger_counts": ledger.counts(subject),
        "trace": trace,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs-root", type=Path, default=HERE / "runs")
    parser.add_argument("--run-id", default=None, help="default: a UTC timestamp")
    args = parser.parse_args(argv)
    summary = run(args.runs_root, args.run_id or new_run_id())
    print(json.dumps({k: v for k, v in summary.items() if k != "trace"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
