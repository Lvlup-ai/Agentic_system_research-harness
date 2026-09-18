"""The run journal: every move of the orchestrator, written by the harness, reviewable at the end.

Why this module exists
----------------------
The orchestrator is meant to have latitude: it declares distances, abandons
iterations, decides what to do with a disagreement, chooses what to measure.
That latitude is only acceptable if every one of those moves can be reviewed
afterwards. Each module of the harness keeps its own files, so what happened
to a theory or at a boundary can be reconstructed; what the orchestrator did,
in what order, and why, could not. This module is that record.

Three principles:

1. **The harness writes the lines.** Every module emits a line when it acts
   (a directive, a guard result, a refusal with the module that said no, a
   stamp, a measurement, a decision, a knowledge entry). The orchestrator
   does not have to remember to log: going through the harness is enough.
2. **The orchestrator writes its reasons.** ``note`` records free text from a
   named actor, attached to the theory or boundary it concerns.
3. **The journal is readable and checkable.** ``render`` produces the
   chronology in Markdown for the end-of-run review; ``verify`` checks the
   chain: every line carries the hash of the previous one, so an edited or
   removed line shows.

The journal does not prevent anything. It makes everything visible.

How to use it
-------------
Activate a journal once; every module then emits into it::

    from agent_harness import journal
    journal.activate(run_root / "journal.jsonl")
    ...harness calls...
    journal.note("orchestrator", "off-topic theory declined: the age track is not served yet", subject="t03")
    journal.deactivate()

    j = journal.Journal(run_root / "journal.jsonl")
    j.verify()                 # the number of intact lines, or JournalBroken
    (run_root / "journal.md").write_text(j.render())

From a shell, pass ``--journal PATH`` to any CLI, or set
``AGENT_HARNESS_JOURNAL=PATH`` once; then::

    python -m agent_harness.journal note --journal runs/r1/journal.jsonl --actor orchestrator \\
        --subject t03 --text "declined: the age track is not served yet"
    python -m agent_harness.journal render --journal runs/r1/journal.jsonl > runs/r1/journal.md
    python -m agent_harness.journal verify --journal runs/r1/journal.jsonl

Line format (one JSON object per line)::

    {"n": 7, "at": "2026-01-01T10:00:00Z", "actor": "harness", "action": "trial.refused",
     "subject": "t03", "data": {"cause": "budget", "detail": "..."},
     "prev": "<hash of line 6>", "hash": "<sha256 of this line without hash>"}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "ENV_VAR",
    "Journal",
    "JournalBroken",
    "activate",
    "active",
    "deactivate",
    "emit",
    "note",
    "resolve",
    "using",
]

ENV_VAR = "AGENT_HARNESS_JOURNAL"
_ACTIVE: list["Journal"] = []


class JournalBroken(ValueError):
    """A line does not chain to the previous one: the journal was edited or cut."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonable(v: Any) -> Any:
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, (set, frozenset, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, Mapping):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if hasattr(v, "value") and isinstance(getattr(v, "value"), str):   # enums
        return v.value
    return v


def _canonical(entry: Mapping) -> str:
    return json.dumps({k: v for k, v in entry.items() if k != "hash"},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Journal:
    """One run's journal file. Append-only; every read verifies the chain."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # -- writing -------------------------------------------------------------

    def record(self, actor: str, action: str, subject: str | None = None, **data) -> dict:
        """Append one line. Returns it."""
        if not actor.strip() or not action.strip():
            raise ValueError("a journal line needs an actor and an action")
        last = self._last()
        entry = {
            "n": (last["n"] + 1) if last else 1,
            "at": _now(),
            "actor": actor,
            "action": action,
            "subject": subject,
            "data": _jsonable(data),
            "prev": last["hash"] if last else "",
        }
        entry["hash"] = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    def note(self, actor: str, text: str, subject: str | None = None, **data) -> dict:
        """The orchestrator's (or an agent's) reasoning, in its own words."""
        if not text.strip():
            raise ValueError("a note needs a text")
        return self.record(actor, "note", subject, text=text, **data)

    # -- reading -------------------------------------------------------------

    def _last(self) -> dict | None:
        if not self.path.exists():
            return None
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)
        return last

    def entries(self) -> list[dict]:
        """Every line, chain verified. Raises JournalBroken on the first bad link."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        prev_hash, expected_n = "", 1
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                ok = (e["n"] == expected_n and e["prev"] == prev_hash
                      and e["hash"] == hashlib.sha256(_canonical(e).encode("utf-8")).hexdigest())
            except (ValueError, KeyError, TypeError) as exc:
                raise JournalBroken(f"{self.path}: line {lineno} is unreadable: {exc}") from exc
            if not ok:
                raise JournalBroken(f"{self.path}: line {lineno} (n={e.get('n')}, {e.get('action')}) "
                                    "does not chain to the previous one: edited, removed or reordered")
            out.append(e)
            prev_hash, expected_n = e["hash"], e["n"] + 1
        return out

    def verify(self) -> int:
        """The number of intact lines. Raises JournalBroken otherwise."""
        return len(self.entries())

    def by_subject(self, subject: str) -> list[dict]:
        return [e for e in self.entries() if e.get("subject") == subject]

    def notes(self) -> list[dict]:
        return [e for e in self.entries() if e["action"] == "note"]

    # -- rendering -----------------------------------------------------------

    def render(self) -> str:
        """The chronology, in Markdown, for the end-of-run review."""
        entries = self.entries()
        lines = ["# Run journal", "",
                 f"> {len(entries)} lines, chain intact. Every line was written by the harness when it "
                 "acted, or by an actor as a note. Nothing here prevented anything; everything here "
                 "can be reviewed.", ""]
        if not entries:
            return "\n".join(lines + ["Empty.", ""])
        actors = sorted({e["actor"] for e in entries})
        subjects = [s for s in dict.fromkeys(e.get("subject") for e in entries) if s]
        lines += [f"Actors: {', '.join(actors)}. Subjects: {', '.join(subjects) or 'none'}.", "",
                  "## Chronology", "", "| # | At (UTC) | Actor | Action | Subject | What |", "|---|---|---|---|---|---|"]
        for e in entries:
            lines.append(f"| {e['n']} | {e['at'][11:19]} | {e['actor']} | `{e['action']}` | "
                         f"{e.get('subject') or ''} | {_summary(e)} |")
        lines.append("")
        for s in subjects:
            own = [e for e in entries if e.get("subject") == s]
            lines += [f"## {s}", ""]
            for e in own:
                mark = "**note**" if e["action"] == "note" else f"`{e['action']}`"
                lines.append(f"- {e['n']} · {e['actor']} · {mark} — {_summary(e)}")
            lines.append("")
        notes = [e for e in entries if e["action"] == "note" and not e.get("subject")]
        if notes:
            lines += ["## Notes on the run", ""]
            lines += [f"- {e['n']} · {e['actor']}: {e['data'].get('text', '')}" for e in notes]
            lines.append("")
        return "\n".join(lines)


