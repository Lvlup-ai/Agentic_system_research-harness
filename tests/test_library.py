"""The library is closed: every card has a status, the index is generated, citations are checked."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.research.library import (
    CardError,
    CitationRefused,
    Library,
    Status,
    parse_card,
)


def card(dir_: Path, name: str, status: str, requires: list[str] | None = None, body: str = "Why.\n") -> Path:
    p = dir_ / f"{name}.md"
    req = f"requires: [{', '.join(requires)}]\n" if requires else ""
    p.write_text(f"---\ntitle: {name.replace('_', ' ').title()}\nstatus: {status}\n{req}---\n{body}")
    return p


@pytest.fixture
def lib_dir(tmp_path: Path) -> Path:
    card(tmp_path, "form_validation_regression", "usable", ["signup_date", "email"])
    card(tmp_path, "default_value_leak", "usable", ["age"])
    card(tmp_path, "unit_mismatch", "needs_extension", ["score"])
    card(tmp_path, "duplicate_import", "out_of_data")
    return tmp_path


# ── Cards ────────────────────────────────────────────────────────────────────

def test_a_card_is_parsed_with_its_status_and_requirements(lib_dir: Path) -> None:
    c = parse_card(lib_dir / "form_validation_regression.md")
    assert c.id == "form_validation_regression"
    assert c.status is Status.USABLE
    assert c.requires == ("signup_date", "email")
    assert c.body == "Why.\n"


def test_a_card_without_status_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("---\ntitle: X\n---\nbody\n")
    with pytest.raises(CardError, match="no status"):
        parse_card(tmp_path / "x.md")


def test_a_card_with_an_unknown_status_is_refused(tmp_path: Path) -> None:
    card(tmp_path, "x", "maybe")
    with pytest.raises(CardError, match="unknown status 'maybe'"):
        parse_card(tmp_path / "x.md")


def test_a_card_without_front_matter_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("# Just a title\n")
    with pytest.raises(CardError, match="no YAML front matter"):
        parse_card(tmp_path / "x.md")


def test_one_malformed_card_fails_the_whole_library(lib_dir: Path) -> None:
    (lib_dir / "broken.md").write_text("---\ntitle: Broken\n---\n")
    with pytest.raises(CardError, match="broken.md"):
        Library(lib_dir)


# ── Counts and index ─────────────────────────────────────────────────────────

def test_counts_are_read_from_the_cards(lib_dir: Path) -> None:
    lib = Library(lib_dir)
    assert lib.counts() == {"usable": 2, "needs_extension": 1, "out_of_data": 1}
    assert lib.citable() == ("default_value_leak", "form_validation_regression", "unit_mismatch")


def test_the_index_is_generated_and_detects_staleness(lib_dir: Path) -> None:
    lib = Library(lib_dir)
    assert not lib.index_is_current()
    path = lib.write_index()
    text = path.read_text()
    assert "4 cards: 2 usable, 1 needs_extension, 1 out_of_data" in text
    assert "## Out of reach of the data (not citable) (1)" in text
    assert "`unit_mismatch` — Unit Mismatch — requires score" in text
    assert lib.index_is_current()
    card(lib_dir, "new_card", "usable")
    lib.reload()
    assert not lib.index_is_current(), "a new card makes the index stale"


def test_the_index_file_is_not_a_card(lib_dir: Path) -> None:
    lib = Library(lib_dir)
    lib.write_index()
    assert "INDEX" not in [c.id for c in Library(lib_dir).cards]


# ── Citations ────────────────────────────────────────────────────────────────

def test_citations_are_sorted_by_what_they_imply(lib_dir: Path) -> None:
    r = Library(lib_dir).check_citations(
        ["form_validation_regression", "unit_mismatch", "duplicate_import", "made_up"])
    assert r.usable == ("form_validation_regression",)
    assert r.needs_extension == ("unit_mismatch",)
    assert r.out_of_data == ("duplicate_import",)
    assert r.unknown == ("made_up",)
    assert not r.ok


def test_an_unknown_card_is_refused_before_any_trial(lib_dir: Path) -> None:
    with pytest.raises(CitationRefused, match=r"unknown card\(s\) \['made_up'\]"):
        Library(lib_dir).require_citations(["form_validation_regression", "made_up"])


def test_an_out_of_data_card_is_refused(lib_dir: Path) -> None:
    with pytest.raises(CitationRefused, match=r"out-of-data card\(s\) \['duplicate_import'\]"):
        Library(lib_dir).require_citations(["duplicate_import"])


def test_a_needs_extension_card_passes_but_is_named(lib_dir: Path) -> None:
    r = Library(lib_dir).require_citations(["unit_mismatch"])
    assert r.ok and r.needs_extension == ("unit_mismatch",)


def test_no_citation_is_allowed(lib_dir: Path) -> None:
    assert Library(lib_dir).require_citations([]).ok


# ── Status changes are memory ────────────────────────────────────────────────

def test_set_status_rewrites_the_card_and_keeps_its_history(lib_dir: Path) -> None:
    lib = Library(lib_dir)
    c = lib.set_status("unit_mismatch", Status.OUT_OF_DATA,
                       "theory_03 refuted: the score column has no unit at all")
    assert c.status is Status.OUT_OF_DATA
    assert c.history[0]["from"] == "needs_extension" and c.history[0]["to"] == "out_of_data"
    assert "refuted" in c.history[0]["reason"]
    again = parse_card(lib_dir / "unit_mismatch.md")
    assert again.status is Status.OUT_OF_DATA and again.body == "Why.\n", "the body is untouched"
    assert lib.counts()["out_of_data"] == 2


def test_a_status_change_needs_a_reason(lib_dir: Path) -> None:
    with pytest.raises(CardError, match="needs a reason"):
        Library(lib_dir).set_status("unit_mismatch", Status.USABLE, "  ")


def test_setting_the_same_status_changes_nothing(lib_dir: Path) -> None:
    lib = Library(lib_dir)
    c = lib.set_status("default_value_leak", Status.USABLE, "no-op")
    assert c.history == ()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "agent_harness.research.library", *args],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def test_cli_index_check_and_status(lib_dir: Path) -> None:
    d = str(lib_dir)
    code, out = _cli("verify-index", "--dir", d)
    assert code == 1 and out["current"] is False
    code, out = _cli("index", "--dir", d)
    assert code == 0 and out["counts"]["usable"] == 2
    code, out = _cli("verify-index", "--dir", d)
    assert code == 0
    code, out = _cli("check", "--dir", d, "--cite", "form_validation_regression", "--cite", "duplicate_import")
    assert code == 2 and out["error"] == "CitationRefused" and "duplicate_import" in out["detail"]
    code, out = _cli("check", "--dir", d, "--cite", "unit_mismatch")
    assert code == 0 and out["needs_extension"] == ["unit_mismatch"]
    code, out = _cli("set-status", "--dir", d, "--card", "unit_mismatch", "--status", "usable",
                     "--reason", "the score column got a unit")
    assert code == 0 and out["status"] == "usable" and len(out["history"]) == 1


def test_cli_refuses_a_library_with_a_broken_card(lib_dir: Path) -> None:
    (lib_dir / "broken.md").write_text("no front matter\n")
    code, out = _cli("counts", "--dir", str(lib_dir))
    assert code == 2 and out["error"] == "CardError"
