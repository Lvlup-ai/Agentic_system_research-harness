"""The deliverable of the research phase, under contract ``theory-report/1``.

One item per measured theory, a summary the rows must support, and the
in-sample reminder. The reviewer reads it through the contract loader; a
report whose summary contradicts its items never reaches the reviewer.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from agent_harness.contracts import Deliverable, Registry
from agent_harness.research.theory import Verdict

CONTRACT = "theory-report/1"


class TheoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    track: str
    verdict: Verdict
    digest: str
    cites: tuple[str, ...] = ()


class TheorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    confirmed: int
    refuted: int
    undecided: int


class TheoryReport(Deliverable):
    items: tuple[TheoryItem, ...]
    summary: TheorySummary
    in_sample: bool = True

    @classmethod
    def build(cls, items: Sequence[TheoryItem]) -> "TheoryReport":
        n = {v: sum(1 for i in items if i.verdict is v) for v in Verdict}
        return cls(contract=CONTRACT, items=tuple(items),
                   summary=TheorySummary(count=len(items), confirmed=n[Verdict.CONFIRMED],
                                         refuted=n[Verdict.REFUTED], undecided=n[Verdict.UNDECIDED]))


def _counts_match(d: Deliverable) -> str | None:
    assert isinstance(d, TheoryReport)
    n = {v: sum(1 for i in d.items if i.verdict is v) for v in Verdict}
    expected = (len(d.items), n[Verdict.CONFIRMED], n[Verdict.REFUTED], n[Verdict.UNDECIDED])
    got = (d.summary.count, d.summary.confirmed, d.summary.refuted, d.summary.undecided)
    return None if expected == got else f"summary {got} does not match the items {expected}"


def _in_sample_is_said(d: Deliverable) -> str | None:
    assert isinstance(d, TheoryReport)
    return None if d.in_sample else "a report must say its numbers are in-sample"


def registry() -> Registry:
    r = Registry()
    r.register(CONTRACT, TheoryReport, checks=[_counts_match, _in_sample_is_said])
    return r
