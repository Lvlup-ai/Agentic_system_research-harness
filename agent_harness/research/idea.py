"""The idea: the human starting point, frozen during a run, rewritten on the verdicts.

Why this module exists
----------------------
The human input to a research run is not a thesis. It is an idea: a supposed
mechanism, why it should exist, orders of magnitude, and the tracks worth
looking at. No thresholds, no features, no fine mechanics; those are the
researcher's work. Two things must hold for the loop to be honest:

* the idea **does not move during a run**. The researcher may drift away
  from it (that costs budget), but the reference itself is frozen: a digest
  is taken at the start and verified at the end;
* at the end, the idea is **rewritten on the verdicts**, by the harness,
  from the knowledge store: every claim in the rewrite carries the theory and
  the verdict that founds it, or the statement that nothing was established.
  A model may add prose around it; it cannot put a claim in it.

File format (Markdown with a YAML front matter)::

    ---
    title: Malformed rows come from a form that lost its validation
    tracks: [email, signup_date, age]
    orders_of_magnitude:
      malformed_share: "a few percent of rows"
    ---
    The signup form is the only entry point... (the mechanism, and why)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_harness import journal
from agent_harness.research.knowledge import Knowledge
from agent_harness.research.theory import Verdict

__all__ = ["Idea", "IdeaError", "IdeaMoved"]


class IdeaError(ValueError):
    """The idea file is malformed: no front matter, no tracks, no body."""


class IdeaMoved(IdeaError):
    """The idea changed after the run started."""


def _split(text: str, origin: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise IdeaError(f"{origin}: no YAML front matter")
    end = text.find("\n---", 3)
    if end < 0:
        raise IdeaError(f"{origin}: front matter is not closed")
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as exc:
        raise IdeaError(f"{origin}: invalid YAML front matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise IdeaError(f"{origin}: front matter must be a mapping")
    return meta, text[end + 4:].lstrip("-").lstrip("\n")


@dataclass(frozen=True)
class Idea:
    path: Path
    title: str
    tracks: tuple[str, ...]
    orders_of_magnitude: dict[str, str]
    body: str
    text: str

    @classmethod
    def load(cls, path: Path) -> "Idea":
        path = Path(path)
        if not path.exists():
            raise IdeaError(f"idea not found: {path}")
        text = path.read_text(encoding="utf-8")
        meta, body = _split(text, path.name)
        tracks = meta.get("tracks")
        if not isinstance(tracks, list) or not tracks or not all(isinstance(t, str) and t.strip() for t in tracks):
            raise IdeaError(f"{path.name}: `tracks` must be a non-empty list of names; "
                            "they are what the budget's in-scope minimum counts on")
        if len(set(tracks)) != len(tracks):
            raise IdeaError(f"{path.name}: duplicate track names")
        if len(body.strip()) < 40:
            raise IdeaError(f"{path.name}: the body must say the supposed mechanism and why it should exist")
        oom = meta.get("orders_of_magnitude") or {}
        if not isinstance(oom, dict):
            raise IdeaError(f"{path.name}: `orders_of_magnitude` must be a mapping")
        return cls(path=path, title=str(meta.get("title") or path.stem),
                   tracks=tuple(str(t) for t in tracks),
                   orders_of_magnitude={str(k): str(v) for k, v in oom.items()},
                   body=body, text=text)

    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    # -- frozen during the run -------------------------------------------------

    def freeze(self, run_root: Path) -> Path:
        """Record the digest in the run root. Idempotent for the same content."""
        run_root = Path(run_root)
        run_root.mkdir(parents=True, exist_ok=True)
        marker = run_root / "idea.frozen.json"
        if marker.exists():
            self.verify(run_root)
            return marker
        marker.write_text(json.dumps({
            "path": str(self.path), "title": self.title, "tracks": list(self.tracks),
            "digest": self.digest(),
            "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        journal.emit("idea.frozen", "run", title=self.title, tracks=list(self.tracks), digest=self.digest())
        return marker

    def verify(self, run_root: Path) -> str:
        marker = Path(run_root) / "idea.frozen.json"
        if not marker.exists():
            raise IdeaError(f"the idea was never frozen for this run ({marker} missing)")
        frozen = json.loads(marker.read_text(encoding="utf-8"))["digest"]
        if frozen != self.digest():
            journal.emit("idea.moved", "run", frozen=frozen, now=self.digest())
            raise IdeaMoved(
                f"the idea changed after the run started (frozen {frozen[:12]}…, now "
                f"{self.digest()[:12]}…): a reference that moves cannot be drifted from")
        return frozen

    # -- rewritten on the verdicts -----------------------------------------------

    def rewrite(self, knowledge: Knowledge, run_id: str) -> str:
        """The idea as the verdicts leave it. Generated; every claim carries its source."""
        lines = [f"# {self.title} — rewritten on {len(knowledge.entries)} measured theories", "",
                 f"> Generated from the knowledge store of subject `{knowledge.subject}` after run "
                 f"`{run_id}`. Every statement below carries the theory and the verdict that founds "
                 "it. All numbers are in-sample: nothing was held out.", ""]
        for track in self.tracks:
            entries = knowledge.on_track(track)
            lines.append(f"## Track `{track}`")
            lines.append("")
            if not entries:
                lines += ["Nothing established: no theory was measured on this track.", ""]
                continue
            for e in entries:
                lines.append(f"- **{e.verdict.value}** — {e.theory_id} ({e.run_id}): {e.prediction}")
                for d in e.decisions:
                    lines.append(f"  - `{d['metric']}` = {d['value']} → {d['decision']}")
                if e.refuted_exactly:
                    lines.append(f"  - refuted exactly: {e.refuted_exactly}")
                if e.cites:
                    lines.append(f"  - rests on: {', '.join(f'`{c}`' for c in e.cites)}")
            lines.append("")
        extra = sorted({e.track for e in knowledge.entries} - set(self.tracks))
        if extra:
            lines += ["## Off the idea's tracks", ""]
            for track in extra:
                for e in knowledge.on_track(track):
                    lines.append(f"- **{e.verdict.value}** — {e.theory_id} ({e.run_id}, `{track}`): {e.prediction}")
            lines.append("")
        lessons = knowledge.lessons()
        lines += ["## What no run must pay for again", ""]
        lines += [f"- {l}" for l in lessons] or ["- Nothing yet."]
        lines.append("")
        counts = {v.value: sum(1 for e in knowledge.entries if e.verdict is v) for v in Verdict}
        lines += ["## Count", "",
                  ", ".join(f"{n} {v}" for v, n in counts.items()) + ".", "",
                  "## The idea as deposited (unchanged)", "", self.body.rstrip(), ""]
        return "\n".join(lines)

    def write_rewrite(self, knowledge: Knowledge, run_id: str, out: Path) -> Path:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.rewrite(knowledge, run_id), encoding="utf-8")
        journal.emit("idea.rewritten", "run", out=out, theories=len(knowledge.entries), run_id=run_id)
        return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.research.idea",
        description="Load, freeze, verify and rewrite the human idea. JSON out; exit 2 on refusal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--idea", required=True, type=Path)
    journal.add_journal_argument(common)

    sub.add_parser("load", parents=[common])
    f = sub.add_parser("freeze", parents=[common]); f.add_argument("--run-root", required=True, type=Path)
    v = sub.add_parser("verify", parents=[common]); v.add_argument("--run-root", required=True, type=Path)
    r = sub.add_parser("rewrite", parents=[common])
    r.add_argument("--store", required=True, type=Path)
    r.add_argument("--subject", required=True)
    r.add_argument("--run-id", required=True)
    r.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    journal.activate_from_args(args)
    try:
        idea = Idea.load(args.idea)
        if args.cmd == "load":
            out = {"ok": True, "title": idea.title, "tracks": list(idea.tracks), "digest": idea.digest()}
        elif args.cmd == "freeze":
            out = {"ok": True, "marker": str(idea.freeze(args.run_root)), "digest": idea.digest()}
        elif args.cmd == "verify":
            out = {"ok": True, "digest": idea.verify(args.run_root)}
        else:
            path = idea.write_rewrite(Knowledge(args.store, args.subject), args.run_id, args.out)
            out = {"ok": True, "out": str(path)}
    except IdeaError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.research.idea import main as _main

    sys.exit(_main())
