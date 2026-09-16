"""Dated schedules persist intent and never authorize twice across restart."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from nxt_edge_task.cases import derive_edge_view, transport_session_spec
from nxt_edge_task.contracts import canonical_json, stable_digest, utc_text
from nxt_edge_task.journal import PreconditionFailed, RecordSpec
from nxt_edge_task.schedules import ScheduleService, normalized_schedule
from tests.edge_task.conftest import Harness, run_until


def service(harness: Harness) -> ScheduleService:
    return ScheduleService(harness.edge_journal(), harness.config, harness.facts)


def payload(harness: Harness, **overrides) -> dict:
    return {
        "robot_id": "picker-01", "zone_id": "Z1", "operator": "test",
        "due_at_utc": utc_text(harness.clock() + timedelta(seconds=5)),
        "expires_at_utc": utc_text(harness.clock() + timedelta(seconds=60)),
        **overrides,
    }


def test_future_schedule_does_not_create_or_publish_until_due(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    api = service(harness)
    data = payload(harness)
    created = api.create(data, harness.clock())
    assert created["status"] == "created"
    assert api.create(data, harness.clock())["status"] == "idempotent"
    api.tick(harness.clock())
    harness.step(4)
    api.tick(harness.clock())
    assert "task_created" not in harness.kinds(harness.edge_records())
    assert harness.edge.publish_calls == []
    harness.step(1)
    api.tick(harness.clock())
    row = api.snapshot(harness.clock())["schedules"][0]
    assert row["status"] == "DISPATCHED"
    run_until(harness, lambda: harness.task(row["task_id"])["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", row["task_id"]) == 1
    assert harness.executions("carrier-01") == 0
    # A restarted service learns the fired marker from the task itself.
    restarted = service(harness)
    restarted.tick(harness.clock())
    assert len([r for r in harness.edge_records() if r.record_kind == "task_created"]) == 1
    assert restarted.snapshot(harness.clock())["schedules"][0]["task_id"] == row["task_id"]


def test_task_record_survives_crash_before_anchor_update_without_second_dispatch(harness: Harness, monkeypatch) -> None:
    harness.start_all()
    harness.step(2)
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())
    original = api.journal._write_anchor

    def crash_after_durable_task(*_args):
        raise OSError("injected crash after task line is durable")

    monkeypatch.setattr(api.journal, "_write_anchor", crash_after_durable_task)
    with pytest.raises(OSError, match="injected crash"):
        api.tick(harness.clock())
    monkeypatch.setattr(api.journal, "_write_anchor", original)
    recovered = service(harness)
    recovered.tick(harness.clock())
    rows = recovered.snapshot(harness.clock())["schedules"]
    assert rows[0]["status"] == "DISPATCHED"
    records = harness.edge_journal().read()
    tasks = [record for record in records if record.record_kind == "task_created"]
    assert len(tasks) == 1
    assert tasks[0].payload["schedule_id"] == rows[0]["schedule_id"]
    assert not any(record.record_kind == "schedule_fired" for record in records)


def test_concurrent_services_admit_the_same_due_schedule_once(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    service(harness).create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())
    apis = [service(harness), service(harness)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda api: api.tick(harness.clock()), apis))
    assert harness.kinds(harness.edge_records()).count("task_created") == 1


def test_restart_after_window_reports_missed_without_late_execution(harness: Harness) -> None:
    api = service(harness)
    api.create(payload(harness), harness.clock())
    harness.clock.advance(60)
    service(harness).tick(harness.clock())
    snapshot = service(harness).snapshot(harness.clock())
    assert snapshot["schedules"][0]["status"] == "MISSED"
    assert snapshot["notifications"][0]["reason_code"] == "dispatch_window_missed"
    service(harness).tick(harness.clock())
    assert harness.kinds(harness.edge_records()).count("schedule_missed") == 1
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_offline_due_schedule_is_rejected_once_not_retried_when_robot_returns(harness: Harness) -> None:
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())
    api.tick(harness.clock())
    row = api.snapshot(harness.clock())["schedules"][0]
    assert (row["status"], row["reason_code"]) == ("REJECTED", "device_not_online")
    harness.start_all()
    harness.step(2)
    api.tick(harness.clock())
    assert "task_created" not in harness.kinds(harness.edge_records())
    assert harness.kinds(harness.edge_records()).count("schedule_rejected") == 1


def test_stale_status_cannot_authorize_even_without_gateway_tick(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    api = service(harness)
    api.create(payload(harness), harness.clock())
    harness.clock.advance(harness.config.stale_after_s + 5)
    api.tick(harness.clock())
    assert api.snapshot(harness.clock())["schedules"][0]["reason_code"] == "device_not_online"
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_cancel_is_durable_and_never_cancels_existing_task(harness: Harness) -> None:
    api = service(harness)
    row = api.create(payload(harness), harness.clock())["schedule"]
    assert api.cancel(row["schedule_id"], "manager", harness.clock())["status"] == "cancelled"
    assert api.cancel(row["schedule_id"], "manager", harness.clock())["status"] == "idempotent"
    harness.clock.advance(10)
    service(harness).tick(harness.clock())
    assert service(harness).snapshot(harness.clock())["schedules"][0]["status"] == "CANCELLED"
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_active_task_admission_is_preserved(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    existing = harness.create()["task_id"]
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock()), operator="other"), harness.clock())
    api.tick(harness.clock())
    row = api.snapshot(harness.clock())["schedules"][0]
    assert row["reason_code"] == "robot_has_active_task"
    assert list(derive_edge_view(harness.config, harness.edge_journal().read()).tasks) == [existing]


def test_cancellation_racing_tick_cannot_undo_or_double_an_admission(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    api = service(harness)
    row = api.create(payload(harness, due_at_utc=utc_text(harness.clock())), harness.clock())["schedule"]

    def cancel():
        try:
            return service(harness).cancel(row["schedule_id"], "manager", harness.clock())
        except PreconditionFailed as exc:
            assert exc.code == "schedule_not_pending"
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cancel), executor.submit(service(harness).tick, harness.clock())]
        for future in futures:
            future.result()
    final = api.snapshot(harness.clock())["schedules"][0]
    task_count = harness.kinds(harness.edge_records()).count("task_created")
    assert (final["status"], task_count) in {("CANCELLED", 0), ("DISPATCHED", 1)}
    if final["status"] == "DISPATCHED":
        with pytest.raises(PreconditionFailed):
            api.cancel(final["schedule_id"], "manager", harness.clock())


def test_manifest_change_cannot_reuse_existing_edge_or_schedule_evidence(harness: Harness) -> None:
    harness.start_all()
    api = service(harness)
    api.create(payload(harness), harness.clock())
    foreign = ScheduleService(harness.edge_journal(), harness.config, replace(harness.facts, manifest_digest="0" * 64))
    with pytest.raises(PreconditionFailed) as raised:
        foreign.snapshot(harness.clock())
    assert raised.value.code == "schedule_identity_mismatch"


@pytest.mark.parametrize("changes", [
    {"robot_id": "unknown"}, {"zone_id": "unknown"}, {"operator": ""},
    {"operator": "name with spaces"}, {"progress_window_s": True},
    {"due_at_utc": "2026-09-12T08:00:00"}, {"recurrence": "daily"},
    {"expires_at_utc": "2026-09-11T08:00:00Z"},
])
def test_invalid_schedule_never_writes(changes, harness: Harness) -> None:
    with pytest.raises(PreconditionFailed):
        service(harness).create(payload(harness, **changes), harness.clock())
    assert harness.edge_journal().read() == ()


def test_duplicate_create_after_expiry_remains_idempotent(harness: Harness) -> None:
    api = service(harness)
    data = payload(harness)
    api.create(data, harness.clock())
    harness.clock.advance(70)
    assert service(harness).create(data, harness.clock())["status"] == "idempotent"


def test_unbound_schedule_keeps_the_original_v1_body_and_identity(harness: Harness) -> None:
    data = payload(harness)
    legacy_body = {
        "schema": "nxt-edge-schedule/v1",
        "site_id": harness.config.site_id,
        "deployment_id": harness.config.deployment_id,
        "simulation_env_id": harness.config.simulation_env_id,
        "config_digest": harness.config.config_digest,
        "manifest_digest": harness.facts.manifest_digest,
        **data,
        "progress_window_s": harness.config.default_progress_window_s,
    }
    body = normalized_schedule(data, harness.config, harness.facts)
    assert canonical_json(body) == canonical_json(legacy_body)
    row = service(harness).create(data, harness.clock())["schedule"]
    assert row["schedule_id"] == "schedule_" + stable_digest(legacy_body)[:24]
    stored = harness.edge_records()[0]
    assert canonical_json(dict(stored.payload["schedule"])) == canonical_json(legacy_body)
    assert "admission_reference" not in row
    assert service(harness).create(data, harness.clock())["status"] == "idempotent"


def test_bound_schedule_uses_v2_and_reference_participates_in_identity(harness: Harness) -> None:
    api = service(harness)
    data = payload(harness, admission_reference="evidence-01")
    row = api.create(data, harness.clock())["schedule"]
    assert row["schema"] == "nxt-edge-schedule/v2"
    assert row["admission_reference"] == "evidence-01"
    body = normalized_schedule(data, harness.config, harness.facts)
    assert row["schedule_id"] == "schedule_" + stable_digest(body)[:24]
    another = api.create({**data, "admission_reference": "evidence-02"}, harness.clock())["schedule"]
    assert another["schedule_id"] != row["schedule_id"]
    before = harness.edge_journal_path.read_bytes()
    assert service(harness).create(data, harness.clock())["status"] == "idempotent"
    assert harness.edge_journal_path.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"admission_reference": ""}, {"admission_reference": "   "},
    {"admission_reference": None}, {"admission_reference": 1},
    {"admission_reference": "x" * 129},
    {"admission_reference": "evidence-01", "unrecognized": True},
])
def test_invalid_bound_schedule_writes_nothing(harness: Harness, changes) -> None:
    with pytest.raises(PreconditionFailed):
        service(harness).create(payload(harness, **changes), harness.clock())
    assert harness.edge_records() == []


@pytest.mark.parametrize("changes", [
    {"schema": "nxt-edge-schedule/v3"},
    {"schema": "nxt-edge-schedule/v1", "admission_reference": "evidence-01"},
    {"schema": "nxt-edge-schedule/v2"},
    {"unrecognized": True},
])
def test_stored_schedule_schema_and_fields_fail_closed(harness: Harness, changes) -> None:
    body = {**normalized_schedule(payload(harness), harness.config, harness.facts), **changes}
    harness.edge_journal().append(RecordSpec(
        "schedule_created", "OPERATOR", utc_text(harness.clock()),
        {"schedule_id": "schedule_" + stable_digest(body)[:24], "schedule": body},
    ))
    with pytest.raises(PreconditionFailed) as raised:
        service(harness).snapshot(harness.clock())
    assert raised.value.code == "schedule_integrity"
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_bound_schedule_without_gate_is_rejected_before_task_creation(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)
    api = service(harness)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock()), admission_reference="evidence-01"), harness.clock())
    api.tick(harness.clock())
    row = api.snapshot(harness.clock())["schedules"][0]
    assert (row["status"], row["reason_code"]) == ("REJECTED", "admission_gate_unavailable")
    service(harness).tick(harness.clock())
    assert harness.kinds(harness.edge_records()).count("schedule_rejected") == 1
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_gate_rejection_never_reaches_task_request(harness: Harness, monkeypatch) -> None:
    from nxt_edge_task.contracts import TaskRequest

    harness.start_all()
    harness.step(2)
    api = ScheduleService(harness.edge_journal(), harness.config, harness.facts,
                          admission_gate=lambda records, row, now: ("evidence_expired", "The bound evidence expired."))
    api.create(payload(harness, due_at_utc=utc_text(harness.clock()), admission_reference="evidence-01"), harness.clock())

    def forbidden_build(*args, **kwargs):
        raise AssertionError("a rejected schedule must not build a task request")

    monkeypatch.setattr(TaskRequest, "build", forbidden_build)
    api.tick(harness.clock())
    row = api.snapshot(harness.clock())["schedules"][0]
    assert (row["status"], row["reason_code"]) == ("REJECTED", "evidence_expired")
    assert "task_created" not in harness.kinds(harness.edge_records())


def test_gate_uses_current_verified_lock_records_and_cannot_rewrite_intent(harness: Harness, monkeypatch) -> None:
    harness.start_all()
    harness.step(2)
    calls = []

    def gate(records, row, now):
        assert isinstance(records, tuple)
        assert records[-1].record_kind == "transport_session"
        assert records[-1].payload["detail"] == "inserted after tick discovery"
        assert row["admission_reference"] == "evidence-01"
        assert now == harness.clock()
        with pytest.raises(TypeError):
            row["zone_id"] = "different-zone"
        with pytest.raises(TypeError):
            records[-1].payload["detail"] = "rewritten"
        calls.append(row["schedule_id"])
        return None

    api = ScheduleService(harness.edge_journal(), harness.config, harness.facts, admission_gate=gate)
    created = api.create(payload(harness, due_at_utc=utc_text(harness.clock()), admission_reference="evidence-01"), harness.clock())
    append = api.journal.append_via

    def insert_then_admit(builder):
        append(lambda records: [transport_session_spec("probe", harness.clock(), session_present=None, detail="inserted after tick discovery")])
        return append(builder)

    with monkeypatch.context() as patch:
        patch.setattr(api.journal, "append_via", insert_then_admit)
        api.tick(harness.clock())
    assert calls == [created["schedule"]["schedule_id"]]
    row = api.snapshot(harness.clock())["schedules"][0]
    assert row["status"] == "DISPATCHED" and row["zone_id"] == "Z1"
    # Restart without a gate cannot re-admit a schedule already linked to a task.
    service(harness).tick(harness.clock())
    assert harness.kinds(harness.edge_records()).count("task_created") == 1
    run_until(harness, lambda: harness.task(row["task_id"])["state"] == "SUCCEEDED")
    assert harness.executions("picker-01", row["task_id"]) == 1


@pytest.mark.parametrize("gate_result", [False, True, (), ("", "detail"), ("code", None), ["code", "detail"]])
def test_invalid_gate_result_fails_closed(harness: Harness, gate_result) -> None:
    api = ScheduleService(harness.edge_journal(), harness.config, harness.facts,
                          admission_gate=lambda records, row, now: gate_result)
    api.create(payload(harness, due_at_utc=utc_text(harness.clock()), admission_reference="evidence-01"), harness.clock())
    before = harness.edge_journal_path.read_bytes()
    with pytest.raises(PreconditionFailed) as raised:
        api.tick(harness.clock())
    assert raised.value.code == "invalid_admission_gate"
    assert harness.edge_journal_path.read_bytes() == before


def test_gate_error_fails_closed_and_unbound_schedule_does_not_call_gate(harness: Harness) -> None:
    harness.start_all()
    harness.step(2)

    def broken_gate(records, row, now):
        raise OSError("evidence unavailable")

    api = ScheduleService(harness.edge_journal(), harness.config, harness.facts, admission_gate=broken_gate)
    data = payload(harness, due_at_utc=utc_text(harness.clock()))
    api.create({**data, "admission_reference": "evidence-01"}, harness.clock())
    before = harness.edge_journal_path.read_bytes()
    with pytest.raises(OSError, match="evidence unavailable"):
        api.tick(harness.clock())
    assert harness.edge_journal_path.read_bytes() == before
    api.cancel(api.snapshot(harness.clock())["schedules"][0]["schedule_id"], "manager", harness.clock())
    api.create(data, harness.clock())
    api.tick(harness.clock())
    assert harness.kinds(harness.edge_records()).count("task_created") == 1


@pytest.mark.parametrize("operation", ["create", "cancel", "acknowledge", "resolve"])
def test_mutation_receipt_needs_no_post_commit_read(harness: Harness, monkeypatch, operation) -> None:
    api = service(harness)
    now = harness.clock()
    data = payload(harness, due_at_utc=utc_text(now))
    schedule_id = notification_id = None
    if operation != "create":
        schedule_id = api.create(data, now)["schedule"]["schedule_id"]
    if operation in {"acknowledge", "resolve"}:
        api.tick(now)
        notification_id = api.snapshot(now)["notifications"][0]["notification_id"]
    if operation == "resolve":
        api.acknowledge(notification_id, "manager", "Reviewed", now)

    def mutate():
        if operation == "create":
            return api.create(data, now)
        if operation == "cancel":
            return api.cancel(schedule_id, "manager", now)
        return getattr(api, operation)(notification_id, "manager", "Reviewed", now)

    def broken_read(*args, **kwargs):
        raise OSError("read model unavailable after append")

    # append_via verifies and supplies the locked prefix itself. Any additional
    # read would now turn a durable response into a spurious failure.
    monkeypatch.setattr(api.journal, "read", broken_read)
    result = mutate()
    before = harness.edge_journal_path.read_bytes()
    repeated = mutate()
    assert repeated["status"] == "idempotent"
    assert harness.edge_journal_path.read_bytes() == before
    field = "notification" if notification_id else "schedule"
    assert repeated[field] == result[field]
    verified = service(harness).snapshot(now)
    assert result[field] == verified["notifications" if notification_id else "schedules"][0]


@pytest.mark.parametrize("operation", ["create", "cancel", "acknowledge", "resolve"])
def test_lost_append_result_does_not_claim_success_and_retry_is_idempotent(harness: Harness, monkeypatch, operation) -> None:
    api = service(harness)
    now = harness.clock()
    data = payload(harness, due_at_utc=utc_text(now))
    schedule_id = notification_id = None
    if operation != "create":
        schedule_id = api.create(data, now)["schedule"]["schedule_id"]
    if operation in {"acknowledge", "resolve"}:
        api.tick(now)
        notification_id = api.snapshot(now)["notifications"][0]["notification_id"]
    if operation == "resolve":
        api.acknowledge(notification_id, "manager", "Reviewed", now)

    def mutate(target):
        if operation == "create":
            return target.create(data, now)
        if operation == "cancel":
            return target.cancel(schedule_id, "manager", now)
        return getattr(target, operation)(notification_id, "manager", "Reviewed", now)

    append = api.journal.append_via

    def commit_without_result(builder):
        append(builder)
        raise OSError("lost append result")

    with monkeypatch.context() as patch:
        patch.setattr(api.journal, "append_via", commit_without_result)
        with pytest.raises(OSError, match="lost append result"):
            mutate(api)
    before = harness.edge_journal_path.read_bytes()
    recovered = mutate(service(harness))
    assert recovered["status"] == "idempotent"
    assert harness.edge_journal_path.read_bytes() == before
