---
title: Malformed rows come from a form that lost its validation
tracks: [email, signup_date, age]
orders_of_magnitude:
  malformed_share: "a few percent of rows, not a majority"
  clustering: "most malformed rows after one deployment date, not spread evenly"
---
This is an *idea*, not a thesis. It names a supposed mechanism, says why it
should exist, and gives orders of magnitude. It carries no thresholds and no
rules: writing the theories that test it, with their predictions and their
zones of refutation, is the researcher's work.

**Mechanism.** The signup form is the only entry point for these rows. A
deployment that dropped the client-side validation would let malformed emails,
impossible ages and future dates through. If that is what happened, malformed
rows cluster after one date rather than spreading evenly across the years,
and each column's anomalies share that date.

**Why it should exist.** Validation regressions are common when a form is
rebuilt, and nothing else in this pipeline touches the rows once they are in.
User behaviour would produce a spread; a regression produces a step.

**What would make it wrong.** Malformed rows spread evenly across signup
dates, or each column's anomalies start at a different date: then the form
is not the cause, or not the only one.

**Tracks.** One per column that a validation rule would protect: `email`,
`signup_date`, `age`. The `score` and `country` columns are out of the idea's
scope; a theory on them is allowed, and costs more.
