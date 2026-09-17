"""The step token that binds the modules: no seal without clearance, no measurement without payment.

Why this module exists
----------------------
Each module of the harness refuses what it can see: an unsealed note, an
unknown card, an exhausted budget. None of them can see whether the *other*
checks ran. An orchestrator that calls ``seal`` and ``measure`` and skips the
budget, the library and the knowledge store gets a verdict all the same. The
sequence lived in the orchestrator's discipline, which is what this harness
exists not to rely on.

The passport closes that gap. It is an append-only chain of stamps kept
next to the theory, one per protocol step, each stamp an HMAC over the
theory id, the step, the note's digest, the stamp's data and the previous
stamp, keyed by a **run secret the agents never hold**. A stamp cannot be
forged without the secret, cannot be reordered, and cannot be re-issued:

    cleared  ─► sealed ─► consumed ─► measured
    (knowledge, library, budget checked on this note)
                (theory.seal requires cleared on the same digest)
                            (budget.consume requires sealed; the distance is the one cleared)
                                        (theory.record_measure requires consumed)

``clear`` runs the three pre-spending checks and stamps; ``consume`` pays the
trial with the distance that was cleared, not one declared afterwards, and
stamps; ``Theory.seal`` and ``Theory.record_measure`` require and stamp their
steps when given the passport; ``Knowledge.record`` requires ``measured``.

How to use it
-------------
In process::

    secret = new_secret(runs_root / ".harness_secret")     # once per lab, outside the run root
    passport = Passport(theory.dir / "passport.json", theory.dir.name, secret)
    clear(theory, knowledge, library, budget, distance="in_scope", passport=passport)
    ...auditor exchange, amendments (a change to the note needs clearing again)...
    theory.seal(passport)
    consume(theory, budget, passport)
    theory.record_measure(values, passport)
    knowledge.record(theory, run_id, ..., passport=passport)

From a shell (the secret file must be outside every path an agent can read)::

    python -m agent_harness.research.trial clear   --dir runs/r1/theories/t01 --secret-file .secret \\
        --distance in_scope --store knowledge --subject s --library library --budget-run-file runs/r1/budget.json
    python -m agent_harness.research.theory seal    --dir runs/r1/theories/t01 --secret-file .secret
    python -m agent_harness.research.trial consume --dir runs/r1/theories/t01 --secret-file .secret \\
        --budget-run-file runs/r1/budget.json
    python -m agent_harness.research.theory measure --dir runs/r1/theories/t01 --secret-file .secret --values v.json

What it does not do: it does not protect a run whose secret the agent can
read, and it does not make the measured values true. It makes the order of
the protocol a fact the harness can check, on the exact note that was sealed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_harness.budget import Budget, Trial, restore_run, save_run
from agent_harness.research.knowledge import Knowledge
from agent_harness.research.library import Library
from agent_harness.research.theory import Theory

__all__ = [
    "BadStamp",
    "Passport",
    "ProtocolError",
    "Refused",
    "STEPS",
    "StepMissing",
    "StepRepeated",
    "clear",
    "consume",
    "load_secret",
    "new_secret",
]

STEPS: tuple[str, ...] = ("cleared", "sealed", "consumed", "measured")
# What the previous stamp must be for each step. Clearing may repeat before the
# seal (an amended note is cleared again); nothing else repeats.
_PREVIOUS: dict[str, tuple[str | None, ...]] = {
    "cleared": (None, "cleared"),
    "sealed": ("cleared",),
    "consumed": ("sealed",),
    "measured": ("consumed",),
}


class ProtocolError(ValueError):
    """The step token refused. The message says which step is missing or forged."""


class StepMissing(ProtocolError):
    pass


class StepRepeated(ProtocolError):
    pass


class BadStamp(ProtocolError):
    """A stamp does not verify: the passport was edited, or signed with another secret."""


class Refused(ProtocolError):
    """A pre-spending check said no. ``cause`` names the module."""

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(f"{cause}: {detail}")
        self.cause = cause
        self.detail = detail


# ── Secrets ──────────────────────────────────────────────────────────────────

def new_secret(path: Path) -> bytes:
    """Create the run secret if absent (mode 0600), return it. Keep it outside the run root."""
    path = Path(path)
    if path.exists():
        return load_secret(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secret.hex().encode("ascii"))
    return secret


def load_secret(path: Path) -> bytes:
    text = Path(path).read_text(encoding="utf-8").strip()
    if len(text) < 32:
        raise ProtocolError(f"{path}: the secret is too short to be one")
    return bytes.fromhex(text) if all(c in "0123456789abcdef" for c in text) else text.encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── The passport ─────────────────────────────────────────────────────────────

@dataclass
class Passport:
    """The chain of stamps of one theory. Append-only; every read verifies the chain."""

    path: Path
    theory_id: str
    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.secret:
            raise ProtocolError("a passport needs a secret")

    # -- chain -----------------------------------------------------------------

    def _mac(self, step: str, digest: str, data: dict, prev: str) -> str:
        payload = "|".join((self.theory_id, step, digest,
                            json.dumps(data, sort_keys=True, separators=(",", ":")), prev))
        return hmac.new(self.secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def stamps(self) -> list[dict]:
        """Every stamp, verified. Raises BadStamp on the first one that does not."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        prev = ""
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                s = json.loads(line)
                expected = self._mac(s["step"], s["digest"], s.get("data", {}), prev)
            except (ValueError, KeyError, TypeError) as exc:
                raise BadStamp(f"{self.path}: stamp {n} is unreadable: {exc}") from exc
            if s.get("theory") != self.theory_id or not hmac.compare_digest(expected, s.get("mac", "")):
                raise BadStamp(f"{self.path}: stamp {n} ({s.get('step')}) does not verify: "
                               "edited, out of order, or signed with another secret")
            out.append(s)
            prev = s["mac"]
        return out

    def last(self) -> dict | None:
        stamps = self.stamps()
        return stamps[-1] if stamps else None

    def stamp(self, step: str, digest: str, **data) -> dict:
        """Append one stamp, in order. Refuses a repeat or a skipped step."""
        if step not in STEPS:
            raise ProtocolError(f"unknown step {step!r}; steps: {STEPS}")
        stamps = self.stamps()
        last = stamps[-1] if stamps else None
        prev_step = last["step"] if last else None
        if prev_step not in _PREVIOUS[step]:
            if any(s["step"] == step for s in stamps) and step != "cleared":
                raise StepRepeated(f"{self.theory_id}: step {step!r} was already stamped; it does not repeat")
            raise StepMissing(f"{self.theory_id}: step {step!r} needs {_PREVIOUS[step][-1]!r} before it, "
                              f"the last stamp is {prev_step!r}")
        if last and step != "cleared" and last["digest"] != digest:
            raise StepMissing(f"{self.theory_id}: the note changed since step {prev_step!r} "
                              f"({last['digest'][:12]}… → {digest[:12]}…); clear it again")
        entry = {"theory": self.theory_id, "step": step, "digest": digest,
                 "data": dict(data), "at": _now()}
        entry["mac"] = self._mac(step, digest, entry["data"], last["mac"] if last else "")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def require(self, step: str, digest: str | None = None) -> dict:
        """The last stamp must be ``step``, on ``digest`` when given. Returns it."""
        last = self.last()
        if last is None or last["step"] != step:
            raise StepMissing(f"{self.theory_id}: step {step!r} is required, the last stamp is "
                              f"{last['step'] if last else None!r}")
        if digest is not None and last["digest"] != digest:
            raise StepMissing(f"{self.theory_id}: step {step!r} was stamped on another version of the "
                              f"note ({last['digest'][:12]}… vs {digest[:12]}…)")
        return last

    def find(self, step: str) -> dict | None:
        """The most recent stamp of ``step``, verified, or None."""
        for s in reversed(self.stamps()):
            if s["step"] == step:
                return s
        return None


