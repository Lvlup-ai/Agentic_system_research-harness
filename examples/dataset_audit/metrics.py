"""The deterministic measurement: what a theory can ask the harness to compute.

A theory note names metrics and gives each one zones. The researcher also
writes a ``measurement.json`` next to the note, saying how each metric is
computed: a rule that flags rows, and one of the functions below. The harness
runs it; nothing here judges.

    {
      "rule": {"column": "email", "op": "contains", "value": "@"},
      "metrics": {
        "precision":        {"fn": "precision"},
        "share_after_2023": {"fn": "share_after", "date": "2023-01-01"}
      }
    }
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .rules import Rule, measure

MetricFn = Callable[[list[dict], Rule, Mapping], float]


def _flagged(rows: list[dict], rule: Rule) -> list[dict]:
    return [r for r in rows if rule.rejects(r)]


def precision(rows: list[dict], rule: Rule, params: Mapping) -> float:
    """Share of flagged rows that are labelled anomalies."""
    return measure(rule, rows).precision


def flagged(rows: list[dict], rule: Rule, params: Mapping) -> float:
    """Number of rows the rule flags."""
    return float(measure(rule, rows).flagged)


def share_after(rows: list[dict], rule: Rule, params: Mapping) -> float:
    """Share of flagged rows whose signup_date is on or after ``date`` (0.0 if none flagged)."""
    date = str(params["date"])
    hit = _flagged(rows, rule)
    if not hit:
        return 0.0
    return round(sum(1 for r in hit if str(r["signup_date"]) >= date) / len(hit), 4)


METRICS: dict[str, MetricFn] = {"precision": precision, "flagged": flagged, "share_after": share_after}


class MeasurementError(ValueError):
    """The measurement spec names an unknown function or a malformed rule."""


def validate_measurement(spec: Mapping, columns: tuple[str, ...]) -> Rule:
    """Refuse a spec the harness could not run. Returns the rule."""
    try:
        rule = Rule.from_dict(spec["rule"])
    except (KeyError, TypeError) as exc:
        raise MeasurementError(f"measurement needs a `rule`: {exc}") from exc
    rule.validate(columns)
    metrics = spec.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise MeasurementError("measurement needs a non-empty `metrics` mapping")
    for name, m in metrics.items():
        fn = (m or {}).get("fn")
        if fn not in METRICS:
            raise MeasurementError(f"metric {name!r}: unknown function {fn!r}; known: {tuple(METRICS)}")
        if fn == "share_after" and "date" not in m:
            raise MeasurementError(f"metric {name!r}: share_after needs a `date`")
    return rule


def run_measurement(spec: Mapping, rows: list[dict], columns: tuple[str, ...]) -> dict[str, float]:
    """Compute every metric of the spec. Deterministic."""
    rule = validate_measurement(spec, columns)
    return {name: METRICS[m["fn"]](rows, rule, m) for name, m in spec["metrics"].items()}
