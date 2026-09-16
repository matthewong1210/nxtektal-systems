"""Dated schedules persist intent and never authorize twice across restart."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from nxt_edge_task.cases import derive_edge_view
from nxt_edge_task.contracts import utc_text
from nxt_edge_task.journal import PreconditionFailed
from nxt_edge_task.schedules import ScheduleService
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