# ── The two steps that belong to no single module ──────────────────────────────

def clear(theory: Theory, knowledge: Knowledge, library: Library, budget: Budget,
          distance: str, passport: Passport) -> dict:
    """Knowledge, library and budget say yes to this note, as it is now. Stamps ``cleared``."""
    note = theory.note()
    try:
        knowledge.check_note(note)
    except ValueError as exc:
        raise Refused("knowledge", str(exc)) from exc
    try:
        report = library.require_citations(note.cites)
    except ValueError as exc:
        raise Refused("library", str(exc)) from exc
    if report.needs_extension and not note.caveats.strip():
        raise Refused("library", f"card(s) {list(report.needs_extension)} need a declared "
                                 "approximation in the note's caveats")
    ok, why = budget.check(distance)
    if not ok:
        raise Refused("budget", why)
    return passport.stamp("cleared", note.digest(), distance=distance, track=note.track,
                          cites=list(note.cites), needs_extension=list(report.needs_extension))


def consume(theory: Theory, budget: Budget, passport: Passport,
            accepted: bool | None = None, note: str = "") -> Trial:
    """Pay the trial with the distance that was cleared. Requires ``sealed``, stamps ``consumed``."""
    digest = theory.seal_digest()
    if digest is None:
        raise StepMissing(f"{theory.dir.name}: not sealed")
    passport.require("sealed", digest)
    cleared = passport.find("cleared")
    if cleared is None or cleared["digest"] != digest:
        raise StepMissing(f"{theory.dir.name}: no clearance on the sealed note")
    distance = cleared["data"]["distance"]
    trial = budget.consume(theory.dir.name, distance, track=cleared["data"].get("track"),
                           accepted=accepted, note=note)
    passport.stamp("consumed", digest, distance=distance, cost=trial.cost, n=trial.n)
    return trial


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.research.trial",
        description="Clear a note before spending, pay the trial after the seal. JSON out; exit 2 on refusal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", required=True, type=Path, help="the theory directory")
    common.add_argument("--secret-file", required=True, type=Path)
    common.add_argument("--passport", type=Path, default=None, help="default: <dir>/passport.json")
    common.add_argument("--budget-run-file", required=True, type=Path)

    c = sub.add_parser("clear", parents=[common])
    c.add_argument("--distance", required=True)
    c.add_argument("--store", required=True, type=Path, help="the knowledge store")
    c.add_argument("--subject", required=True)
    c.add_argument("--library", required=True, type=Path)

    k = sub.add_parser("consume", parents=[common])
    k.add_argument("--accepted", default=None, choices=("true", "false"))
    k.add_argument("--note", default="")

    s = sub.add_parser("status", parents=[common])

    args = parser.parse_args(argv)
    try:
        theory = Theory(args.dir)
        passport = Passport(args.passport or args.dir / "passport.json", args.dir.name,
                            load_secret(args.secret_file))
        budget, store = restore_run(args.budget_run_file)
        if args.cmd == "clear":
            stamp = clear(theory, Knowledge(args.store, args.subject), Library(args.library),
                          budget, args.distance, passport)
            out = {"ok": True, "step": "cleared", "digest": stamp["digest"], "data": stamp["data"]}
        elif args.cmd == "consume":
            accepted = None if args.accepted is None else args.accepted == "true"
            trial = consume(theory, budget, passport, accepted, args.note)
            save_run(budget, args.budget_run_file, store)
            out = {"ok": True, "step": "consumed", "cost": trial.cost, "remaining": budget.remaining(),
                   "summary": budget.summary()}
        else:
            stamps = passport.stamps()
            out = {"ok": True, "steps": [s["step"] for s in stamps],
                   "last": stamps[-1]["step"] if stamps else None}
    except (ProtocolError, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.research.trial import main as _main

    sys.exit(_main())
