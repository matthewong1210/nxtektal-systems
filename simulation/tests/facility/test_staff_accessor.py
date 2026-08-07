"""staff_summary(): the one additive upstream accessor for the facility layer."""

from __future__ import annotations

from .conftest import rng_states


def test_staff_summary_initial_state(sim, weekday):
    capacity, busy, queued = sim.staff_summary()
    assert capacity == weekday.human_ops.staff_count
    assert busy == 0
    assert queued == 0


def test_staff_summary_is_pure(sim):
    before = rng_states(sim)
    first = sim.staff_summary()
    second = sim.staff_summary()
    assert first == second
    assert rng_states(sim) == before


def test_staff_summary_reflects_busy_staff(sim):
    # Occupy one staff slot directly on the simpy resource; the accessor must
    # report it without touching simulation dynamics.
    request = sim._human_staff.request()
    try:
        capacity, busy, queued = sim.staff_summary()
        assert busy == 1
        assert queued == 0
        assert capacity == sim.scenario.human_ops.staff_count
    finally:
        sim._human_staff.release(request)
    assert sim.staff_summary()[1] == 0