def _summary(e: Mapping) -> str:
    data = e.get("data") or {}
    if e["action"] == "note":
        return str(data.get("text", "")).replace("|", "\\|")
    parts = []
    for k, v in data.items():
        if isinstance(v, (dict, list)) and len(json.dumps(v)) > 80:
            v = f"{type(v).__name__}({len(v)})"
        parts.append(f"{k}={json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}")
    return ", ".join(parts).replace("|", "\\|")[:300]


# ── The active journal ───────────────────────────────────────────────────────
#
# Modules call `emit()`; it is a no-op when no journal is active, so every
# module works alone, and writes its lines as soon as one is activated.

def activate(path: Path | str) -> Journal:
    j = Journal(Path(path))
    _ACTIVE.append(j)
    return j


def deactivate() -> None:
    if _ACTIVE:
        _ACTIVE.pop()


def active() -> Journal | None:
    return _ACTIVE[-1] if _ACTIVE else None


@contextmanager
def using(path: Path | str) -> Iterator[Journal]:
    j = activate(path)
    try:
        yield j
    finally:
        deactivate()


def emit(action: str, subject: str | None = None, actor: str = "harness", **data) -> dict | None:
    """Write a line into the active journal, if any."""
    j = active()
    if j is None:
        return None
    return j.record(actor, action, subject, **data)


def note(actor: str, text: str, subject: str | None = None, **data) -> dict | None:
    j = active()
    if j is None:
        return None
    return j.note(actor, text, subject, **data)


def resolve(explicit: Path | str | None) -> Journal | None:
    """The journal a CLI should write to: ``--journal``, else the environment, else none."""
    path = explicit or os.environ.get(ENV_VAR)
    return Journal(Path(path)) if path else None


def add_journal_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--journal", type=Path, default=None,
                        help=f"append this command's lines to a run journal (default: ${ENV_VAR})")


def activate_from_args(args: argparse.Namespace) -> Journal | None:
    """For CLIs: activate the journal named by ``--journal`` or the environment."""
    j = resolve(getattr(args, "journal", None))
    if j is not None:
        _ACTIVE.append(j)
    return j


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.journal",
        description="Write a note into a run journal, render it, or verify its chain.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--journal", type=Path, default=None, help=f"default: ${ENV_VAR}")

    n = sub.add_parser("note", parents=[common])
    n.add_argument("--actor", required=True)
    n.add_argument("--text", required=True)
    n.add_argument("--subject", default=None)
    sub.add_parser("render", parents=[common])
    sub.add_parser("verify", parents=[common])
    sub.add_parser("tail", parents=[common]).add_argument("--n", type=int, default=10)

    args = parser.parse_args(argv)
    j = resolve(args.journal)
    if j is None:
        print(json.dumps({"ok": False, "error": "NoJournal",
                          "detail": f"pass --journal PATH or set {ENV_VAR}"}))
        return 2
    try:
        if args.cmd == "note":
            e = j.note(args.actor, args.text, args.subject)
            print(json.dumps({"ok": True, "n": e["n"]}))
        elif args.cmd == "render":
            print(j.render())
        elif args.cmd == "verify":
            print(json.dumps({"ok": True, "lines": j.verify()}))
        else:
            for e in j.entries()[-args.n:]:
                print(json.dumps(e, ensure_ascii=False))
    except (JournalBroken, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.journal import main as _main

    sys.exit(_main())
