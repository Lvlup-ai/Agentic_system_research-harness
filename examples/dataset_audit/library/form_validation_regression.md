---
title: Form validation regression
status: usable
requires: [signup_date, email, age]
---
A form that is rebuilt often loses a validation rule it used to have. When
that happens, the rows created after the deployment carry values the old
form would have rejected: an address without `@`, an empty field, an age no
human has, a date in the future.

**What it predicts.** Malformed values cluster *after* one date rather than
spreading evenly over the years, and the columns the same form validates
share that date.

**What would contradict it.** Malformed values spread evenly across signup
dates, or each column's anomalies start at a different date.

**How to test it here.** Flag the malformed rows of a column with a rule,
then measure the share of flagged rows whose `signup_date` is on or after a
candidate deployment date (`share_after`). A regression gives a share close
to one; user behaviour gives a share close to the overall share of rows
after that date.
