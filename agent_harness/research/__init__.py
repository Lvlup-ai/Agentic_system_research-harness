"""The research layer: how a researcher agent thinks, made enforceable.

A human deposits an *idea*: a supposed mechanism, why it should exist, orders
of magnitude, no thresholds. The researcher turns it into *theories*, one per
track, each written before any measurement: the mechanism, a quantified
prediction with its zones of confirmation and refutation, what would prove it
wrong, what is measured and why that tests this mechanism. The note is sealed,
measured, and its verdict is computed from the zones it declared. The next
theory is written on what the previous ones established; at the end, the idea
itself is rewritten on the verdicts.

Modules: ``theory`` (the sealed note and the computed verdict), ``library``
(closed knowledge with statuses), ``knowledge`` (what past theories
established), ``idea`` (the human starting point and its rewrite).
"""
