# agent-harness

A research lab run by LLM agents, where the harness is deterministic.

**The LLM judges, the harness computes.** A human deposits an idea. A
researcher agent turns it into theories, one at a time, each written before
any measurement: the mechanism, a quantified prediction with its zones of
confirmation and refutation, what would prove it wrong. The note is sealed,
measured, and its verdict is computed from the zones it declared. The next
theory is written on what the previous ones established. At the end, the
idea is rewritten on the verdicts. Nothing in that loop is left to a prompt:
what the agent may cite, when it may spend, whether it may retry, and what
its verdict is are all decided by code.

> This code was extracted from a private multi-agent research pipeline that
> ran for several months. Every module exists because a research loop left
> to itself went wrong in a specific way; each docstring says which.

## The research loop

```
   human ──► idea.md ─────────────────────────────────────────────────────────────┐
              (mechanism, why, orders of magnitude, tracks — frozen during the run) │
                                                                                    │
   ┌─── per theory ──────────────────────────────────────────────────────────────┐  │
   │  knowledge.briefing ──► [researcher] writes the note ──► auditor challenges │  │
   │         │                    │                                │             │  │
   │   what earlier          mechanism, prediction,          before the seal:    │  │
   │   theories established  zones, refutation, cites        falsifiable? cited? │  │
   │                              │                                              │  │
   │  refused before spending if: already refuted · ignores its track's history  │  │
   │                              · cites an unknown card · budget says no       │  │
   │                              ▼                                              │  │
   │                          theory.seal ──► budget.consume ──► measurement     │  │
   │                                                                  │          │  │
   │                          verdict computed from the sealed zones ◄┘          │  │
   │                          confirmed · refuted · undecided                    │  │
   │                              │                                              │  │
   │                          knowledge.record (what exactly was refuted)        │  │
   └──────────────────────────────┼──────────────────────────────────────────────┘  │
                                  ▼                                                 │
   report under contract ──► [reviewer] compares every note with its seal           │
                             PASS · RETRY (n ≤ N) · ESCALATE ──► human              │
                                  ▼                                                 │
   idea rewritten on the verdicts ◄─────────────────────────────────────────────────┘
```

The orchestrating LLM asks the harness for the next action, invokes one agent
under a write guard, and records the outcome through the harness. It never
writes a state file, never picks a verdict, never decides a retry.

## The research layer

| Module | What it enforces |
|---|---|
| [`research/idea`](agent_harness/research/idea.py) | The human starting point: mechanism, why, orders of magnitude, tracks. Frozen during a run; rewritten at the end from the knowledge store, every claim carrying its theory and verdict. |
| [`research/library`](agent_harness/research/library.py) | Closed knowledge: cards with a status (`usable`, `needs_extension`, `out_of_data`), an index generated from them, citations checked before any trial. |
| [`research/theory`](agent_harness/research/theory.py) | The theory note, declared before the measurement. Falsifiable or refused. Sealed by hash; a reworded note is detected and restored. The verdict is computed from the declared zones, never chosen, and never recomputed: a theory is measured once. |
| [`research/knowledge`](agent_harness/research/knowledge.py) | What past theories established: append-only, per subject. The same formulation is not refuted twice; a note on a track with history must say what it builds on; lessons feed the briefs. |
| [`research/trial`](agent_harness/research/trial.py) | The step token that binds the modules: an append-only chain of HMAC stamps, keyed by a run secret the agents never hold. No seal without clearance by knowledge, library and budget; no payment without a seal; no measurement without payment; the distance paid is the one cleared. |

## The substrate

| Module | What it enforces |
|---|---|
| [`state_machine`](agent_harness/state_machine.py) | An explicit table of allowed transitions. An event the machine is not waiting for is an error that leaves no trace. Verdicts of a phase come from a closed vocabulary. |
| [`jurisdiction`](agent_harness/jurisdiction.py) | Each agent role may write only inside its declared globs. Snapshot before, enforce after, restore whatever was touched illegally. Works in-process or across a tool call. |
| [`budget`](agent_harness/budget.py) | Persistent trial quotas per subject. A trial costs more the further it drifts from the idea; a retry consumes, a re-run does not; a second run does not get a fresh budget. |
| [`ledger`](agent_harness/ledger.py) | Append-only event log with a closed vocabulary. The multiple-testing counter you can read back, in Markdown. |
| [`contracts`](agent_harness/contracts.py) | Versioned deliverable contracts with a validating loader: unknown version, truncated payload, unknown field or a summary that contradicts its rows are refused. |
| [`review_loop`](agent_harness/review_loop.py) | The bounded adversarial loop: PASS / RETRY / ESCALATE. A RETRY needs a finding *against* the result; beyond the cap it becomes an ESCALATE. |
| [`prompt_tests`](agent_harness/prompt_tests.py) | Tests that check an agent brief tells the truth about the repository: counts, quoted thresholds, named tools, stale phrases. |
| [`journal`](agent_harness/journal.py) | The run journal: every move of the orchestrator, written by the harness itself when a module acts, plus the orchestrator's notes in its own words. Hash-chained, so an edited or removed line shows; rendered in Markdown for the end-of-run review. It prevents nothing; it makes everything reviewable. |

Every module has a CLI, so an orchestrating LLM can drive it from a shell:
a refusal is a JSON line and exit code 2 (`prompt_tests` and `library
verify-index` use exit 1 for "the check failed", as test runners do). Every
module header says why it exists, how to use it, and the format of what it
reads and writes.

