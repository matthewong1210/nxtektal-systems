"""Regression tests from the adversarial hardening review."""

from __future__ import annotations

import dataclasses

import pytest

from nxt_site_agent import (
    ServiceStorage,
    SiteAgentError,
    SourceCursor,
)
from nxt_site_agent.service import _run_number
from nxt_workflow_enablement import (
    RANGE_OPS_WORKFLOW_ID,
    WorkflowEnablementError,
)
from scripts.pilot_course_a_enablement_fixture import (
    assemble_range_ops_runtime,
    runtime_evidence_root_is_empty,
)
from scripts.site_agent_fixture import (
    DEPLOYMENT_ID,
    SITE_ID,
    service_composition_seam,
    service_launch_materials,
    service_observation_source,
    service_site,
)


def test_scenario_now_survives_restart_with_rejected_later_times(
    tmp_path, launch
):
    """Post-restart responses must not be stamped earlier than pre-restart."""
    service = launch(tmp_path)
    for _ in range(5):  # through the two 19:30 rejections
        service.advance()
    before = service.scenario_now_iso()
    assert before is not None and "19:30" in before
    service.stop()
    resumed = launch(tmp_path)
    after = resumed.scenario_now_iso()
    assert after == before


def test_bounded_run_survives_restart(tmp_path, launch, monkeypatch):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    # Tighten the declared bound to the already-consumed count and prove
    # both the live service and a resumed service refuse further cycles.
    service._plan = dataclasses.replace(service._plan, max_cycles=2)
    with pytest.raises(SiteAgentError) as excinfo:
        service.advance()
    assert excinfo.value.code == "advance_refused"
    assert "bounded run" in excinfo.value.detail
    service.stop()
    resumed = launch(tmp_path)
    resumed._plan = dataclasses.replace(resumed._plan, max_cycles=2)
    with pytest.raises(SiteAgentError) as refused:
        resumed.advance()
    assert refused.value.code == "advance_refused"


def test_failed_restart_keeps_the_existing_runtime_serving(tmp_path, launch):
    service = launch(tmp_path)
    service.advance()
    # Tamper the persisted cursor beyond the declared cycle count: the
    # recompose must refuse with a coded error and leave the current
    # runtime able to keep advancing.
    service.storage.write_cursor(
        SourceCursor(consumed_cycles=99, next_sequence_number=99)
    )
    with pytest.raises(SiteAgentError) as excinfo:
        service.restart_runtime()
    assert excinfo.value.code == "restart_refused"
    health = service.health_snapshot()
    assert health["service_state"] == "serving"
    outcome = service.advance()
    assert outcome["outcome"] == "evaluated"


def test_tampered_cursor_refuses_process_launch_with_coded_error(
    tmp_path, launch
):
    service = launch(tmp_path)
    service.advance()
    service.stop()
    service.storage.write_cursor(
        SourceCursor(consumed_cycles=99, next_sequence_number=99)
    )
    from nxt_site_agent import LaunchRefusedError, SiteAgentService

    with pytest.raises(LaunchRefusedError) as excinfo:
        SiteAgentService.launch(
            runs_root=tmp_path,
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=service_composition_seam(),
        )
    assert excinfo.value.code == "composition_failed"


def test_briefing_is_idempotent_under_crash_window_redelivery(
    tmp_path, launch
):
    """A replayed cycle after a cursor-behind crash is one admission."""
    service = launch(tmp_path)
    service.advance()
    service.advance()
    service.stop()
    # crash window: the second cycle's event was appended but the cursor
    # write was lost
    service.storage.write_cursor(
        SourceCursor(consumed_cycles=1, next_sequence_number=1)
    )
    resumed = launch(tmp_path)
    outcome = resumed.advance()
    assert outcome["outcome"] == "replay_skipped"
    briefing = resumed.briefing_snapshot()
    assert briefing["cycles"] == {"admitted": 2, "rejected": 0}


