"""The theory note: declared before the measurement, sealed, then judged by its own zones.

Why this module exists
----------------------
A researcher agent that sees the numbers before writing what it expected will
write what it saw. Left to itself, a research loop drifts into three failures:
"theories" that are parametric variants of one idea with no mechanism behind
them, criteria of refutation that fire on pure noise, and theories quietly
reworded after the measurement to fit the result. All three are invisible in
the final numbers. This module makes the discipline that prevents them
mechanical:

1. **The note comes first.** Mechanism, quantified prediction, what would
   prove it wrong, what is measured and why that tests *this* mechanism. No
   note, no trial.
2. **Falsifiable or refused.** Every metric the note predicts carries a zone
   of confirmation and a zone of refutation, disjoint, both bounded. What is
   in neither is the grey zone, and it is named before the measurement:
   "undecided" is a verdict declared in advance, not a qualification made
   after the fact.
3. **Sealed before measured.** Amendments are free until the seal (the
   auditor's challenge happens here, and each version is archived). After the
   seal the note is a hash; a measurement is refused on an unsealed note.
4. **The verdict is computed.** ``refuted`` if any metric falls in its
   refutation zone, ``confirmed`` if every metric falls in its confirmation
   zone, ``undecided`` otherwise. The researcher does not pick it.
5. **Rewording is detected.** A note whose content no longer matches its seal
   yields a finding *against* the deliverable for the review loop.

Layout on disk (one directory per theory)::

    <dir>/note.json          the current note
    <dir>/versions/NN.json   every earlier version, archived at each amendment
    <dir>/exchange.json      the auditor/researcher positions, before the seal
    <dir>/seal.json          the hash and the time; present means sealed
    <dir>/measure.json       the measured values
    <dir>/verdict.json       the computed verdict and how each zone decided
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "AlreadySealed",
    "Exchange",
    "IncompleteMeasure",
    "Interval",
    "MalformedNote",
    "NotSealed",
    "TamperedNote",
    "Theory",
    "TheoryError",
    "TheoryNote",
    "UnfalsifiableNote",
    "Verdict",
    "Zone",
    "ZoneDecision",
]


# ── Errors ───────────────────────────────────────────────────────────────────

class TheoryError(ValueError):
    """The note or the operation was refused. The message says why."""


class MalformedNote(TheoryError):
    pass


class UnfalsifiableNote(TheoryError):
    pass


class AlreadySealed(TheoryError):
    pass


class NotSealed(TheoryError):
    pass


class IncompleteMeasure(TheoryError):
    pass


class TamperedNote(TheoryError):
    pass


# ── Vocabulary ───────────────────────────────────────────────────────────────

class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    UNDECIDED = "undecided"


# ── The note ─────────────────────────────────────────────────────────────────

class Interval(BaseModel):
    """A closed interval with at least one finite bound. ``None`` means unbounded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounded_and_ordered(self) -> "Interval":
        if self.min is None and self.max is None:
            raise ValueError("an interval needs at least one bound; unbounded means 'always'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"interval min {self.min} is above max {self.max}")
        return self

    def contains(self, x: float) -> bool:
        return (self.min is None or x >= self.min) and (self.max is None or x <= self.max)

    def overlaps(self, other: "Interval") -> bool:
        lo = max(v for v in (self.min, other.min) if v is not None) if (self.min is not None or other.min is not None) else None
        hi = min(v for v in (self.max, other.max) if v is not None) if (self.max is not None or other.max is not None) else None
        if lo is None or hi is None:
            return True
        return lo <= hi


class Zone(BaseModel):
    """What one metric must do to confirm or refute the theory. The rest is grey."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str = Field(min_length=1)
    confirm: Interval
    refute: Interval

    @model_validator(mode="after")
    def _disjoint(self) -> "Zone":
        if self.confirm.overlaps(self.refute):
            raise ValueError(
                f"metric {self.metric!r}: the confirmation and refutation zones overlap; "
                "a value cannot both confirm and refute")
        return self

    def decide(self, value: float) -> Verdict:
        if self.refute.contains(value):
            return Verdict.REFUTED
        if self.confirm.contains(value):
            return Verdict.CONFIRMED
        return Verdict.UNDECIDED


# Fields that describe where a note comes from, not what it claims. They are
# part of the seal (the note as written) but not of the formulation (the theory).
LINEAGE_FIELDS: tuple[str, ...] = ("builds_on", "caveats")


class TheoryNote(BaseModel):
    """What the researcher writes before spending a trial. Strict: no extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    track: str = Field(min_length=1, description="the track of the idea this theory serves")
    mechanism: str = Field(min_length=20, description="why the world would do this; not a measurement")
    prediction: str = Field(min_length=10, description="what is expected, in numbers, before looking")
    zones: tuple[Zone, ...] = Field(min_length=1)
    refutation: str = Field(min_length=10, description="the result that would mean the theory is wrong")
    measures: str = Field(min_length=10, description="what is measured and why it tests this mechanism")
    cites: tuple[str, ...] = Field(default=(), description="library cards this theory rests on")
    builds_on: tuple[str, ...] = Field(default=(), description="earlier theories whose verdicts it uses")
    caveats: str = Field(default="", description="what this measurement cannot decide")

    @model_validator(mode="after")
    def _one_zone_per_metric(self) -> "TheoryNote":
        metrics = [z.metric for z in self.zones]
        dupes = sorted({m for m in metrics if metrics.count(m) > 1})
        if dupes:
            raise ValueError(f"a metric has two zones: {dupes}")
        return self

    def canonical(self) -> str:
        """The text that is hashed: sorted keys, no whitespace games."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))

    def digest(self) -> str:
        """The seal: the whole note, lineage included."""
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def formulation_digest(self) -> str:
        """The theory itself, without its lineage.

        Two notes with the same mechanism, prediction, zones, refutation,
        measures and citations are the same theory, whatever they say they
        build on. This is what "the same theory is not refuted twice" compares.
        """
        data = self.model_dump(mode="json")
        for f in LINEAGE_FIELDS:
            data.pop(f, None)
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_note(raw: Mapping) -> TheoryNote:
    """Validate a raw note; refusals are TheoryErrors with the reason."""
    try:
        return TheoryNote.model_validate(raw)
    except ValidationError as exc:
        text = str(exc)
        if "overlap" in text or "at least one bound" in text:
            raise UnfalsifiableNote(text) from exc
        raise MalformedNote(text) from exc


# ── The exchange before the seal ─────────────────────────────────────────────

class Exchange(BaseModel):
    """One position in the auditor/researcher exchange. Both sides are kept."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1)
    position: str = Field(min_length=1)
    note_version: int


# ── Decisions ────────────────────────────────────────────────────────────────

class ZoneDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    value: float
    decision: Verdict


def compute_verdict(zones: tuple[Zone, ...], values: Mapping[str, float]) -> tuple[Verdict, tuple[ZoneDecision, ...]]:
    """One refutation refutes; confirmation needs every zone; the rest is undecided."""
    missing = [z.metric for z in zones if z.metric not in values]
    if missing:
        raise IncompleteMeasure(f"the measurement lacks the metric(s) the note predicts: {missing}")
    decisions = tuple(ZoneDecision(metric=z.metric, value=float(values[z.metric]),
                                   decision=z.decide(float(values[z.metric]))) for z in zones)
    kinds = {d.decision for d in decisions}
    if Verdict.REFUTED in kinds:
        verdict = Verdict.REFUTED
    elif kinds == {Verdict.CONFIRMED}:
        verdict = Verdict.CONFIRMED
    else:
        verdict = Verdict.UNDECIDED
    return verdict, decisions


# ── The theory on disk ───────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Theory:
    """One theory directory and the operations allowed on it, in order."""

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    # -- paths ---------------------------------------------------------------

    @property
    def note_path(self) -> Path:
        return self.dir / "note.json"

    @property
    def seal_path(self) -> Path:
        return self.dir / "seal.json"

    @property
    def measure_path(self) -> Path:
        return self.dir / "measure.json"

    @property
    def verdict_path(self) -> Path:
        return self.dir / "verdict.json"

    @property
    def exchange_path(self) -> Path:
        return self.dir / "exchange.json"

    @property
    def versions_dir(self) -> Path:
        return self.dir / "versions"

    # -- state ---------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self.note_path.exists()

    @property
    def sealed(self) -> bool:
        return self.seal_path.exists()

    @property
    def measured(self) -> bool:
        return self.verdict_path.exists()

    @property
    def version(self) -> int:
        """1 for a fresh draft, +1 per amendment."""
        if not self.versions_dir.exists():
            return 1 if self.exists else 0
        return len(list(self.versions_dir.glob("*.json"))) + 1

    def note(self) -> TheoryNote:
        if not self.exists:
            raise MalformedNote(f"no note at {self.note_path}")
        return parse_note(json.loads(self.note_path.read_text(encoding="utf-8")))

    def seal_digest(self) -> str | None:
        if not self.sealed:
            return None
        return json.loads(self.seal_path.read_text(encoding="utf-8"))["digest"]

    def exchange(self) -> tuple[Exchange, ...]:
        if not self.exchange_path.exists():
            return ()
        raw = json.loads(self.exchange_path.read_text(encoding="utf-8"))
        return tuple(Exchange.model_validate(e) for e in raw)

    # -- operations, in order --------------------------------------------------

    def draft(self, raw: Mapping) -> TheoryNote:
        """Write the first version. Refused if a note already exists (amend instead)."""
        if self.exists:
            raise AlreadySealed("a note exists; use amend()") if self.sealed else \
                MalformedNote("a note exists; use amend()")
        note = parse_note(raw)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.note_path.write_text(json.dumps(note.model_dump(mode="json"), indent=2,
                                             ensure_ascii=False), encoding="utf-8")
        return note

    def amend(self, raw: Mapping) -> TheoryNote:
        """Replace the note before the seal; the previous version is archived, never lost."""
        if self.sealed:
            raise AlreadySealed("the note is sealed: it cannot change any more")
        current = self.note()
        note = parse_note(raw)
        if note.canonical() == current.canonical():
            return note
        self.versions_dir.mkdir(exist_ok=True)
        n = self.version
        (self.versions_dir / f"{n:02d}.json").write_text(
            self.note_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.note_path.write_text(json.dumps(note.model_dump(mode="json"), indent=2,
                                             ensure_ascii=False), encoding="utf-8")
        return note

    def record_exchange(self, role: str, position: str) -> Exchange:
        """An auditor's challenge or the researcher's reply, before the seal."""
        if self.sealed:
            raise AlreadySealed("the exchange happens before the seal; after it, use the review loop")
        if not self.exists:
            raise MalformedNote("no note to challenge")
        entry = Exchange(role=role, position=position, note_version=self.version)
        entries = [*self.exchange(), entry]
        self.exchange_path.write_text(
            json.dumps([e.model_dump() for e in entries], indent=2, ensure_ascii=False),
            encoding="utf-8")
        return entry

    def seal(self) -> str:
        """Freeze the note. Returns its digest. Idempotent."""
        note = self.note()
        if self.sealed:
            return self.seal_digest()  # type: ignore[return-value]
        digest = note.digest()
        self.seal_path.write_text(json.dumps({
            "digest": digest, "version": self.version, "sealed_at": _now()}, indent=2),
            encoding="utf-8")
        return digest

    def verify(self) -> str:
        """The note still matches its seal. Raises TamperedNote otherwise."""
        if not self.sealed:
            raise NotSealed("the note is not sealed; nothing to verify against")
        expected = self.seal_digest()
        actual = self.note().digest()
        if actual != expected:
            raise TamperedNote(
                f"the note changed after its seal (sealed {expected[:12]}…, now {actual[:12]}…): "
                "a theory reworded after the fact is contested outright")
        return actual  # type: ignore[return-value]

    def record_measure(self, values: Mapping[str, float]) -> tuple[Verdict, tuple[ZoneDecision, ...]]:
        """Store the measurement and compute the verdict from the sealed zones."""
        if not self.sealed:
            raise NotSealed("measuring an unsealed theory is refused: seal it first")
        self.verify()
        note = self.note()
        verdict, decisions = compute_verdict(note.zones, values)
        self.measure_path.write_text(json.dumps(dict(values), indent=2, sort_keys=True),
                                     encoding="utf-8")
        self.verdict_path.write_text(json.dumps({
            "verdict": verdict.value,
            "decisions": [d.model_dump() for d in decisions],
            "digest": self.seal_digest(),
            "decided_at": _now(),
        }, indent=2), encoding="utf-8")
        return verdict, decisions

    def verdict(self) -> Verdict | None:
        if not self.measured:
            return None
        return Verdict(json.loads(self.verdict_path.read_text(encoding="utf-8"))["verdict"])

    # -- for the review loop ---------------------------------------------------

    def integrity_finding(self):
        """A Finding AGAINST the deliverable when the note was reworded, else None."""
        from agent_harness.review_loop import Direction, Finding
        try:
            self.verify()
        except NotSealed:
            return Finding(Direction.AGAINST, f"{self.dir.name}: measured without a seal",
                           "no seal.json next to the note")
        except TamperedNote as exc:
            return Finding(Direction.AGAINST, f"{self.dir.name}: note reworded after its seal",
                           str(exc))
        return None

    def summary(self) -> dict:
        note = self.note() if self.exists else None
        return {
            "dir": str(self.dir), "exists": self.exists, "version": self.version,
            "sealed": self.sealed, "digest": self.seal_digest(),
            "measured": self.measured,
            "verdict": self.verdict().value if self.measured else None,
            "track": note.track if note else None,
            "metrics": [z.metric for z in note.zones] if note else [],
            "exchange_entries": len(self.exchange()),
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.research.theory",
        description="Draft, amend, seal, measure and verify a theory note. JSON out; exit 2 on refusal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", required=True, type=Path)

    d = sub.add_parser("draft", parents=[common]); d.add_argument("--note", required=True, type=Path)
    a = sub.add_parser("amend", parents=[common]); a.add_argument("--note", required=True, type=Path)
    x = sub.add_parser("exchange", parents=[common])
    x.add_argument("--role", required=True); x.add_argument("--position", required=True)
    sub.add_parser("seal", parents=[common])
    sub.add_parser("verify", parents=[common])
    m = sub.add_parser("measure", parents=[common]); m.add_argument("--values", required=True, type=Path)
    sub.add_parser("status", parents=[common])

    args = parser.parse_args(argv)
    theory = Theory(args.dir)
    try:
        if args.cmd == "draft":
            note = theory.draft(json.loads(args.note.read_text(encoding="utf-8")))
            out = {"ok": True, "version": theory.version, "metrics": [z.metric for z in note.zones]}
        elif args.cmd == "amend":
            theory.amend(json.loads(args.note.read_text(encoding="utf-8")))
            out = {"ok": True, "version": theory.version}
        elif args.cmd == "exchange":
            e = theory.record_exchange(args.role, args.position)
            out = {"ok": True, "entries": len(theory.exchange()), "note_version": e.note_version}
        elif args.cmd == "seal":
            out = {"ok": True, "digest": theory.seal(), "version": theory.version}
        elif args.cmd == "verify":
            out = {"ok": True, "digest": theory.verify()}
        elif args.cmd == "measure":
            verdict, decisions = theory.record_measure(json.loads(args.values.read_text(encoding="utf-8")))
            out = {"ok": True, "verdict": verdict.value,
                   "decisions": [dd.model_dump() for dd in decisions]}
        else:
            out = {"ok": True, **theory.summary()}
    except TheoryError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.research.theory import main as _main

    sys.exit(_main())
