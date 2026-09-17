"""Admission timestamps are verified read evidence, never stage-start facts."""

import pytest

from nxt_edge_task.cases import TASK_CREATED
from nxt_pilot_ops.planning_workflow import CONFIRMATION
from nxt_site_agent import SiteAgentError
from scripts.pilot_dispatch_demo import PilotDispatchRuntime
from .test_dispatch_composition import runner
from .test_planning_composition import post, setup_plan
from .test_planning_failures import inject_mismatched_task, planning_runner
from tests.pilot_ops.test_planning_wire_contract import validator


def read_without_writes(runtime):
    journal = runtime.journal
    before = journal.path.read_bytes(), journal.anchor_path.read_bytes()
    snapshot = runtime.route_planning('GET', '/api/v1/planning', {})
    validator('#/$defs/Snapshot').validate(snapshot)
    assert (journal.path.read_bytes(), journal.anchor_path.read_bytes()) == before
    assert snapshot['outcomes'] == []  # admission alone creates no stage facts
    return snapshot['confirmations'][0]


@pytest.mark.parametrize('state', [None, 'SCHEDULED', 'CANCELLED', 'REJECTED', 'MISSED'])
def test_no_admission_keeps_timestamp_null_without_rewriting_confirmation(runner, state):
    runtime, clock = runner
    source, _, request = setup_plan(runtime, clock, delay=120)
    receipt = post(runtime, 'confirmations', request)
    durable = receipt['record']
    assert 'task_created_at_utc' not in durable
    if state is not None:
        runtime.planning.recover()
    if state == 'CANCELLED':
        runtime.route('POST', f"/api/v0/task-ops/schedules/{durable['schedule_id']}/cancel",
                      {'operator': 'manager'})
    elif state == 'REJECTED':
        source.update(request_id='corrected', expected_revision=1, reason='Close zone')
        source['zones'][0]['collection_allowed']['value'] = False
        post(runtime, 'inputs', source)
        clock.advance(120)
        runtime.tick()
    elif state == 'MISSED':
        clock.advance(3600)
        runtime.tick()
    projected = read_without_writes(runtime)
    assert projected['schedule_status'] == state
    assert projected['task_id'] is None
    assert projected['task_created_at_utc'] is None
    assert not any(r.record_kind == TASK_CREATED for r in runtime.journal.read())
    recovered = runtime.route_planning('GET', '/api/v1/planning/requests/confirm-one', {})
    assert recovered['record'] == durable
    assert post(runtime, 'confirmations', request)['record'] == durable


def test_delayed_admission_projects_original_record_time_only(runner):
    runtime, clock = runner
    _, plan, request = setup_plan(runtime, clock)
    clock.advance(1)
    receipt = post(runtime, 'confirmations', request)
    clock.advance(6.123456)
    runtime.tick()
    assert runtime.failure is None
    task = next(r for r in runtime.journal.read() if r.record_kind == TASK_CREATED)
    assert task.recorded_at_utc == '2026-09-16T08:00:07.123456Z'
    assert task.recorded_at_utc != plan['selection']['start_at_utc']
    assert task.recorded_at_utc != receipt['record']['confirmed_at_utc']
    clock.advance(20)
    projected = read_without_writes(runtime)
    assert projected['task_id'] == task.payload['task_id']
    assert projected['task_created_at_utc'] == task.recorded_at_utc
    stored = next(r for r in runtime.journal.read() if r.record_kind == CONFIRMATION)
    assert stored.to_dict()['payload'] == receipt['record']
    assert 'task_created_at_utc' not in stored.payload
    # Recovery receipts are durable records, not time-varying read projections.
    assert runtime.route_planning('GET', '/api/v1/planning/requests/confirm-one', {})['record'] == receipt['record']
    assert post(runtime, 'confirmations', request)['record'] == receipt['record']


@pytest.mark.parametrize('admitted', [False, True])
def test_restart_replays_legacy_confirmation_without_migration(runner, admitted):
    runtime, clock = runner
    _, _, request = setup_plan(runtime, clock)
    receipt = post(runtime, 'confirmations', request)
    if admitted:
        clock.advance(7)
        runtime.tick()
    projected = read_without_writes(runtime)
    prefix = runtime.journal.path.read_bytes()
    assert 'task_created_at_utc' not in receipt['record']
    runtime.close()
    clock.advance(1)
    restarted = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        restarted.start(background=False)
        after = read_without_writes(restarted)
        assert after['task_id'] == projected['task_id']
        assert after['task_created_at_utc'] == projected['task_created_at_utc']
        assert restarted.journal.path.read_bytes().startswith(prefix)
        tasks = [r for r in restarted.journal.read() if r.record_kind == TASK_CREATED]
        assert len(tasks) == int(admitted)
        assert post(restarted, 'confirmations', request)['record'] == receipt['record']
    finally:
        restarted.close()


@pytest.mark.parametrize('mismatch', ['task_id', 'zone_id', 'issued_at_utc'])
def test_bad_admission_link_is_unavailable_instead_of_a_timestamp(planning_runner, mismatch):
    runtime, clock = planning_runner
    inject_mismatched_task(runtime, clock, mismatch)
    before = runtime.journal.path.read_bytes(), runtime.journal.anchor_path.read_bytes()
    with pytest.raises(SiteAgentError) as raised:
        runtime.route_planning('GET', '/api/v1/planning', {})
    assert raised.value.code == 'planning_unavailable'
    assert (runtime.journal.path.read_bytes(), runtime.journal.anchor_path.read_bytes()) == before
