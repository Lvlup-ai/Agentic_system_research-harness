# agent-harness

A deterministic harness for orchestrating LLM agents.

**The LLM judges, the harness computes.** Agents propose, audit and contest.
The harness decides what happens next, keeps count, and refuses what the
protocol forbids. No decision of flow is ever left to a prompt.

> This code was extracted from a private multi-agent research pipeline that
> ran for several months. The state machine is the part that pipeline should
> have had in code from the start: it lived in a runbook prompt, and the day
> an audit showed that a "bounded" retry loop had no enforced bound at all is
> the day this repository was decided. Every module below exists because of
> an incident; each docstring says which.

## The problem

A pipeline of agents usually looks like a script that chains prompts. It works
until it doesn't, and when it doesn't, nothing tells you:

- an agent wrote outside the files it was allowed to touch;
- a "maximum of 3 retries" was exceeded because the counter lived in the model's
  context and the context was summarised;
- a report was truncated, or its summary statistics contradict its own rows;
- an agent invented a verdict that was not in the allowed vocabulary;
- a prompt described the repository as it was two months ago.

## What the harness provides

| Module | What it enforces |
|---|---|
| [`state_machine`](agent_harness/state_machine.py) | An explicit table of allowed transitions. An event the machine is not waiting for is an error that leaves no trace. Verdicts come from a closed vocabulary and are set only by the machine. |
| [`jurisdiction`](agent_harness/jurisdiction.py) | Each agent role may write only inside its declared globs. Snapshot before, enforce after, restore whatever was touched illegally. Works in-process or across a tool call. |
| [`budget`](agent_harness/budget.py) | Persistent trial quotas per subject. A trial costs more the further it drifts from the question; a retry consumes, a re-run does not; a second run does not get a fresh budget. |
| [`ledger`](agent_harness/ledger.py) | Append-only event log with a closed vocabulary. The multiple-testing counter you can read back, in Markdown. |
| [`contracts`](agent_harness/contracts.py) | Versioned deliverable contracts with a validating loader: unknown version, truncated payload, unknown field or a summary that contradicts its rows are refused. The writer runs the same checks. |
| [`review_loop`](agent_harness/review_loop.py) | The bounded adversarial loop: PASS / RETRY / ESCALATE. A RETRY needs a finding *against* the result and a list of what to redo; beyond the cap it becomes an ESCALATE. |
| [`prompt_tests`](agent_harness/prompt_tests.py) | Tests that check an agent brief tells the truth about the repository: counts, quoted thresholds, named tools, stale phrases. From pytest or from a YAML file. |

Every module has a CLI that prints JSON, so an orchestrating LLM can drive it
from a shell without ever editing a state file by hand.

## How a run looks

```
            ┌────────────────────────── harness (deterministic) ──────────────────────────┐
            │                                                                              │
  human ──► │  next_action ──► [agent: propose] ──► record ──► next_action ──► ...         │
            │       ▲              (guarded by             │                               │
            │       │               jurisdiction)          ▼                               │
            │       │                                  [agent: audit] ──► record           │
            │       │                                                        │             │
            │       └──────────── RETRY (n < N) ◄──── [agent: contest] ◄─────┘             │
            │                     PASS ──► next phase                                      │
            │                     ESCALATE ──► human                                       │
            └──────────────────────────────────────────────────────────────────────────────┘
```

The orchestrating LLM does exactly three things: ask the harness for the next
action, invoke one agent under a jurisdiction guard, and record the outcome
through the harness. It never writes state files directly.

## Quickstart

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                   # the harness, 140+ tests
.venv/bin/python -m examples.dataset_audit.run     # a full run, no model needed
```

The example ([`examples/dataset_audit`](examples/dataset_audit/README.md))
runs three scripted agents looking for rules that flag the anomalous rows of
a small table. It is written so that every mechanism fires once: a drift
refused before measurement, a write outside a jurisdiction restored, an
overstated claim caught at the boundary and sent back, a second run that
finds its budget already spent. Its briefs are tested against its code:

```bash
.venv/bin/python -m agent_harness.prompt_tests examples/dataset_audit/prompt_checks.yaml --base .
```

Plugging a model in means replacing three function bodies. Nothing else
changes.

## Driving it from a shell

Each module is also a command. A minimal loop, as an orchestrating LLM would
run it:

```bash
python -m agent_harness.state_machine init --runs-root runs --phases rules --max-iterations 20
python -m agent_harness.state_machine next-action --runs-root runs --run-id <id> --phase rules
SNAP=$(python -m agent_harness.jurisdiction capture --run-root runs/<id> --matrix matrix.yaml)
#   ... invoke the proposer ...
python -m agent_harness.jurisdiction enforce --run-root runs/<id> --matrix matrix.yaml \
    --snapshot-dir "$SNAP" --role proposer --slot item=rule_01
python -m agent_harness.state_machine record-audit --runs-root runs --run-id <id> --phase rules --verdict GO
python -m agent_harness.state_machine record-result --runs-root runs --run-id <id> --phase rules \
    --accepted true --score 0.61 --id rule_01
```

A forbidden transition, an unknown role, an event name outside the ledger's
vocabulary or an invented verdict all exit with code 2 and a JSON error, and
save nothing.

## Design rules

- Same inputs and same code give the same outputs. No randomness anywhere.
- Every threshold is declared before the run, in a file, and is never changed
  during the run.
- A refused event leaves no trace. A recorded one is never rewritten.
- A prompt is a document that nothing compiles. Here, prompts are tested.

## Status and roadmap

Early. The seven modules above are complete and tested; the API may still
move. Candidates for the next steps, in rough order:

- a **security gate**: self-tests that prove each guard works before a run
  starts, so a silent regression cannot let a run go unguarded;
- an **AST guard** for agent-written code: whitelisted imports, no file I/O,
  no private attribute access, no dynamic execution;
- generic **brief templates** for the proposer, auditor and reviewer roles.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
