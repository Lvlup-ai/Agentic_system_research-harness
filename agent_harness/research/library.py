"""Closed knowledge: the cards a researcher may reason with, each with a status.

Why this module exists
----------------------
A researcher agent given free rein will reach for any concept it knows, some
of which the data cannot support and some of which are simply invented. A
research lab gives its researcher a library of cards instead: each card is
one concept, and carries a status that says what the data allows, usable as
is, usable with an approximation the researcher must declare, or out of
reach. Two failures are then still possible, and both are closed here:

* a brief or an index that announces the library's contents from memory goes
  wrong the day a card is added: the index is **generated** from the cards,
  never written by hand, and the counts are read from it;
* a card without a status is invisible to every count, hence to the agent: a
  card whose status is missing or unknown is **refused at load time**.

A theory note cites cards. ``check_citations`` refuses an unknown card and an
out-of-data card before any trial is spent, and names the cards that require
a declared approximation.

Card format: a Markdown file with a YAML front matter::

    ---
    title: Form validation regression
    status: usable
    requires: [signup_date, email]
    ---
    Why malformed values would cluster after a deployment...
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import yaml

__all__ = [
    "Card",
    "CardError",
    "CitationRefused",
    "CitationReport",
    "Library",
    "Status",
]


class Status(str, Enum):
    USABLE = "usable"                  # the data supports it as is
    NEEDS_EXTENSION = "needs_extension"  # usable with an approximation the note must declare
    OUT_OF_DATA = "out_of_data"        # the data cannot support it: not citable


class CardError(ValueError):
    """A card is malformed: missing front matter, missing or unknown status."""


class CitationRefused(ValueError):
    """A theory cites what it may not: an unknown card or an out-of-data card."""


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    status: Status
    requires: tuple[str, ...] = ()
    body: str = ""
    history: tuple[dict, ...] = ()   # status changes: {at, from, to, reason}
    path: Path | None = None


def _split_front_matter(text: str, origin: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise CardError(f"{origin}: no YAML front matter (file must start with '---')")
    end = text.find("\n---", 3)
    if end < 0:
        raise CardError(f"{origin}: front matter is not closed")
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as exc:
        raise CardError(f"{origin}: invalid YAML front matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise CardError(f"{origin}: front matter must be a mapping")
    body = text[end + 4:].lstrip("-").lstrip("\n")
    return meta, body


def _render(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + body


def parse_card(path: Path) -> Card:
    meta, body = _split_front_matter(path.read_text(encoding="utf-8"), path.name)
    raw_status = meta.get("status")
    if raw_status is None:
        raise CardError(f"{path.name}: no status; a card without a status is invisible to every count")
    try:
        status = Status(str(raw_status))
    except ValueError as exc:
        raise CardError(f"{path.name}: unknown status {raw_status!r}; "
                        f"allowed: {[s.value for s in Status]}") from exc
    requires = meta.get("requires") or []
    if not isinstance(requires, list):
        raise CardError(f"{path.name}: `requires` must be a list")
    history = meta.get("history") or []
    return Card(id=path.stem, title=str(meta.get("title") or path.stem), status=status,
                requires=tuple(str(r) for r in requires), body=body,
                history=tuple(dict(h) for h in history), path=path)


@dataclass(frozen=True)
class CitationReport:
    unknown: tuple[str, ...] = ()
    out_of_data: tuple[str, ...] = ()
    needs_extension: tuple[str, ...] = ()
    usable: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.unknown or self.out_of_data)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "unknown": list(self.unknown), "out_of_data": list(self.out_of_data),
                "needs_extension": list(self.needs_extension), "usable": list(self.usable)}


@dataclass
class Library:
    """A directory of cards. The index is derived from them, never edited."""

    directory: Path
    index_name: str = "INDEX.md"
    _cards: dict[str, Card] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.reload()

    def reload(self) -> None:
        self._cards = {}
        for p in sorted(self.directory.glob("*.md")):
            if p.name == self.index_name:
                continue
            card = parse_card(p)          # a malformed card fails the whole load, loudly
            self._cards[card.id] = card

    # -- reading -------------------------------------------------------------

    @property
    def cards(self) -> tuple[Card, ...]:
        return tuple(self._cards.values())

    def card(self, card_id: str) -> Card:
        try:
            return self._cards[card_id]
        except KeyError:
            raise CitationRefused(f"unknown card {card_id!r}") from None

    def by_status(self, status: Status) -> tuple[Card, ...]:
        return tuple(c for c in self.cards if c.status is status)

    def counts(self) -> dict[str, int]:
        return {s.value: len(self.by_status(s)) for s in Status}

    def citable(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.cards if c.status is not Status.OUT_OF_DATA)

    # -- citations -----------------------------------------------------------

    def check_citations(self, cites: Iterable[str]) -> CitationReport:
        unknown, out, ext, ok = [], [], [], []
        for c in cites:
            card = self._cards.get(c)
            if card is None:
                unknown.append(c)
            elif card.status is Status.OUT_OF_DATA:
                out.append(c)
            elif card.status is Status.NEEDS_EXTENSION:
                ext.append(c)
            else:
                ok.append(c)
        return CitationReport(tuple(unknown), tuple(out), tuple(ext), tuple(ok))

    def require_citations(self, cites: Iterable[str]) -> CitationReport:
        """Refuse before any trial is spent. Returns the report when it passes."""
        report = self.check_citations(cites)
        if not report.ok:
            parts = []
            if report.unknown:
                parts.append(f"unknown card(s) {list(report.unknown)}: a theory may only rest on the library")
            if report.out_of_data:
                parts.append(f"out-of-data card(s) {list(report.out_of_data)}: the data cannot support them")
            raise CitationRefused("; ".join(parts))
        return report

    # -- writing (status changes are the library's memory) ---------------------

    def set_status(self, card_id: str, status: Status, reason: str) -> Card:
        """Change a card's status and record why. The card keeps its own history."""
        card = self.card(card_id)
        if not reason.strip():
            raise CardError("a status change needs a reason: it is what the next run will read")
        if card.status is status:
            return card
        assert card.path is not None
        meta, body = _split_front_matter(card.path.read_text(encoding="utf-8"), card.path.name)
        meta["status"] = status.value
        meta.setdefault("history", []).append({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from": card.status.value, "to": status.value, "reason": reason})
        card.path.write_text(_render(meta, body), encoding="utf-8")
        self.reload()
        return self.card(card_id)

    # -- the index -------------------------------------------------------------

    def index_text(self) -> str:
        counts = self.counts()
        lines = ["# Library index", "",
                 "> Generated from the cards. Do not edit: run "
                 "`python -m agent_harness.research.library index`.", "",
                 f"{len(self.cards)} cards: " + ", ".join(f"{n} {s}" for s, n in counts.items()), ""]
        labels = {Status.USABLE: "Usable as is",
                  Status.NEEDS_EXTENSION: "Usable with a declared approximation",
                  Status.OUT_OF_DATA: "Out of reach of the data (not citable)"}
        for status in Status:
            cards = self.by_status(status)
            lines += [f"## {labels[status]} ({len(cards)})", ""]
            for c in cards:
                req = f" — requires {', '.join(c.requires)}" if c.requires else ""
                lines.append(f"- `{c.id}` — {c.title}{req}")
            lines.append("")
        return "\n".join(lines)

    def write_index(self) -> Path:
        path = self.directory / self.index_name
        path.write_text(self.index_text(), encoding="utf-8")
        return path

    def index_is_current(self) -> bool:
        path = self.directory / self.index_name
        return path.exists() and path.read_text(encoding="utf-8") == self.index_text()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_harness.research.library",
        description="Index, count and check citations against a library of cards. JSON out; exit 2 on refusal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", required=True, type=Path)

    sub.add_parser("index", parents=[common], help="regenerate the index")
    sub.add_parser("counts", parents=[common])
    sub.add_parser("verify-index", parents=[common], help="exit 1 if the index is stale")
    chk = sub.add_parser("check", parents=[common]); chk.add_argument("--cite", action="append", default=[])
    st = sub.add_parser("set-status", parents=[common])
    st.add_argument("--card", required=True)
    st.add_argument("--status", required=True, choices=[s.value for s in Status])
    st.add_argument("--reason", required=True)

    args = parser.parse_args(argv)
    try:
        lib = Library(args.dir)
        if args.cmd == "index":
            out = {"ok": True, "index": str(lib.write_index()), "counts": lib.counts()}
        elif args.cmd == "counts":
            out = {"ok": True, "counts": lib.counts(), "cards": [c.id for c in lib.cards]}
        elif args.cmd == "verify-index":
            current = lib.index_is_current()
            print(json.dumps({"ok": current, "current": current}))
            return 0 if current else 1
        elif args.cmd == "check":
            report = lib.require_citations(args.cite)
            out = {**report.to_dict()}
        else:
            card = lib.set_status(args.card, Status(args.status), args.reason)
            out = {"ok": True, "card": card.id, "status": card.status.value, "history": list(card.history)}
    except (CardError, CitationRefused) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    from agent_harness.research.library import main as _main

    sys.exit(_main())
