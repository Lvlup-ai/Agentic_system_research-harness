# Example: a research lab on a dataset audit

A human deposits an **idea**: malformed rows come from a signup form that
lost its validation. A **researcher** turns it into theories, one per
iteration, each written before any measurement, sealed, measured, and judged
by the zones it declared. An **auditor** challenges each note before the
seal. A **reviewer** checks, at the boundary, that every theory is what was
sealed. The next theory builds on what the previous ones established; at the
end, the idea is rewritten on the verdicts.

The three agents are scripted Python functions, so the run needs no model and
no key; each function says where a model call would go. The harness around
them is the real one.

```bash
python -m examples.dataset_audit.run
```

## The pieces

| File | What it is |
|---|---|
| `idea.md` | The human starting point: mechanism, why, orders of magnitude, three tracks. Frozen during a run. |
| `library/` | Four cards a theory may cite, each with a status. `INDEX.md` is generated from them. |
| `data.py` | Forty rows, nine labelled anomalies, no randomness. |
| `rules.py`, `metrics.py` | The deterministic measurement: a rule flags rows; three functions compute what a theory predicts. |
| `agents.py` | The scripted researcher, auditor and reviewer. |
| `run.py` | The orchestrator: the loop below, wired through the harness. |
| `report.py` | The deliverable of the phase, under contract `theory-report/1`. |
| `briefs/` | The three prompts a model would receive. Tested by `prompt_checks.yaml`. |

## What you will see

Eight iterations, each exercising one mechanism of the research loop:

| # | The researcher's theory | The harness |
|---|---|---|
| 1 | `email`, in scope: malformed emails cluster after a 2023 deployment | sealed, measured; `share_after_2023` = 0.0 falls in the refutation zone ⇒ **refuted**. The postmortem says what exactly: the 2023 cut-off, not the idea |
| 2 | `signup_date`, in scope: future dates exist and are all anomalies | `flagged` = 2, `precision` = 1.0 ⇒ **confirmed** |
| 3 | `score`, off topic: out-of-range scores are a unit problem | **refused before any spending**: the `age` track has no theory yet |
| 4 | `age`, in scope: impossible ages cluster after 2023 | `share_after_2023` = 0.5 is in the grey zone ⇒ **undecided** |
| 5 | `age`, adjacent, citing `server_side_dedup` | **refused**: the library has no such card |
| 6 | `email`, in scope: the same formulation as theory 1 | **refused**: already refuted; the note names what was refuted |
| 7 | `age`, adjacent, citing `unit_mismatch` with a declared approximation | `flagged` = 4, `precision` = 1.0 ⇒ **confirmed**, cost 2. Then the researcher **rewords its prediction** to match the numbers |
| 8 | a valid note, but the researcher writes into `state.json` | the file is **restored**, a jurisdiction failure is recorded |

The phase ends on `max_iterations`. The harness writes `report.json` under
contract; the reviewer reads it through the loader, compares each note with
its seal, finds theory 7 reworded, and returns `RETRY` with an `AGAINST`
finding: "restore the sealed note". The sealed note is put back; the reviewer
returns `PASS`. One ledger line for the retry, the counter at 1 of 2. Then
`idea_rewritten.md` is generated: every claim carries its theory and its
verdict, the `signup_date` track shows its confirmation, and the lessons no
run must pay for again are listed.

Everything lands under `examples/dataset_audit/runs/<run id>/`: `state.json`
(written only by the state machine), `idea.frozen.json`, one directory per
theory with `note.json`, `measurement.json`, `audit.md`, `exchange.json`,
`seal.json`, `measure.json` and `verdict.json`, then `report.json`,
`review/` and `idea_rewritten.md`. Next to the runs, and persisting across
them: `knowledge/<subject>.jsonl`, `budget/<subject>.json`, `ledger.md`.

## Run it twice

The second run on the same table starts with what the first established:
theory 1 is refused as already refuted before anything is spent; the confirmed
and undecided theories are measured again and say which earlier theories
they build on; the budget is the subject's, not the run's, so it starts at 5
of 12 already spent.

## The briefs are tested

`briefs/` holds the three prompts. `prompt_checks.yaml` states what they must
say about the code: the number of columns, operators and metric functions
comes from the modules, the costs from `config.yaml`, and every library card
must carry a status. Add a metric function without updating the researcher's
brief and CI fails:

```bash
python -m agent_harness.prompt_tests examples/dataset_audit/prompt_checks.yaml --base .
```

## Plugging a model in

Replace the bodies of `research`, `audit`, `postmortem` and `review` in
`agents.py` with calls to your model, each reading the matching brief, the
knowledge briefing and the library index. Keep the files and return shapes.
Nothing else changes: the seal, the computed verdict, the budget, the
guard, the contract and the review cap apply to a model exactly as they
apply to the script.
