---
title: Unit mismatch
status: needs_extension
requires: [score]
---
Two producers write the same column in different units: one in percent, one
in basis points, one in points out of ten and one out of a hundred. The
column then holds two populations that look like one.

**What it predicts.** A bimodal distribution whose modes differ by a constant
factor (10, 100), and values that are impossible in the intended unit.

**What would contradict it.** A single mode, or out-of-range values that are
not multiples of one another.

**Why this card needs an extension.** The table carries no producer
identifier, so the two populations cannot be told apart directly. A theory
that cites this card must declare its approximation: which proxy stands in
for the producer, and what that proxy cannot decide.
