"""The three agents, scripted. Each function is where a model call would go.

They are deterministic on purpose: the example must run in CI without a key,
and the harness is exactly the part that does not need a model. A real
deployment replaces the body of ``research``, ``audit``, ``postmortem`` and
``review`` with a call to a model that reads the matching brief in
``briefs/`` and returns the same files or the same structure.

The researcher's script is written so that every mechanism of the research
loop fires once: a theory refuted by its own zones, one confirmed, one left
undecided by its grey zone, a drift refused by the budget, a citation of a
card the library does not have, the same formulation offered twice, a note
reworded after its seal, a write outside the researcher's jurisdiction.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_harness.research.knowledge import Knowledge
from agent_harness.research.library import Library
from agent_harness.research.theory import Theory, TheoryNote, Verdict
from agent_harness.review_loop import Decision, Direction, Finding

from .data import COLUMNS
from .metrics import MeasurementError, validate_measurement
from .report import TheoryReport

# ── Researcher ───────────────────────────────────────────────────────────────
#
# One theory per iteration. Replace with a model call that reads
# briefs/researcher.md, the knowledge briefing and the library index, and
# writes note.json + measurement.json into the theory directory.

_ZONE_PRECISION = {"metric": "precision", "confirm": {"min": 0.8}, "refute": {"max": 0.5}}
_ZONE_SHARE = {"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}

_SCRIPT: tuple[dict, ...] = (
    # 1 — refuted by its own zones: malformed emails all predate 2023
    {"track": "email", "distance": "in_scope",
     "note": {
         "mechanism": "The signup form lost its address validation at a 2023 deployment, so "
                      "malformed emails were created after that date, not before.",
         "prediction": "At least 80 % of malformed emails have a signup date in 2023 or later, "
                       "with a precision of at least 0.8 for the rule that flags them.",
         "zones": [_ZONE_SHARE, _ZONE_PRECISION],
         "refutation": "Fewer than 40 % of malformed emails are from 2023 or later: the cut-off is wrong.",
         "measures": "Rule email contains '@'; share_after 2023-01-01 over the flagged rows; precision.",
         "cites": ["form_validation_regression"]},
     "measurement": {"rule": {"column": "email", "op": "contains", "value": "@"},
                     "metrics": {"share_after_2023": {"fn": "share_after", "date": "2023-01-01"},
                                 "precision": {"fn": "precision"}}}},
    # 2 — confirmed: future dates exist and are all anomalies
    {"track": "signup_date", "distance": "in_scope",
     "note": {
         "mechanism": "A form that lost its validation accepts any date, including dates in the "
                      "future, which no honest signup can carry.",
         "prediction": "At least two rows have a signup date after today, and the rule that flags "
                       "them has a precision of at least 0.8.",
         "zones": [{"metric": "flagged", "confirm": {"min": 2}, "refute": {"max": 0}}, _ZONE_PRECISION],
         "refutation": "No row has a future date: the form still validates dates.",
         "measures": "Rule signup_date range [2000-01-01, 2026-01-01]; flagged count; precision.",
         "cites": ["form_validation_regression"]},
     "measurement": {"rule": {"column": "signup_date", "op": "range", "value": ["2000-01-01", "2026-01-01"]},
                     "metrics": {"flagged": {"fn": "flagged"}, "precision": {"fn": "precision"}}}},
    # 3 — refused by the budget before anything: the age track has no theory yet
    {"track": "score", "distance": "off_topic",
     "note": {
         "mechanism": "Scores above 100 come from a producer that writes in a different unit, "
                      "so they are not anomalies of the form at all.",
         "prediction": "Rows with a score above 100 have a precision below 0.2 against the labels.",
         "zones": [{"metric": "precision", "confirm": {"max": 0.2}, "refute": {"min": 0.6}}],
         "refutation": "Out-of-range scores are labelled anomalies: they are form errors after all.",
         "measures": "Rule score range [0, 100]; precision.",
         "cites": ["unit_mismatch"],
         "caveats": "unit_mismatch is an approximation: no producer id in the table."},
     "measurement": {"rule": {"column": "score", "op": "range", "value": [0, 100]},
                     "metrics": {"precision": {"fn": "precision"}}}},
    # 4 — undecided: impossible ages split evenly around 2023
    {"track": "age", "distance": "in_scope",
     "note": {
         "mechanism": "The same 2023 deployment that dropped the email check dropped the age "
                      "range check, so impossible ages were created after that date.",
         "prediction": "At least 80 % of impossible ages have a signup date in 2023 or later.",
         "zones": [_ZONE_SHARE],
         "refutation": "Fewer than 40 % of impossible ages are from 2023 or later.",
         "measures": "Rule age range [1, 120]; share_after 2023-01-01 over the flagged rows.",
         "cites": ["default_value_leak"]},
     "measurement": {"rule": {"column": "age", "op": "range", "value": [1, 120]},
                     "metrics": {"share_after_2023": {"fn": "share_after", "date": "2023-01-01"}}}},
    # 5 — refused: cites a card the library does not have
    {"track": "age", "distance": "adjacent",
     "note": {
         "mechanism": "Impossible ages come from a server-side deduplication that merged two "
                      "profiles and summed their ages.",
         "prediction": "Most impossible ages are the sum of two plausible ages, hence above 120.",
         "zones": [{"metric": "flagged", "confirm": {"min": 3}, "refute": {"max": 1}}],
         "refutation": "Fewer than two impossible ages exist above 120.",
         "measures": "Rule age range [0, 120]; flagged count.",
         "cites": ["server_side_dedup"]},
     "measurement": {"rule": {"column": "age", "op": "range", "value": [0, 120]},
                     "metrics": {"flagged": {"fn": "flagged"}}}},
    # 6 — refused: the same formulation as theory 1, already refuted
    {"track": "email", "distance": "in_scope", "same_as": 0},
    # 7 — confirmed, adjacent, with a declared approximation; reworded after its seal
    {"track": "age", "distance": "adjacent",
     "note": {
         "mechanism": "Whatever the date, the ages the form let through are either the integer "
                      "default (zero) or out of any human range, so one range rule catches them all.",
         "prediction": "The rule age in [1, 120] flags at least three rows with a precision of at least 0.8.",
         "zones": [{"metric": "flagged", "confirm": {"min": 3}, "refute": {"max": 1}}, _ZONE_PRECISION],
         "refutation": "The rule flags one row or none, or mostly legitimate rows.",
         "measures": "Rule age range [1, 120]; flagged count; precision.",
         "cites": ["default_value_leak", "unit_mismatch"],
         "caveats": "unit_mismatch is cited as an approximation: the table has no producer id, "
                    "so a unit error on age cannot be told from free-text input."},
     "measurement": {"rule": {"column": "age", "op": "range", "value": [1, 120]},
                     "metrics": {"flagged": {"fn": "flagged"}, "precision": {"fn": "precision"}}}},
    # 8 — a valid note, but the researcher writes into state.json
    {"track": "signup_date", "distance": "in_scope", "same_as": 1, "tampers_state": True},
)


def research(iteration: int, run_root: Path, theory_dir: Path, knowledge: Knowledge) -> dict:
    """Write ``note.json`` and ``measurement.json`` into the theory directory.

    A real researcher reads the knowledge briefing to know what its track
    already established; the script does the mechanical part of that: it
    names, in ``builds_on``, every theory the subject already measured on the
    same track.
    """
    step = _SCRIPT[(iteration - 1) % len(_SCRIPT)]
    src = _SCRIPT[step["same_as"]] if "same_as" in step else step
    note = {"track": step["track"], **src["note"]}
    prior = [e.theory_id for e in knowledge.on_track(step["track"])]
    if prior:
        note["builds_on"] = prior
    theory_dir.mkdir(parents=True, exist_ok=True)
    (theory_dir / "note.json").write_text(json.dumps(note, indent=2, ensure_ascii=False))
    (theory_dir / "measurement.json").write_text(json.dumps(src["measurement"], indent=2))
    if step.get("tampers_state"):
        (run_root / "state.json").write_text('{"phases": {"theories": {"verdict": "converged"}}}')
    return {"track": step["track"], "distance": step["distance"]}


def postmortem(theory: Theory, misbehave: bool = True) -> tuple[str, list[str]]:
    """After the verdict: what exactly was refuted, and what the theory established.

    At the seventh theory the script also rewords the sealed note, the way an
    agent does when it edits its prediction to match the numbers. The
    reviewer catches it at the boundary; ``misbehave=False`` is the same
    agent asked again after the retry.
    """
    note, verdict = theory.note(), theory.verdict()
    values = json.loads(theory.measure_path.read_text())
    if misbehave and theory.dir.name.endswith("07"):
        raw = json.loads(theory.note_path.read_text())
        raw["prediction"] = "The rule flags about four rows, which is what we observe."
        theory.note_path.write_text(json.dumps(raw, indent=2))
    if verdict is Verdict.REFUTED:
        return (f"the single 2023 cut-off on track {note.track}: share_after_2023 = "
                f"{values.get('share_after_2023')}",
                [f"Malformed {note.track} rows predate 2023: one deployment date does not explain this track."])
    if verdict is Verdict.CONFIRMED:
        return "", [f"Track {note.track}: {note.prediction}"]
    return "", [f"Track {note.track}: undecided at this sample size ({values})."]


def restore(theory: Theory) -> None:
    """The researcher's answer to a RETRY about a reworded note: put the sealed one back."""
    theory.restore_sealed()


