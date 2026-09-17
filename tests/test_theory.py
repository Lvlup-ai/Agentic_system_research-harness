"""A theory is written before the measurement, sealed, and judged by the zones it declared."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.research.theory import (
    AlreadySealed,
    IncompleteMeasure,
    Interval,
    MalformedNote,
    NotSealed,
    TamperedNote,
    Theory,
    UnfalsifiableNote,
    Verdict,
    Zone,
    compute_verdict,
    parse_note,
)
from agent_harness.review_loop import Direction

NOTE = {
    "track": "email",
    "mechanism": "The signup form lost its address validation in 2023, so malformed emails cluster after that date.",
    "prediction": "At least 80 % of malformed emails were created in 2023 or later; fewer than 40 % would surprise me.",
    "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}],
    "refutation": "Malformed emails spread evenly across years: the form was never the cause.",
    "measures": "Share of malformed emails with signup_date >= 2023-01-01, against all malformed emails.",
    "cites": ["form_validation_regression"],
}


def theory(tmp_path: Path, note: dict = NOTE) -> Theory:
    t = Theory(tmp_path / "theory_01")
    t.draft(note)
    return t


# ── Zones and intervals ──────────────────────────────────────────────────────

def test_an_unbounded_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one bound"):
        Interval()


def test_an_inverted_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="above max"):
        Interval(min=2, max=1)


def test_overlapping_zones_make_the_note_unfalsifiable() -> None:
    bad = {**NOTE, "zones": [{"metric": "m", "confirm": {"min": 0.5}, "refute": {"max": 0.6}}]}
    with pytest.raises(UnfalsifiableNote, match="overlap"):
        parse_note(bad)


def test_touching_zones_overlap_on_the_shared_point() -> None:
    with pytest.raises(ValueError, match="overlap"):
        Zone(metric="m", confirm=Interval(min=0.5), refute=Interval(max=0.5))


def test_a_zone_decides_confirmed_refuted_or_undecided() -> None:
    z = Zone(metric="m", confirm=Interval(min=0.8), refute=Interval(max=0.4))
    assert z.decide(0.9) is Verdict.CONFIRMED
    assert z.decide(0.8) is Verdict.CONFIRMED
    assert z.decide(0.4) is Verdict.REFUTED
    assert z.decide(0.6) is Verdict.UNDECIDED


# ── The note ─────────────────────────────────────────────────────────────────

def test_a_note_without_a_mechanism_is_malformed() -> None:
    with pytest.raises(MalformedNote, match="mechanism"):
        parse_note({**NOTE, "mechanism": "z > 2"})


def test_a_note_without_zones_is_malformed() -> None:
    with pytest.raises(MalformedNote, match="zones"):
        parse_note({**NOTE, "zones": []})


def test_an_unknown_field_is_malformed() -> None:
    with pytest.raises(MalformedNote, match="expected_pnl"):
        parse_note({**NOTE, "expected_pnl": 12})


def test_two_zones_on_one_metric_are_refused() -> None:
    z = {"metric": "m", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}
    with pytest.raises(MalformedNote, match="two zones"):
        parse_note({**NOTE, "zones": [z, z]})


def test_the_digest_ignores_formatting_but_not_content() -> None:
    a = parse_note(NOTE)
    b = parse_note(json.loads(json.dumps(NOTE, indent=4)))
    assert a.digest() == b.digest()
    c = parse_note({**NOTE, "prediction": NOTE["prediction"] + " (revised)"})
    assert c.digest() != a.digest()


def test_lineage_changes_the_seal_but_not_the_formulation() -> None:
    a = parse_note(NOTE)
    b = parse_note({**NOTE, "builds_on": ["theory_00"], "caveats": "40 rows only"})
    assert a.digest() != b.digest()
    assert a.formulation_digest() == b.formulation_digest()
    c = parse_note({**NOTE, "refutation": "Something else would refute it, at least ten words long."})
    assert c.formulation_digest() != a.formulation_digest()


# ── Verdict computation ──────────────────────────────────────────────────────

def test_one_refutation_refutes() -> None:
    zones = (Zone(metric="a", confirm=Interval(min=1), refute=Interval(max=0)),
             Zone(metric="b", confirm=Interval(min=1), refute=Interval(max=0)))
    verdict, decisions = compute_verdict(zones, {"a": 5.0, "b": -1.0})
    assert verdict is Verdict.REFUTED
    assert [d.decision for d in decisions] == [Verdict.CONFIRMED, Verdict.REFUTED]


def test_confirmation_needs_every_zone() -> None:
    zones = (Zone(metric="a", confirm=Interval(min=1), refute=Interval(max=0)),
             Zone(metric="b", confirm=Interval(min=1), refute=Interval(max=0)))
    assert compute_verdict(zones, {"a": 5.0, "b": 0.5})[0] is Verdict.UNDECIDED
    assert compute_verdict(zones, {"a": 5.0, "b": 2.0})[0] is Verdict.CONFIRMED


def test_a_measurement_missing_a_predicted_metric_is_refused() -> None:
    zones = (Zone(metric="a", confirm=Interval(min=1), refute=Interval(max=0)),)
    with pytest.raises(IncompleteMeasure, match=r"\['a'\]"):
        compute_verdict(zones, {"b": 1.0})


# ── Draft, amend, exchange, seal ─────────────────────────────────────────────

def test_draft_writes_the_note_and_nothing_else(tmp_path: Path) -> None:
    t = theory(tmp_path)
    assert t.exists and not t.sealed and t.version == 1
    assert sorted(p.name for p in t.dir.iterdir()) == ["note.json"]


def test_drafting_twice_is_refused(tmp_path: Path) -> None:
    t = theory(tmp_path)
    with pytest.raises(MalformedNote, match="use amend"):
        t.draft(NOTE)


def test_amend_archives_the_previous_version(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.amend({**NOTE, "prediction": "At least 80 % after 2023; below 40 % refutes; 40-80 % is grey."})
    assert t.version == 2
    archived = json.loads((t.dir / "versions" / "01.json").read_text())
    assert archived["prediction"] == NOTE["prediction"]
    assert t.note().prediction.endswith("is grey.")


def test_an_identical_amendment_is_a_no_op(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.amend(dict(NOTE))
    assert t.version == 1 and not (t.dir / "versions").exists()


def test_the_exchange_keeps_both_positions_with_the_version_they_addressed(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.record_exchange("auditor", "the refutation zone is too narrow to ever fire on 40 rows")
    t.amend({**NOTE, "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.5}}]})
    t.record_exchange("researcher", "widened the refutation zone to 0.5")
    ex = t.exchange()
    assert [(e.role, e.note_version) for e in ex] == [("auditor", 1), ("researcher", 2)]


def test_seal_freezes_the_note(tmp_path: Path) -> None:
    t = theory(tmp_path)
    digest = t.seal()
    assert t.sealed and digest == t.note().digest()
    assert t.seal() == digest, "sealing again changes nothing"
    with pytest.raises(AlreadySealed):
        t.amend({**NOTE, "prediction": "something else entirely, after the fact"})
    with pytest.raises(AlreadySealed):
        t.record_exchange("auditor", "too late")


# ── Measure and verdict ──────────────────────────────────────────────────────

def test_measuring_an_unsealed_theory_is_refused(tmp_path: Path) -> None:
    t = theory(tmp_path)
    with pytest.raises(NotSealed):
        t.record_measure({"share_after_2023": 0.9})
    assert not t.measured


def test_the_verdict_is_computed_from_the_sealed_zones(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.seal()
    verdict, decisions = t.record_measure({"share_after_2023": 0.33})
    assert verdict is Verdict.REFUTED and t.verdict() is Verdict.REFUTED
    saved = json.loads(t.verdict_path.read_text())
    assert saved["digest"] == t.seal_digest()
    assert saved["decisions"][0]["decision"] == "refuted"


def test_the_grey_zone_is_undecided(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.seal()
    assert t.record_measure({"share_after_2023": 0.6})[0] is Verdict.UNDECIDED


# ── Tampering ────────────────────────────────────────────────────────────────

def test_a_note_reworded_after_the_seal_is_detected(tmp_path: Path) -> None:
    t = theory(tmp_path)
    t.seal()
    raw = json.loads(t.note_path.read_text())
    raw["prediction"] = "Actually I expected about a third, which is what we see."
    t.note_path.write_text(json.dumps(raw))
    with pytest.raises(TamperedNote, match="reworded after the fact"):
        t.verify()
    with pytest.raises(TamperedNote):
        t.record_measure({"share_after_2023": 0.33})
    assert not t.measured


def test_integrity_finding_is_against_and_names_the_theory(tmp_path: Path) -> None:
    t = theory(tmp_path)
    assert t.seal() and t.integrity_finding() is None
    raw = json.loads(t.note_path.read_text())
    raw["mechanism"] = raw["mechanism"] + " Or maybe something else."
    t.note_path.write_text(json.dumps(raw))
    f = t.integrity_finding()
    assert f is not None and f.direction is Direction.AGAINST and "theory_01" in f.claim


def test_an_unsealed_measured_theory_is_a_finding_too(tmp_path: Path) -> None:
    t = theory(tmp_path)
    f = t.integrity_finding()
    assert f is not None and "without a seal" in f.claim


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "agent_harness.research.theory", *args],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def test_cli_drives_a_theory_to_its_verdict(tmp_path: Path) -> None:
    note = tmp_path / "note.json"
    note.write_text(json.dumps(NOTE))
    values = tmp_path / "values.json"
    values.write_text(json.dumps({"share_after_2023": 0.91}))
    d = str(tmp_path / "theory_01")

    code, out = _cli("draft", "--dir", d, "--note", str(note))
    assert code == 0 and out["metrics"] == ["share_after_2023"]
    code, out = _cli("measure", "--dir", d, "--values", str(values))
    assert code == 2 and out["error"] == "NotSealed"
    code, out = _cli("seal", "--dir", d)
    assert code == 0 and len(out["digest"]) == 64
    code, out = _cli("amend", "--dir", d, "--note", str(note))
    assert code == 2 and out["error"] == "AlreadySealed"
    code, out = _cli("measure", "--dir", d, "--values", str(values))
    assert code == 0 and out["verdict"] == "confirmed"
    code, out = _cli("status", "--dir", d)
    assert out["sealed"] and out["verdict"] == "confirmed" and out["version"] == 1


def test_cli_refuses_an_unfalsifiable_note(tmp_path: Path) -> None:
    note = tmp_path / "note.json"
    note.write_text(json.dumps({**NOTE, "zones": [
        {"metric": "m", "confirm": {"min": 0.0}, "refute": {"max": 1.0}}]}))
    code, out = _cli("draft", "--dir", str(tmp_path / "t"), "--note", str(note))
    assert code == 2 and out["error"] == "UnfalsifiableNote"
    assert not (tmp_path / "t").exists()
