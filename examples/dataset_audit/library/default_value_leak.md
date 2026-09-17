---
title: Default value leak
status: usable
requires: [age]
---
When a numeric field is optional in the form but not in the database, the
empty answer is stored as the type's default: zero for an integer. Zero is
then a *missing* value wearing the clothes of a real one.

**What it predicts.** A cluster of rows at exactly zero in a column where
zero is impossible (an age, a price, a duration), and no rows just above it.

**What would contradict it.** Zeros absent, or zeros accompanied by other
impossible values (negative, absurdly large), which point to free-text input
rather than a default.

**How to test it here.** Flag rows outside the plausible range of the
column, then look at how many of the flagged rows sit exactly at zero.
