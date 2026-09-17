"""What past theories established: the memory the next theory must build on.

Why this module exists
----------------------
A researcher that starts every run from the idea alone re-pays what earlier
runs already paid. The original pipeline kept a section called "what
experience established, and no run must pay for again", and its researcher
rewrote the starting idea after dozens of trials with a number behind every
claim. That memory was a document; this module makes it a store the harness
reads and enforces:

* every measured theory is **recorded** with its verdict, its sealed digest,
  and, when refuted, *what exactly* was refuted (often a formulation, not the
  idea);
* a new note whose **formulation** (mechanism, prediction, zones, refutation,
  measures, citations; lineage excluded) is identical to a **refuted** one on
  the same subject is refused: the same theory is not measured twice;
* a new note on a track that already has verdicts must say which ones it
  **builds on**; a note that ignores its own history is refused;
* **lessons** are short statements a run established; they are what the
  briefs must carry, and ``prompt_tests`` can check that they do.

The store is one JSON-lines file per subject, append-only. A line is never
edited: a later line may supersede an earlier one, and both stay.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_harness.research.theory import Theory, TheoryNote, Verdict, parse_note

__all__ = [
    "AlreadyRefuted",
    "Entry",
    "IgnoresHistory",
    "Knowledge",
    "KnowledgeError",
    "NotRecordable",
]


class KnowledgeError(ValueError):
    pass


class NotRecordable(KnowledgeError):
    """The theory is not measured, or its note no longer matches its seal."""


class AlreadyRefuted(KnowledgeError):
    """The same theory, word for word, was already refuted on this subject."""


class IgnoresHistory(KnowledgeError):
    """The track has verdicts the note does not build on."""


class Entry(BaseModel):
    """One measured theory, as the next run will read it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    theory_id: str
    run_id: str
    track: str
    digest: str                       # the seal, lineage included
    formulation: str = ""             # the theory itself, lineage excluded
    mechanism: str
    prediction: str
    verdict: Verdict
    decisions: tuple[dict, ...] = ()
    refuted_exactly: str = Field(default="", description="what precisely was refuted; a formulation, usually")
    lessons: tuple[str, ...] = Field(default=(), description="statements this theory established")
    cites: tuple[str, ...] = ()
    builds_on: tuple[str, ...] = ()
    recorded_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Knowledge:
    """The append-only memory of one subject."""

    store: Path
    subject: str
    entries: list[Entry] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.store = Path(self.store)
        self.entries = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(Entry.model_validate_json(line))

    @property
    def path(self) -> Path:
        return self.store / f"{self.subject}.jsonl"

    # -- reading -------------------------------------------------------------

    def on_track(self, track: str) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.track == track)

    def tracks(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for e in self.entries:
            t = out.setdefault(e.track, {v.value: 0 for v in Verdict})
            t[e.verdict.value] += 1
        return out

    def refuted_formulations(self) -> frozenset[str]:
        return frozenset(e.formulation for e in self.entries if e.verdict is Verdict.REFUTED)

    def lessons(self) -> tuple[str, ...]:
        """Every established statement, in order, without duplicates."""
        seen: list[str] = []
        for e in self.entries:
            for lesson in e.lessons:
                if lesson not in seen:
                    seen.append(lesson)
        return tuple(seen)

    def ids(self) -> frozenset[str]:
        return frozenset(e.theory_id for e in self.entries)

    def briefing(self, track: str | None = None) -> str:
        """What the researcher reads before writing the next note. Plain text."""
        entries = self.on_track(track) if track else tuple(self.entries)
        if not entries:
            return f"No theory measured yet on subject `{self.subject}`" + (f", track `{track}`." if track else ".")
        lines = [f"Established on subject `{self.subject}`" + (f", track `{track}`" if track else "") + ":"]
        for e in entries:
            line = f"- {e.theory_id} ({e.run_id}, {e.track}): {e.verdict.value}. {e.prediction}"
            if e.refuted_exactly:
                line += f" Refuted exactly: {e.refuted_exactly}"
            lines.append(line)
        if self.lessons():
            lines += ["", "Lessons no run must pay for again:"]
            lines += [f"- {l}" for l in self.lessons()]
        return "\n".join(lines)

    # -- checks before a new trial -------------------------------------------------

    def check_note(self, note: TheoryNote | dict) -> None:
        """Refuse a note that re-pays a refutation or ignores its track's history."""
        if not isinstance(note, TheoryNote):
            note = parse_note(note)
        formulation = note.formulation_digest()
        if formulation in self.refuted_formulations():
            prior = next(e for e in self.entries if e.formulation == formulation)
            raise AlreadyRefuted(
                f"this exact theory was refuted as {prior.theory_id} ({prior.run_id})"
                + (f": {prior.refuted_exactly}" if prior.refuted_exactly else "")
                + ". Change the formulation, or the track.")
        history = self.on_track(note.track)
        if history:
            known = {e.theory_id for e in history}
            cited = set(note.builds_on)
            if not cited & known:
                raise IgnoresHistory(
                    f"track {note.track!r} already has {len(history)} verdict(s) "
                    f"({', '.join(sorted(known))}); the note must say which it builds on")
            unknown = cited - self.ids()
            if unknown:
                raise IgnoresHistory(f"builds_on names theories this subject never measured: {sorted(unknown)}")

    # -- recording -----------------------------------------------------------

    def record(self, theory: Theory, run_id: str, refuted_exactly: str = "",
               lessons: Sequence[str] = ()) -> Entry:
        """Append a measured, intact theory. Nothing is ever rewritten."""
        if not theory.measured:
            raise NotRecordable(f"{theory.dir.name}: no verdict yet; a theory is recorded once measured")
        theory.verify()  # a reworded note is not knowledge
        note = theory.note()
        verdict = theory.verdict()
        assert verdict is not None
        if verdict is Verdict.REFUTED and not refuted_exactly.strip():
            raise NotRecordable(
                f"{theory.dir.name}: a refutation must say what exactly was refuted; "
                "'refuted' alone closes a track for the wrong reason")
        raw = json.loads(theory.verdict_path.read_text(encoding="utf-8"))
        entry = Entry(theory_id=theory.dir.name, run_id=run_id, track=note.track,
                      digest=theory.seal_digest() or "", formulation=note.formulation_digest(),
                      mechanism=note.mechanism,
                      prediction=note.prediction, verdict=verdict,
                      decisions=tuple(raw.get("decisions", ())),
                      refuted_exactly=refuted_exactly.strip(),
                      lessons=tuple(l.strip() for l in lessons if l.strip()),
                      cites=note.cites, builds_on=note.builds_on, recorded_at=_now())
        self.store.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
        self.entries.append(entry)
        return entry


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.research.knowledge",
        description="Read and append what past theories established. JSON out; exit 2 on refusal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", required=True, type=Path)
    common.add_argument("--subject", required=True)

    b = sub.add_parser("briefing", parents=[common]); b.add_argument("--track", default=None)
    sub.add_parser("tracks", parents=[common])
    sub.add_parser("lessons", parents=[common])
    c = sub.add_parser("check", parents=[common]); c.add_argument("--note", required=True, type=Path)
    r = sub.add_parser("record", parents=[common])
    r.add_argument("--theory-dir", required=True, type=Path)
    r.add_argument("--run-id", required=True)
    r.add_argument("--refuted-exactly", default="")
    r.add_argument("--lesson", action="append", default=[])

    args = parser.parse_args(argv)
    k = Knowledge(args.store, args.subject)
    try:
        if args.cmd == "briefing":
            out = {"ok": True, "briefing": k.briefing(args.track)}
        elif args.cmd == "tracks":
            out = {"ok": True, "tracks": k.tracks(), "entries": len(k.entries)}
        elif args.cmd == "lessons":
            out = {"ok": True, "lessons": list(k.lessons())}
        elif args.cmd == "check":
            k.check_note(json.loads(args.note.read_text(encoding="utf-8")))
            out = {"ok": True}
        else:
            e = k.record(Theory(args.theory_dir), args.run_id, args.refuted_exactly, args.lesson)
            out = {"ok": True, "theory_id": e.theory_id, "verdict": e.verdict.value, "entries": len(k.entries)}
    except (KnowledgeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.research.knowledge import main as _main

    sys.exit(_main())