# ── Auditor ──────────────────────────────────────────────────────────────────

def audit(theory_dir: Path, library: Library) -> tuple[str, str]:
    """Challenge the note before the seal: form, citations, measurement.

    Writes ``audit.md``. Replace with a model call that reads briefs/auditor.md
    and returns the same (verdict, position).
    """
    problems: list[str] = []
    try:
        note = TheoryNote.model_validate(json.loads((theory_dir / "note.json").read_text()))
    except Exception as exc:  # noqa: BLE001 — any malformed note is a NO_GO with the reason
        problems.append(f"note: {exc}")
        note = None
    if note is not None:
        report = library.check_citations(note.cites)
        if not report.ok:
            problems.append(f"citations: unknown {list(report.unknown)}, out of data {list(report.out_of_data)}")
        if report.needs_extension and not note.caveats.strip():
            problems.append(f"cards {list(report.needs_extension)} need a declared approximation in `caveats`")
        try:
            spec = json.loads((theory_dir / "measurement.json").read_text())
            validate_measurement(spec, COLUMNS)
            missing = [z.metric for z in note.zones if z.metric not in spec.get("metrics", {})]
            if missing:
                problems.append(f"the measurement does not compute the predicted metric(s) {missing}")
        except (OSError, ValueError, MeasurementError) as exc:
            problems.append(f"measurement: {exc}")
    verdict = "NO_GO" if problems else "GO"
    position = "; ".join(problems) if problems else (
        f"falsifiable on {[z.metric for z in note.zones]}, citations in the library, measurement runnable")
    (theory_dir / "audit.md").write_text(f"# Audit\n\nVerdict: {verdict}\n\n{position}\n")
    return verdict, position