def test_unreadable_journal_is_an_explicit_briefing_exception(
    tmp_path, launch
):
    service = launch(tmp_path)
    for _ in range(2):
        service.advance()
    journal = service.storage.workflow_evidence_root / "evaluations.jsonl"
    text = journal.read_text(encoding="utf-8")
    journal.write_text(text[: len(text) // 2], encoding="utf-8")
    briefing = service.briefing_snapshot()
    kinds = {item["kind"] for item in briefing["exceptions"]}
    assert "evidence_unreadable" in kinds
    assert any(
        "could not be read" in item for item in briefing["unresolved"]
    )


def test_run_directory_numbering_survives_four_digits(tmp_path, launch):
    assert _run_number("run-001") == 1
    assert _run_number("run-999") == 999
    assert _run_number("run-1000") == 1000
    assert _run_number("run-0001") is None
    assert _run_number("run-01") is None
    assert _run_number("other-001") is None


def test_partially_created_run_directory_self_heals(tmp_path, launch):
    """A crashed reset leaves a report-only run dir; relaunch recovers it."""
    service = launch(tmp_path)
    service.advance()
    service.stop()
    partial = ServiceStorage(
        tmp_path / "run-002",
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        workflow_id=RANGE_OPS_WORKFLOW_ID,
    )
    partial.write_ready_report(service.storage.read_ready_report_text())
    resumed = launch(tmp_path)
    assert resumed.storage.run_root.name == "run-002"
    assert resumed.health_snapshot()["source"]["cursor"] == {
        "consumed_cycles": 0,
        "next_sequence_number": 0,
    }
    outcome = resumed.advance()
    assert outcome["outcome"] == "evaluated"
    assert outcome["sequence_number"] == 0


def test_emptiness_predicates_agree(tmp_path):
    """The service's and the composition root's emptiness proofs must agree."""
    storage = ServiceStorage(
        tmp_path / "run-001",
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        workflow_id=RANGE_OPS_WORKFLOW_ID,
    )
    root = storage.workflow_evidence_root

    def agree() -> None:
        assert storage.workflow_root_is_empty() == (
            runtime_evidence_root_is_empty(root)
        )

    agree()  # absent
    root.mkdir(parents=True)
    agree()  # empty directory
    (root / "ledger.jsonl").write_text("x\n", encoding="utf-8")
    agree()  # populated
    (root / "ledger.jsonl").unlink()
    root.rmdir()
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("file-valued root", encoding="utf-8")
    agree()  # collision: a file where the directory should be


def test_factory_rejects_source_with_explicit_resume_parameters(tmp_path):
    materials = service_launch_materials(
        tmp_path / SITE_ID / DEPLOYMENT_ID / RANGE_OPS_WORKFLOW_ID
    )
    site = service_site()
    source = service_observation_source(site)
    with pytest.raises(WorkflowEnablementError) as excinfo:
        assemble_range_ops_runtime(
            materials.plan,
            site,
            tmp_path / "evidence",
            source=source,
            consumed_cycles=2,
        )
    assert "supersedes" in str(excinfo.value)


def test_factory_rejects_non_source_and_non_site_config_objects(tmp_path):
    materials = service_launch_materials(
        tmp_path / SITE_ID / DEPLOYMENT_ID / RANGE_OPS_WORKFLOW_ID
    )
    site = service_site()
    with pytest.raises(WorkflowEnablementError):
        assemble_range_ops_runtime(
            materials.plan, site, tmp_path / "evidence", source=object()
        )
    with pytest.raises(WorkflowEnablementError):
        assemble_range_ops_runtime(
            materials.plan,
            site,
            tmp_path / "evidence",
            site_config={"scenario_name": "not-a-site-config"},
        )


def test_deferred_cycle_records_the_retryable_incident_code(
    tmp_path, launch, monkeypatch
):
    service = launch(tmp_path)
    service.advance()

    # Make the journal unavailable so the next admitted cycle defers.
    from nxt_agent_runtime import EvaluationJournal

    def unavailable(self, record):
        raise OSError("journal store offline (simulated)")

    monkeypatch.setattr(EvaluationJournal, "append", unavailable)
    outcome = service.advance()
    assert outcome["outcome"] == "evaluation_deferred"
    assert outcome["failure_code"] == "journal_unavailable"
    events = service.storage.read_events()
    deferred = [
        event
        for event in events
        if event.get("outcome") == "evaluation_deferred"
    ]
    assert deferred and deferred[-1]["failure_code"] == "journal_unavailable"
    # a deferral resolves nothing, so it must not consume the bound
    assert service.health_snapshot()["source"]["cursor"] == {
        "consumed_cycles": 1,
        "next_sequence_number": 1,
    }


def test_state_projection_reports_worst_dispenser_channel_status(
    tmp_path, launch
):
    service = launch(tmp_path)
    service.advance()
    state = service.state_snapshot()
    assert state["dispenser"]["reading_status"] == "ok"
    assert state["dispenser"]["sensed_reading_age_s"] == 5.0
