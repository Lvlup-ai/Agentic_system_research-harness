"""The step token: no seal without clearance, no measurement without payment, no forgery."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.budget import Budget, save_run
from agent_harness.research.knowledge import Knowledge
from agent_harness.research.library import Library
from agent_harness.research.theory import AlreadyMeasured, Theory, Verdict
from agent_harness.research.trial import (
    BadStamp,
    Passport,
    ProtocolError,
    Refused,
    StepMissing,
    StepRepeated,
    clear,
    consume,
    load_secret,
    new_secret,
)

NOTE = {
    "track": "email",
    "mechanism": "The signup form lost its address validation at one deployment, so malformed emails cluster after it.",
    "prediction": "At least 80 % of malformed emails are from 2023 or later.",
    "zones": [{"metric": "share_after_2023", "confirm": {"min": 0.8}, "refute": {"max": 0.4}}],
    "refutation": "Fewer than 40 % of malformed emails are from 2023 or later.",
    "measures": "Rule email contains @; share_after 2023-01-01.",
    "cites": ["form_validation_regression"],
}


@pytest.fixture
def lab(tmp_path: Path) -> dict:
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "form_validation_regression.md").write_text("---\ntitle: F\nstatus: usable\n---\nWhy.\n")
    (lib / "unit_mismatch.md").write_text("---\ntitle: U\nstatus: needs_extension\n---\nWhy.\n")
    theory = Theory(tmp_path / "runs" / "r1" / "theories" / "t01")
    theory.draft(NOTE)
    secret = new_secret(tmp_path / ".secret")
    return {
        "tmp": tmp_path,
        "theory": theory,
        "knowledge": Knowledge(tmp_path / "knowledge", "s"),
        "library": Library(lib),
        "budget": Budget.load(tmp_path / "budget", "s", "first_pass", total=5, tracks=("email",)),
        "passport": Passport(theory.dir / "passport.json", "t01", secret),
        "secret": secret,
    }


def full_protocol(lab: dict, value: float = 0.0) -> Verdict:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    t.seal(p)
    consume(t, lab["budget"], p)
    verdict, _ = t.record_measure({"share_after_2023": value}, p)
    return verdict


# ── Secrets ──────────────────────────────────────────────────────────────────

def test_a_secret_is_created_once_and_reloaded(tmp_path: Path) -> None:
    s1 = new_secret(tmp_path / "sec")
    assert len(s1) == 32 and (tmp_path / "sec").stat().st_mode & 0o777 == 0o600
    assert new_secret(tmp_path / "sec") == s1 == load_secret(tmp_path / "sec")


def test_a_short_secret_is_refused(tmp_path: Path) -> None:
    (tmp_path / "sec").write_text("abc")
    with pytest.raises(ProtocolError, match="too short"):
        load_secret(tmp_path / "sec")


# ── The chain ────────────────────────────────────────────────────────────────

def test_the_full_protocol_stamps_four_steps_in_order(lab: dict) -> None:
    assert full_protocol(lab) is Verdict.REFUTED
    assert [s["step"] for s in lab["passport"].stamps()] == ["cleared", "sealed", "consumed", "measured"]
    assert lab["passport"].find("consumed")["data"]["cost"] == 1
    assert lab["passport"].find("measured")["data"]["verdict"] == "refuted"


def test_no_seal_without_clearance(lab: dict) -> None:
    with pytest.raises(StepMissing, match="'cleared' is required"):
        lab["theory"].seal(lab["passport"])
    assert not lab["theory"].sealed


def test_an_amended_note_must_be_cleared_again(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    t.amend({**NOTE, "prediction": "At least 80 % are from 2024 or later, say."})
    with pytest.raises(StepMissing, match="another version of the note"):
        t.seal(p)
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)   # clearing repeats
    t.seal(p)
    assert [s["step"] for s in p.stamps()] == ["cleared", "cleared", "sealed"]


def test_no_payment_without_seal_and_no_measurement_without_payment(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    with pytest.raises(StepMissing, match="not sealed"):
        consume(t, lab["budget"], p)
    t.seal(p)
    with pytest.raises(StepMissing, match="'consumed' is required"):
        t.record_measure({"share_after_2023": 0.9}, p)
    assert not t.measured and lab["budget"].spent_this_run() == 0


def test_the_distance_paid_is_the_distance_cleared(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "adjacent", p)
    t.seal(p)
    trial = consume(t, lab["budget"], p)
    assert trial.distance == "adjacent" and trial.cost == 2


def test_a_step_does_not_repeat(lab: dict) -> None:
    full_protocol(lab)
    with pytest.raises(StepRepeated):
        lab["passport"].stamp("sealed", lab["theory"].seal_digest())
    with pytest.raises(AlreadyMeasured):
        lab["theory"].record_measure({"share_after_2023": 0.9}, lab["passport"])


# ── Forgery ──────────────────────────────────────────────────────────────────

def test_an_edited_stamp_does_not_verify(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    lines = p.path.read_text().splitlines()
    s = json.loads(lines[0]); s["data"]["distance"] = "off_topic"
    p.path.write_text(json.dumps(s, sort_keys=True) + "\n")
    with pytest.raises(BadStamp, match="does not verify"):
        t.seal(p)


def test_a_stamp_signed_with_another_secret_does_not_verify(lab: dict) -> None:
    t = lab["theory"]
    forged = Passport(t.dir / "passport.json", "t01", b"x" * 32)
    forged.stamp("cleared", t.note().digest(), distance="in_scope")
    with pytest.raises(BadStamp):
        t.seal(lab["passport"])


def test_a_passport_of_another_theory_is_refused(lab: dict) -> None:
    t = lab["theory"]
    other = Passport(t.dir / "passport.json", "t99", lab["secret"])
    other.stamp("cleared", t.note().digest(), distance="in_scope")
    with pytest.raises(BadStamp):
        t.seal(lab["passport"])


def test_deleting_the_seal_after_measuring_is_tampering(lab: dict) -> None:
    full_protocol(lab)
    t = lab["theory"]
    t.seal_path.unlink()
    with pytest.raises(ValueError, match="seal is gone"):
        t.verify()
    with pytest.raises(ValueError, match="seal was removed after the measurement"):
        t.seal(lab["passport"])


# ── Clearance refusals ───────────────────────────────────────────────────────

def test_clearance_refuses_for_the_module_that_said_no(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    with pytest.raises(Refused, match="^library: unknown card") as e:
        t.amend({**NOTE, "cites": ["made_up"]}); clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    assert e.value.cause == "library"
    t.amend({**NOTE, "cites": ["unit_mismatch"]})
    with pytest.raises(Refused, match="^library: card\\(s\\) \\['unit_mismatch'\\] need a declared"):
        clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    t.amend(NOTE)
    with pytest.raises(Refused, match="^budget: 1 declared track"):
        clear(t, lab["knowledge"], lab["library"], lab["budget"], "off_topic", p)
    assert p.stamps() == [], "a refusal stamps nothing"


def test_clearance_refuses_a_note_already_refuted(lab: dict) -> None:
    full_protocol(lab, 0.0)
    lab["knowledge"].record(lab["theory"], "r1", refuted_exactly="the 2023 cut-off", passport=lab["passport"])
    t2 = Theory(lab["tmp"] / "runs" / "r1" / "theories" / "t02")
    t2.draft({**NOTE, "builds_on": ["t01"]})
    p2 = Passport(t2.dir / "passport.json", "t02", lab["secret"])
    with pytest.raises(Refused, match="^knowledge: this exact theory was refuted as t01"):
        clear(t2, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p2)


def test_knowledge_record_requires_the_measured_stamp(lab: dict) -> None:
    t, p = lab["theory"], lab["passport"]
    clear(t, lab["knowledge"], lab["library"], lab["budget"], "in_scope", p)
    t.seal(p); consume(t, lab["budget"], p)
    t.record_measure({"share_after_2023": 0.9})           # measured without the passport
    with pytest.raises(StepMissing, match="'measured' is required"):
        lab["knowledge"].record(t, "r1", passport=p)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(module: str, *args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.returncode, {"stdout": proc.stdout, "stderr": proc.stderr}


def test_cli_protocol_end_to_end(lab: dict) -> None:
    tmp, t = lab["tmp"], lab["theory"]
    run_file = tmp / "runs" / "r1" / "budget.json"
    save_run(lab["budget"], run_file, tmp / "budget")
    secret = str(tmp / ".secret")
    common = ("--dir", str(t.dir), "--secret-file", secret, "--budget-run-file", str(run_file))

    code, out = _cli("agent_harness.research.theory", "seal", "--dir", str(t.dir))
    assert code == 2 and "needs --secret-file" in out["detail"]
    code, out = _cli("agent_harness.research.theory", "seal", "--dir", str(t.dir), "--secret-file", secret)
    assert code == 2 and out["error"] == "StepMissing"
    code, out = _cli("agent_harness.research.trial", "clear", *common, "--distance", "in_scope",
                     "--store", str(tmp / "knowledge"), "--subject", "s", "--library", str(tmp / "library"))
    assert code == 0 and out["step"] == "cleared" and out["data"]["distance"] == "in_scope"
    code, out = _cli("agent_harness.research.theory", "seal", "--dir", str(t.dir), "--secret-file", secret)
    assert code == 0 and out["guarded"] is True
    values = tmp / "values.json"; values.write_text('{"share_after_2023": 0.95}')
    code, out = _cli("agent_harness.research.theory", "measure", "--dir", str(t.dir),
                     "--secret-file", secret, "--values", str(values))
    assert code == 2 and out["error"] == "StepMissing"
    code, out = _cli("agent_harness.research.trial", "consume", *common)
    assert code == 0 and out["cost"] == 1 and out["remaining"] == 4
    code, out = _cli("agent_harness.research.theory", "measure", "--dir", str(t.dir),
                     "--secret-file", secret, "--values", str(values))
    assert code == 0 and out["verdict"] == "confirmed"
    code, out = _cli("agent_harness.research.trial", "status", *common)
    assert out["steps"] == ["cleared", "sealed", "consumed", "measured"]
    code, out = _cli("agent_harness.research.theory", "measure", "--dir", str(t.dir),
                     "--secret-file", secret, "--values", str(values))
    assert code == 2 and out["error"] == "AlreadyMeasured"


def test_cli_unguarded_is_explicit(lab: dict) -> None:
    t = lab["theory"]
    code, out = _cli("agent_harness.research.theory", "seal", "--dir", str(t.dir), "--unguarded")
    assert code == 0 and out["guarded"] is False
