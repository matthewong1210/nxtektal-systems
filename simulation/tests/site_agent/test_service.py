"""Behavioral tests for the Pilot Site Agent service shell."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from nxt_site_agent import (
    CompositionSeam,
    LaunchMaterials,
    LaunchRefusedError,
    ServiceState,
    ServiceStorage,
    SiteAgentError,
    SiteAgentService,
    SourceCursor,
)
from nxt_workflow_enablement import RANGE_OPS_WORKFLOW_ID, RangeOpsLaunchPlan
from scripts.site_agent_fixture import (
    DEPLOYMENT_ID,
    MAX_SERVICE_CYCLES,
    SITE_ID,
    broken_service_manifest_payload,
    evaluate_service_enablement,
    service_composition_seam,
)
from nxt_workflow_enablement import canonical_report_json

EXPECTED_STORYLINE = (
    ("evaluated", 0, "no_action", None, None),
    ("evaluated", 1, "recommend", "operator_intervention", None),
    ("rejected", 2, None, None, "insufficient_data_quality"),
    ("rejected", 2, None, None, "stale_observation"),
    ("rejected", 2, None, None, "insufficient_data_quality"),
    ("evaluated", 2, "recommend", "operator_intervention", None),
    ("source_exhausted", None, None, None, None),
)

EVIDENCE_STREAMS = ("ledger.jsonl", "evaluations.jsonl", "snapshots.jsonl")


def run_full_storyline(service: SiteAgentService) -> list[dict]:
    outcomes = []
    for _ in range(len(EXPECTED_STORYLINE)):
        outcomes.append(service.advance())
    return outcomes


def evidence_bytes(storage: ServiceStorage) -> dict[str, bytes]:
    return {
        name: (storage.workflow_evidence_root / name).read_bytes()
        for name in EVIDENCE_STREAMS
    }


def test_fresh_launch_runs_the_declared_storyline(tmp_path, launch):
    service = launch(tmp_path)
    outcomes = run_full_storyline(service)
    observed = [
        (
            outcome["outcome"],
            outcome["sequence_number"],
            outcome["verdict"],
            outcome["recommendation_action"],
            outcome["failure_code"],
        )
        for outcome in outcomes
    ]
    assert observed == [tuple(item) for item in EXPECTED_STORYLINE]
    health = service.health_snapshot()
    assert health["service_state"] == "serving"
    assert health["degraded"] is False
    assert health["pending_recommendation_count"] == 2
    assert health["source"]["cursor"] == {
        "consumed_cycles": 6,
        "next_sequence_number": 3,
    }
    assert health["source"]["exhausted"] is True
    assert health["mode_label"] == "fixture-backed Shadow Mode"


def test_rejected_cycles_create_no_policy_evidence(tmp_path, launch):
    service = launch(tmp_path)
    run_full_storyline(service)
    evaluations = service.evaluations_snapshot()
    # three admitted cycles -> exactly three evaluation records
    assert [item["sequence_number"] for item in evaluations] == [0, 1, 2]
    assert [item["verdict"] for item in evaluations] == [
        "no_action",
        "recommend",
        "recommend",
    ]
    # the NO_ACTION record carries its canonical trace evidence
    assert evaluations[0]["trace"] is not None
    assert evaluations[0]["trace"]["rationale"]
    # RECOMMEND records surface the ledger-stored trace
    assert evaluations[1]["trace"] is not None
    assert evaluations[1]["trace"]["missing_data_reasons"]


def test_missing_sensor_is_not_zero_inventory(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(3):  # calm, spike, missing-dispenser rejection
        service.advance()
    state = service.state_snapshot()
    # the latest admitted envelope is the 18:30 spike, not a zeroed reading
    assert state["available"] is True
    assert state["dispenser"]["clean_available_balls"] == 2400
    assert state["envelope"]["sequence_number"] == 1
    # the rejected cycle is visible as an exception, not as state
    briefing = service.briefing_snapshot()
    rejected = [
        item
        for item in briefing["exceptions"]
        if item["kind"] == "rejected_cycle"
    ]
    assert len(rejected) == 1
    assert rejected[0]["failure_code"] == "insufficient_data_quality"


def test_full_storyline_evidence_is_byte_identical(tmp_path, launch):
    first = launch(tmp_path / "a")
    run_full_storyline(first)
    second = launch(tmp_path / "b")
    run_full_storyline(second)
    assert evidence_bytes(first.storage) == evidence_bytes(second.storage)
    report_a = first.storage.read_ready_report_text()
    report_b = second.storage.read_ready_report_text()
    assert report_a == report_b


def test_restart_resumes_without_duplicate_evidence(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(4):  # ends on the retained storyline position: cursor 4/2
        service.advance()
    before = evidence_bytes(service.storage)
    cursor_before = service.health_snapshot()["source"]["cursor"]
    service.stop()

    resumed = launch(tmp_path)
    assert resumed.storage.run_root == service.storage.run_root
    assert resumed.health_snapshot()["source"]["cursor"] == cursor_before
    assert evidence_bytes(resumed.storage) == before
    remaining = [resumed.advance() for _ in range(3)]
    assert [item["outcome"] for item in remaining] == [
        "rejected",
        "evaluated",
        "source_exhausted",
    ]

    # the completed evidence equals a single uninterrupted run
    reference = launch(tmp_path / "reference", force_fresh=False)
    # force_fresh=False under a fresh directory performs a fresh launch
    run_full_storyline(reference)
    assert evidence_bytes(resumed.storage) == evidence_bytes(
        reference.storage
    )


def test_stale_cursor_behind_by_one_replays_idempotently(tmp_path, launch):
    service = launch(tmp_path)
    service.advance()
    service.advance()  # acknowledged spike cycle; cursor now 2/2
    before = evidence_bytes(service.storage)
    service.stop()
    # simulate a crash between the feed advance and the cursor write:
    # the persisted cursor is behind by exactly one resolved cycle
    storage = service.storage
    storage.write_cursor(
        SourceCursor(consumed_cycles=1, next_sequence_number=1)
    )
    resumed = launch(tmp_path)
    outcome = resumed.advance()
    assert outcome["outcome"] == "replay_skipped"
    assert outcome["sequence_number"] == 1
    assert evidence_bytes(resumed.storage) == before
    assert resumed.health_snapshot()["source"]["cursor"] == {
        "consumed_cycles": 2,
        "next_sequence_number": 2,
    }


def test_stale_cursor_behind_a_rejection_re_rejects_deterministically(
    tmp_path, launch
):
    service = launch(tmp_path)
    for _ in range(3):  # last outcome: missing-dispenser rejection (3/2)
        service.advance()
    before = evidence_bytes(service.storage)
    service.stop()
    service.storage.write_cursor(
        SourceCursor(consumed_cycles=2, next_sequence_number=2)
    )
    resumed = launch(tmp_path)
    outcome = resumed.advance()
    assert outcome["outcome"] == "rejected"
    assert outcome["failure_code"] == "insufficient_data_quality"
    assert evidence_bytes(resumed.storage) == before


def test_not_ready_composition_refuses_launch(tmp_path, broken_seam):
    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path,
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=broken_seam,
        )
    assert excinfo.value.code == "workflow_not_ready"
    # a refused launch composes nothing and writes no evidence
    assert list(tmp_path.iterdir()) == [] or not any(
        (tmp_path / child.name / SITE_ID).exists()
        for child in tmp_path.iterdir()
    )


def test_service_gate_refuses_a_not_ready_report_with_a_hand_built_plan(
    tmp_path, seam
):
    _, report = evaluate_service_enablement(broken_service_manifest_payload())
    hand_plan = RangeOpsLaunchPlan(
        workflow_id=RANGE_OPS_WORKFLOW_ID,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        transport_mode="FIXTURE_ONLY",
        runtime_mode="SHADOW",
        max_cycles=MAX_SERVICE_CYCLES,
        simulation_midnight_iso="2026-08-08T00:00:00+00:00",
        clean_sensed_valid=True,
        evidence_paths=(
            "checkpoints/evaluation",
            "checkpoints/site",
            "evaluations.jsonl",
            "ledger.jsonl",
            "snapshots.jsonl",
        ),
    )
    forged = CompositionSeam(
        composer=seam.composer,
        materials_for=lambda root: LaunchMaterials(
            plan=hand_plan,
            report_canonical_json=canonical_report_json(report),
        ),
        cycle_catalog=seam.cycle_catalog,
    )
    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path,
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=forged,
        )
    assert excinfo.value.code == "workflow_not_ready"
    assert "NOT_READY" in excinfo.value.detail


def test_non_fixture_or_non_shadow_plans_are_refused(tmp_path, seam):
    materials = seam.materials_for(
        tmp_path / "probe" / SITE_ID / DEPLOYMENT_ID / RANGE_OPS_WORKFLOW_ID
    )

    def forged_seam(plan: RangeOpsLaunchPlan) -> CompositionSeam:
        return CompositionSeam(
            composer=seam.composer,
            materials_for=lambda root: LaunchMaterials(
                plan=plan,
                report_canonical_json=materials.report_canonical_json,
            ),
            cycle_catalog=seam.cycle_catalog,
        )

    live_plan = dataclasses.replace(
        materials.plan, transport_mode="LIVE_DEVICE"
    )
    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path / "live",
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=forged_seam(live_plan),
        )
    assert excinfo.value.code == "transport_not_fixture_only"

    active_plan = dataclasses.replace(materials.plan, runtime_mode="ACTIVE")
    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path / "active",
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=forged_seam(active_plan),
        )
    assert excinfo.value.code == "runtime_not_shadow"


def test_identity_mismatch_refuses_launch(tmp_path, seam):
    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path,
            site_id=SITE_ID,
            deployment_id="another-deployment",
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=seam,
        )
    assert excinfo.value.code == "identity_mismatch"


def test_foreign_evidence_without_service_records_is_a_collision(
    tmp_path, launch
):
    root = (
        tmp_path
        / "run-001"
        / SITE_ID
        / DEPLOYMENT_ID
        / RANGE_OPS_WORKFLOW_ID
    )
    root.mkdir(parents=True)
    (root / "ledger.jsonl").write_text("foreign\n", encoding="utf-8")
    with pytest.raises(LaunchRefusedError) as excinfo:
        launch(tmp_path)
    assert excinfo.value.code == "evidence_root_collision"


def test_tampered_report_refuses_resume(tmp_path, launch):
    service = launch(tmp_path)
    service.advance()
    service.stop()
    report_path = service.storage.report_path
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["manifest_digest"] = "sha256:" + "0" * 64
    report_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LaunchRefusedError) as excinfo:
        launch(tmp_path)
    assert excinfo.value.code == "report_invalid"


def test_graceful_stop_refuses_further_operations(tmp_path, launch):
    service = launch(tmp_path)
    service.advance()
    service.stop()
    assert service.health_snapshot()["service_state"] == "stopped"
    with pytest.raises(SiteAgentError) as excinfo:
        service.advance()
    assert excinfo.value.code == "advance_refused"
    with pytest.raises(SiteAgentError) as excinfo:
        service.respond(
            "rec_missing",
            kind="accept",
            operator_id="mgr",
            reason_code="x",
        )
    assert excinfo.value.code == "service_stopped"
    service.stop()  # idempotent


def test_source_exhaustion_and_bounded_run_refuse_advances(tmp_path, launch):
    service = launch(tmp_path)
    run_full_storyline(service)
    with pytest.raises(SiteAgentError) as excinfo:
        service.advance()
    assert excinfo.value.code == "advance_refused"
    assert "exhausted" in excinfo.value.detail
    # the declared bounded-run cap is enforced independently
    service._plan = dataclasses.replace(service._plan, max_cycles=1)
    with pytest.raises(SiteAgentError) as excinfo:
        service.advance()
    assert excinfo.value.code == "advance_refused"


def test_cursor_write_failure_fails_the_service_closed(
    tmp_path, launch, monkeypatch
):
    service = launch(tmp_path)

    def broken_write(cursor):
        raise SiteAgentError("cursor_write_failed", "disk full (simulated)")

    monkeypatch.setattr(service._storage, "write_cursor", broken_write)
    with pytest.raises(SiteAgentError) as excinfo:
        service.advance()
    assert excinfo.value.code == "cursor_write_failed"
    # The service must actually fail closed: an unwritable cursor makes
    # a future restart unsafe, so no further cycles may advance.
    health = service.health_snapshot()
    assert health["service_state"] == "failed"
    assert health["degraded"] is True
    with pytest.raises(SiteAgentError) as refused:
        service.advance()
    assert refused.value.code == "advance_refused"


def test_event_append_failure_degrades_but_does_not_block(
    tmp_path, launch, monkeypatch
):
    service = launch(tmp_path)
    monkeypatch.setattr(
        type(service._storage), "append_event", lambda self, event: False
    )
    outcome = service.advance()
    assert outcome["outcome"] == "evaluated"
    health = service.health_snapshot()
    assert health["degraded"] is True
    assert health["service_state"] == "serving"
    assert health["event_append_failures"] >= 1


def test_broken_evidence_store_produces_failed_state(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    service.stop()
    journal = service.storage.workflow_evidence_root / "evaluations.jsonl"
    text = journal.read_text(encoding="utf-8")
    journal.write_text(text[: len(text) // 2], encoding="utf-8")
    resumed = launch(tmp_path)
    health = resumed.health_snapshot()
    assert health["service_state"] == "failed"
    assert health["degraded"] is True
    assert health["last_failure_code"] is not None
    with pytest.raises(SiteAgentError):
        resumed.advance()


def test_reset_launches_the_next_empty_run_directory(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    first_bytes = evidence_bytes(service.storage)
    first_root = service.storage.run_root
    health = service.reset()
    assert service.storage.run_root.name == "run-002"
    assert health["source"]["cursor"] == {
        "consumed_cycles": 0,
        "next_sequence_number": 0,
    }
    # the first run's canonical evidence is untouched
    previous = ServiceStorage(
        first_root,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        workflow_id=RANGE_OPS_WORKFLOW_ID,
    )
    assert evidence_bytes(previous) == first_bytes
    outcome = service.advance()
    assert outcome["outcome"] == "evaluated"
    assert outcome["sequence_number"] == 0


def test_restart_runtime_recomposes_from_persisted_cursor(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(3):
        service.advance()
    before = evidence_bytes(service.storage)
    health = service.restart_runtime()
    assert health["service_state"] == "serving"
    assert health["source"]["cursor"] == {
        "consumed_cycles": 3,
        "next_sequence_number": 2,
    }
    assert evidence_bytes(service.storage) == before
    outcome = service.advance()
    assert outcome["outcome"] == "rejected"
    assert outcome["failure_code"] == "stale_observation"


def test_only_the_range_ops_workflow_acquires_evidence(tmp_path, launch):
    service = launch(tmp_path)
    run_full_storyline(service)
    children = sorted(
        child.name for child in service.storage.identity_root.iterdir()
    )
    assert children == [
        RANGE_OPS_WORKFLOW_ID,
        "service",
        "workflow_enablement_report.ready.json",
    ]
    report = json.loads(service.storage.read_ready_report_text())
    verdicts = {
        workflow_id: section["verdict"]
        for workflow_id, section in report["workflows"].items()
    }
    assert verdicts[RANGE_OPS_WORKFLOW_ID] == "READY_FOR_FIXTURE_SHADOW_MODE"
    assert verdicts["course.grounds_condition_intelligence"] == "NOT_READY"
    assert verdicts["course.player_caddy_experience"] == "NOT_READY"


def test_manager_response_uses_scenario_time_and_ledger_legality(
    tmp_path, launch
):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    pending = [
        item
        for item in service.recommendations_snapshot()
        if item["case_status"] == "pending"
    ]
    assert len(pending) == 1
    recommendation_id = pending[0]["recommendation_id"]
    result = service.respond(
        recommendation_id,
        kind="accept",
        operator_id="mgr-demo-01",
        reason_code="staffing_available",
        note="Send a staff member to refill the hopper.",
    )
    assert result["case_status"] == "accepted"
    assert result["manager_response"]["kind"] == "accept"
    # responded_at equals the scenario now of the latest observation
    assert result["manager_response"]["responded_at"].startswith(
        "2026-08-08T18:30:00"
    )
    # a second response is rejected by existing workflow semantics
    with pytest.raises(SiteAgentError) as excinfo:
        service.respond(
            recommendation_id,
            kind="reject",
            operator_id="mgr-demo-01",
            reason_code="changed_mind",
        )
    assert excinfo.value.code == "workflow_transition_rejected"


def test_manager_response_before_issuance_is_rejected(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    pending = service.recommendations_snapshot()[0]
    with pytest.raises(SiteAgentError) as excinfo:
        service.respond(
            pending["recommendation_id"],
            kind="accept",
            operator_id="mgr-demo-01",
            reason_code="early",
            responded_at="2026-08-08T00:30:00+00:00",
        )
    # the existing workflow contract owns this rule: a response cannot
    # precede issuance, and the ledger rejects the illegal transition
    assert excinfo.value.code == "workflow_transition_rejected"


def test_manager_modify_records_a_linked_modification(tmp_path, launch):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    pending = service.recommendations_snapshot()[0]
    result = service.respond(
        pending["recommendation_id"],
        kind="modify",
        operator_id="mgr-demo-01",
        reason_code="tighter_deadline",
        replacement_action="operator_intervention",
        replacement_execute_before="2026-08-08T19:00:00+00:00",
        note="Handle it before the 19:00 rush.",
    )
    assert result["case_status"] == "modified"
    assert result["manager_response"]["kind"] == "modify"
    original = result["recommendation"]
    assert original is not None
    assert original["action"] == "operator_intervention"


def test_launch_seam_is_deterministic(tmp_path, seam):
    probe_root = (
        tmp_path / "probe" / SITE_ID / DEPLOYMENT_ID / RANGE_OPS_WORKFLOW_ID
    )
    first = seam.materials_for(probe_root)
    second = seam.materials_for(probe_root)
    assert first.report_canonical_json == second.report_canonical_json
    assert dataclasses.asdict(first.plan) == dataclasses.asdict(second.plan)
