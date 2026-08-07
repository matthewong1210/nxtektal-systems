"""NXTektal Range Operations Training Environment (Phase 0.5).

A fast, simulator-independent *operational* digital twin of a golf driving
range: dispenser inventory -> customer demand -> balls on range -> robot
collection -> handoff/washing -> back to the dispenser. A centralized Range
Operations Agent is trained and evaluated over repeated simulated operating
days through a Gymnasium-compatible environment (:class:`RangeOpsEnv`).

Relationship to Phase 0 (``nxt_sim``)
-------------------------------------
This package reuses only the *pure* Phase 0 vocabulary:

* ``nxt_sim.interfaces.types`` — ``TaskStatus`` / ``FailureReason`` enums, and
* ``nxt_sim.config.models`` — ``StrictModel`` / ``PhysicalParam`` provenance.

It never imports the Phase 0 adapters, the mock world model, or the
``HandoffController``: the operational simulator abstracts a whole handoff
cycle behind a :class:`SkillOutcomeModel`, so a future physics-backed
(Isaac) or empirically fitted model can replace the mock one without
touching this package's task logic. Phase 0 itself is untouched.

Placeholder policy (inherited project constraint)
-------------------------------------------------
All physical/economic quantities are placeholder-provenance values. Results
produced with them exercise the *pipeline*, not the facility: they must not
be presented as real facility performance.
"""

from __future__ import annotations

SIMULATOR_VERSION = "0.5.0"

__version__ = SIMULATOR_VERSION