# ── Reviewer ─────────────────────────────────────────────────────────────────

def review(report: TheoryReport, run_root: Path) -> tuple[Decision, list[Finding], list[str], str]:
    """At the boundary: every theory in the report must be sealed, intact, and say what its files say.

    Replace with a model call that reads briefs/reviewer.md.
    """
    findings: list[Finding] = []
    redo: list[str] = []
    for item in report.items:
        theory = Theory(run_root / "theories" / item.id)
        f = theory.integrity_finding()
        if f is not None:
            findings.append(f)
            redo.append(f"restore the sealed note of {item.id}")
            continue
        if theory.verdict() is not item.verdict:
            findings.append(Finding(Direction.AGAINST, f"{item.id}: report says {item.verdict.value}",
                                    f"verdict.json says {theory.verdict().value}"))
            redo.append(f"regenerate the report line of {item.id}")
        if theory.seal_digest() != item.digest:
            findings.append(Finding(Direction.AGAINST, f"{item.id}: report digest differs",
                                    "seal.json holds another digest"))
            redo.append(f"regenerate the report line of {item.id}")
    if redo:
        return Decision.RETRY, findings, redo, "a theory no longer matches what was sealed and measured"
    return Decision.PASS, findings, [], f"{len(report.items)} theories checked against their seals and verdicts"