What is enforced, and by what: each module refuses what it can see (an
unsealed note, an unknown card, an exhausted budget, an event out of order).
The *order* between modules is enforced by the passport of `research/trial`:
with it, `seal` and `measure` refuse a note that was not cleared and a trial
that was not paid. Without it (`--unguarded`, or the Python API called
without a passport), the sequence is the caller's discipline, and the
harness says so.

What is deliberately *not* enforced: the orchestrator's latitude. It declares
distances, abandons iterations, arbitrates disagreements, chooses what to
measure. That latitude is acceptable because every one of those moves is
written: with a journal active (`--journal`, or `AGENT_HARNESS_JOURNAL` set
once), each module writes its line when it acts, refusals carry the module
that said no, failures keep their kind, and the orchestrator writes its
reasons with `journal note`. The end-of-run review reads `journal render`.

## Quickstart

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                   # the harness, 210+ tests
.venv/bin/python -m examples.dataset_audit.run     # a full research run, no model needed
```

The example ([`examples/dataset_audit`](examples/dataset_audit/README.md))
is a research lab on a dataset audit: an idea about a form that lost its
validation, a library of four cards, three scripted agents, eight theories.
It is written so that every mechanism fires once: a theory refuted by its own
zones, one confirmed, one left undecided, a drift refused by the budget, a
citation of a card the library does not have, the same formulation offered
twice, a note reworded after its seal and caught at the boundary, a write
outside a jurisdiction restored. Run it twice and the second run starts from
what the first established. Its briefs are tested against its code:

```bash
.venv/bin/python -m agent_harness.prompt_tests examples/dataset_audit/prompt_checks.yaml --base .
```

Plugging a model in means replacing five function bodies in `agents.py`.
Nothing else changes.

## Driving it from a shell

Each module is also a command. One theory, as an orchestrating LLM would run
it. The secret file lives outside every run root and outside every path an
agent can read; `runs/r1/budget.json` is the run's budget state:

```bash
export AGENT_HARNESS_JOURNAL=runs/r1/journal.jsonl
python -m agent_harness.research.idea freeze --idea idea.md --run-root runs/r1
python -m agent_harness.budget init --run-file runs/r1/budget.json --store budget --subject s --reason first_pass --total 12 --track email
python -m agent_harness.research.knowledge briefing --store knowledge --subject s --track email
#   ... the researcher writes runs/r1/theories/t01/note.json under a jurisdiction guard ...
python -m agent_harness.research.trial clear --dir runs/r1/theories/t01 --secret-file .secret --budget-run-file runs/r1/budget.json --distance in_scope --store knowledge --subject s --library library
python -m agent_harness.research.theory exchange --dir runs/r1/theories/t01 --role auditor --position "falsifiable"
python -m agent_harness.research.theory seal --dir runs/r1/theories/t01 --secret-file .secret
python -m agent_harness.research.trial consume --dir runs/r1/theories/t01 --secret-file .secret --budget-run-file runs/r1/budget.json
#   ... the measurement runs and writes values.json: the numbers the note predicted ...
python -m agent_harness.research.theory measure --dir runs/r1/theories/t01 --secret-file .secret --values values.json
python -m agent_harness.research.knowledge record --store knowledge --subject s --theory-dir runs/r1/theories/t01 --run-id r1 --secret-file .secret
python -m agent_harness.budget commit --run-file runs/r1/budget.json
python -m agent_harness.journal note --actor orchestrator --subject t01 --text "in scope: the email track is the idea's first track; measured as declared"
python -m agent_harness.journal verify
```

Every command above left its line in `runs/r1/journal.jsonl`; `python -m
agent_harness.journal render` turns it into the chronology a reviewer reads.

Skip `trial clear` and `seal` is refused; skip `trial consume` and `measure`
is refused; measure twice and the second is refused; edit a stamp and every
later command is refused. A forbidden transition, an unknown role, an
unfalsifiable note, a citation outside the library or an invented verdict
exit with code 2 and a JSON error, and save nothing. This sequence is
replayed by the test suite.

## Design rules

- Same inputs and same code give the same outputs. No randomness anywhere.
- Every threshold is declared before the run, in a file, and is never changed
  during the run.
- A theory is written before its measurement, judged by what it declared,
  and measured once.
- A refused event leaves no trace in the state. A recorded one is never
  rewritten. Both leave a line in the journal.
- The order of the protocol is a fact the harness checks (the passport),
  not a discipline of the orchestrator.
- The orchestrator is free to decide, and every decision is written where a
  reviewer will read it.
- A prompt is a document that nothing compiles. Here, prompts are tested.

## Status and roadmap

Early. The thirteen modules above are complete and tested; the API may still
move. Known limits, stated rather than hidden: the guard only watches the
roots it is given, so an agent with shell access can write elsewhere; the
measured values come from whoever runs the measurement; a run's `config.json`
is not sealed. Next steps, in rough order:

- a **security gate**: self-tests that prove each guard works before a run
  starts, so a silent regression cannot let a run go unguarded;
- an **AST guard** for agent-written measurement code: whitelisted imports,
  no file I/O, no private attribute access, no dynamic execution;
- generic **brief templates** for the researcher, auditor and reviewer roles.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
