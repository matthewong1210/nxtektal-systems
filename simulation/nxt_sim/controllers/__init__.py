"""Robot TASK logic. Simulator-independent: must never import from adapters."""

from nxt_sim.controllers.handoff_state_machine import (
    CycleOutcome,
    HandoffController,
    HandoffState,
    TransitionRecord,
)

__all__ = ["CycleOutcome", "HandoffController", "HandoffState", "TransitionRecord"]
