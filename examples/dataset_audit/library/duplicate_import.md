---
title: Duplicate import
status: out_of_data
requires: [import_batch_id]
---
A file imported twice creates rows that are identical except for their
identifier. The anomaly is not in any value: it is in the multiplicity.

**What it predicts.** Pairs of rows identical on every business column, with
consecutive identifiers, and an import batch that appears twice in the
import log.

**Why this card is out of reach.** Testing it needs the import batch
identifier, and this table does not carry it. A theory cannot cite this card
until the data does. The status changes when the column exists; the card
stays so the gap is visible.
