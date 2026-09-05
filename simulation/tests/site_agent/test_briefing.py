"""Shift Briefing projection behavior over the live storyline."""

from __future__ import annotations

from nxt_site_agent import scenario_time_label
from nxt_site_agent.briefing import (
    TAG_DETECTED,
    TAG_MANAGER_DECISION,
    TAG_MISSING,
    TAG_OBSERVED,
    TAG_RECOMMENDED,
    TAG_STALE,
)


def test_scenario_time_label_is_pure_arithmetic():
    assert scenario_time_label(63000.0) == "17:30"
    assert scenario_time_label(66600.0) == "18:30"
    assert scenario_time_label(0) == "00:00"
    assert scenario_time_label(None) is None
    assert scenario_time_label(-5.0) is None


def test_briefing_over_the_full_storyline(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(7):
        service.advance()
    pending = service.recommendations_snapshot()
    service.respond(
        pending[0]["recommendation_id"],
        kind="accept",
        operator_id="mgr-demo-01",
        reason_code="staffing_available",
    )
    briefing = service.briefing_snapshot()

    assert briefing["disclaimer"] == (
        "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"
    )
    assert briefing["identity"]["workflow_id"] == (
        "range.closed_loop_collection_handoff"
    )
    assert briefing["cycles"] == {"admitted": 3, "rejected": 3}

    tags = {entry["tag"] for entry in briefing["timeline"]}
    assert TAG_OBSERVED in tags
    assert TAG_DETECTED in tags
    assert TAG_RECOMMENDED in tags
    assert TAG_MANAGER_DECISION in tags
    assert TAG_MISSING in tags
    assert TAG_STALE in tags

    # chronological by scenario time (unknown times sort first)
    times = [
        entry["scenario_t_s"]
        for entry in briefing["timeline"]
        if entry["scenario_t_s"] is not None
    ]
    assert times == sorted(times)

    # NO_ACTION is a positive, explained record (its rationale is on
    # the timeline; the full record stays on /evaluations)
    assert briefing["counts"]["no_action"] == 1
    no_action_entries = [
        entry
        for entry in briefing["timeline"]
        if entry["tag"] == TAG_DETECTED
    ]
    assert any("NO_ACTION" in entry["text"] for entry in no_action_entries)

    # one pending review remains, one decision recorded
    assert briefing["counts"]["pending_review"] == 1
    assert briefing["counts"]["manager_decisions"] == 1
    decision_entries = [
        entry
        for entry in briefing["timeline"]
        if entry["tag"] == TAG_MANAGER_DECISION
    ]
    assert "no command was created" in decision_entries[0]["text"]

    # rejected cycles are exceptions with their failure codes
    codes = sorted(
        item["failure_code"]
        for item in briefing["exceptions"]
        if item["kind"] == "rejected_cycle"
    )
    assert codes == [
        "insufficient_data_quality",
        "insufficient_data_quality",
        "stale_observation",
    ]

    # unresolved items name the pending review and the rejections
    assert any(
        "awaiting manager review" in item for item in briefing["unresolved"]
    )


def test_briefing_marks_failed_service_state(tmp_path, launch):
    service = launch(tmp_path)
    service.advance()
    service._fail("simulated_failure", "injected for the briefing test")
    briefing = service.briefing_snapshot()
    kinds = {item["kind"] for item in briefing["exceptions"]}
    assert "service_failed" in kinds
    assert any("failed state" in item for item in briefing["unresolved"])
