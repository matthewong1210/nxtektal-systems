"""Fixtures for the nxt_memory (operational memory) tests.

Window fixtures are plain dicts built through the contract's own section
builders — no simulator involvement except in the integration guards.
"""

from __future__ import annotations

from nxt_memory.records import (
    EpisodeMeta,
    HumanVerdict,
    MemoryWindow,
    Verdict,
    WindowStatus,
    decision_section,
    execution_section,
    observation_section,
    outcome_section,
)

BASE_METRICS = {
    "demand_balls_total": 100,
    "demand_balls_served": 100,
    "stockout_minutes": 0.0,
    "open_minutes_elapsed": 60.0,
    "balls_collected": 40,
    "balls_processed": 30,
    "energy_wh": 120.0,
    "empty_travel_s": 10.0,
    "loaded_travel_s": 20.0,
    "idle_robot_s": 5.0,
    "handoff_queue_s": 0.0,
    "charger_queue_s": 0.0,
    "active_robot_s": 900.0,
    "human_interventions_requested": 0,
    "human_interventions_completed": 0,
    "hard_failures": 0,
    "estops": 0,
    "safe_recoveries": 0,
    "task_switches": 0,
    "unsafe_rejections": 0,
    "dock_attempts": 2,
    "dock_failures": 0,
}


def metrics(**overrides) -> dict:
    out = dict(BASE_METRICS)
    out.update(overrides)
    return out


def sample_recommendations() -> list[dict]:
    return [
        {
            "rule_id": "battery_reserve",
            "urgency": "now",
            "action": "Get R1 charged",
            "affected_resources": ["charger", "robot:R1"],
            "expected_outcome": "Robot stays assignable",
            "confidence": "high",
            "rationale": "R1 battery at 12%",
        },
        {
            "rule_id": "idle_capacity",
            "urgency": "soon",
            "action": "Put idle R2 to work in zone Z1",
            "affected_resources": ["robot:R2", "zone:Z1"],
            "expected_outcome": "Field balls return to the wash cycle",
            "confidence": "high",
            "rationale": "zone Z1 holds 400 balls",
        },
    ]


def make_meta(**overrides) -> EpisodeMeta:
    base = dict(
        site_id="site-test",
        deployment_id="deploy-test",
        episode_id="unit-seed7",
        scenario_name="unit",
        seed=7,
        simulator_version="0.5.0",
        git_commit="abc1234",
        driver_name="pytest",
        driver_version="1",
    )
    base.update(overrides)
    return EpisodeMeta(**base)


def make_window(
    seq: int = 0,
    *,
    episode_id: str = "unit-seed7",
    verdicts: tuple[HumanVerdict, ...] | None = None,
    recommendations: list[dict] | None = None,
    events: list[dict] | None = None,
    metrics_before: dict | None = None,
    metrics_after: dict | None = None,
    status: WindowStatus = WindowStatus.COMPLETE,
    eta: float | None = 45.0,
    t_start: float = 21600.0,
) -> MemoryWindow:
    recs = sample_recommendations() if recommendations is None else recommendations
    if verdicts is None:
        verdicts = (
            HumanVerdict(
                rec_index=0,
                rule_id="battery_reserve",
                verdict=Verdict.ACCEPTED,
                decided_by="op-1",
            ),
        )
    return MemoryWindow(
        episode_id=episode_id,
        seq=seq,
        t_start_s=t_start,
        t_end_s=t_start + 1800.0,
        status=status,
        observation=observation_section(
            state={"meta": {"scenario_name": "unit"}, "ball_flow": {"clean_available": 900}},
            operational_state="strained",
            stockout_eta_minutes=eta,
            stockout_limited_by="dirty_supply" if eta is not None else "none",
            indicators={"clean_frac": 0.5},
        ),
        decision=decision_section(
            recommendations=recs,
            rule_params={"min_battery_reserve_frac": 0.15},
            decided_by="op-1",
            verdicts=verdicts,
        ),
        execution=execution_section(
            events=events
            if events is not None
            else [{"t_s": 21660.0, "kind": "directive_applied", "payload": {}}],
            cursor_start=0,
            cursor_end=1,
        ),
        outcome=outcome_section(
            metrics_before=metrics_before or metrics(),
            metrics_after=metrics_after
            or metrics(
                stockout_minutes=3.0,
                open_minutes_elapsed=90.0,
                energy_wh=180.0,
                human_interventions_requested=1,
                estops=1,
            ),
        ),
    )
