"""Explicit human planning, durable confirmation and independent results."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from nxt_edge_task.cases import TASK_CREATED
from nxt_pilot_ops.planning_workflow import CONFIRMATION
from nxt_site_agent import SiteAgentApiServer, SiteAgentError
from scripts.pilot_dispatch_demo import PilotDispatchRuntime
from .test_dispatch_composition import runner, cycle, call, execution_count
from tests.pilot_ops.test_planning_wire_contract import validator

EXAMPLE = json.loads((Path(__file__).resolve().parents[2] / 'docs/contracts/planning-v1/examples/success.json').read_text())


def sample(definition):
    return deepcopy(next(e['request']['body'] for e in EXAMPLE['exchanges']
                         if e['request'].get('schema_ref') == f'#/$defs/{definition}'))


def setup_plan(runtime, clock, delay=5):
    source = sample('InputRequest')
    post(runtime, 'inputs', source)
    request = sample('PlanRequest')
    request['selection']['start_at_utc'] = (clock() + timedelta(seconds=delay)).isoformat().replace('+00:00', 'Z')
    plan = post(runtime, 'plans', request)['record']
    assert plan['status'] == 'READY'
    confirmation = {'schema': 'nxt-planning-confirmation/v1', 'request_id': 'confirm-one',
                    'plan_id': plan['plan_id'], 'plan_version': plan['version'], 'operator': '经理 陈'}
    return source, plan, confirmation


def post(runtime, kind, body):
    return runtime.route_planning('POST', '/api/v1/planning/' + kind, body)


def test_http_complete_flow_uses_shared_schema_and_never_infers_inventory(runner, launch, tmp_path):
    runtime, clock = runner
    service = launch(tmp_path / 'agent')
    server = SiteAgentApiServer(service, task_operations=runtime.route, planning_operations=runtime.route_planning)
    server.start_background()
    try:
        status, empty = call(server, 'GET', '/api/v1/planning')
        assert status == 200 and empty['data']['latest_input'] is None
        validator('#/$defs/SuccessEnvelope').validate(empty)
        for definition, suffix in [('InputRequest', 'inputs'), ('PlanRequest', 'plans')]:
            request = sample(definition)
            if definition == 'PlanRequest':
                request['selection']['start_at_utc'] = '2026-09-16T08:00:05Z'
            status, result = call(server, 'POST', '/api/v1/planning/' + suffix, request)
            assert status == 200
            validator('#/$defs/SuccessEnvelope').validate(result)
        plan = result['data']['record']
        body = {'schema': 'nxt-planning-confirmation/v1', 'request_id': 'confirm-one',
                'plan_id': plan['plan_id'], 'plan_version': 1, 'operator': '经理 陈'}
        status, confirmed = call(server, 'POST', '/api/v1/planning/confirmations', body)
        assert status == 200
        validator('#/$defs/SuccessEnvelope').validate(confirmed)
        assert confirmed['data']['record']['request']['operator'] == '经理 陈'
        cycle(runtime, clock)
        status, state = call(server, 'GET', '/api/v1/planning')
        validator('#/$defs/SuccessEnvelope').validate(state)
        data = state['data']
        c = data['confirmations'][0]
        assert c['task_id'] and c['schedule_status'] == 'DISPATCHED'
        assert execution_count(runtime.root) == 1
        assert next(iter(runtime.snapshot()['tasks'].values()))['state'] == 'SUCCEEDED'
        assert data['outcomes'] == []
        original_inventory = data['latest_input']['inventory_clean_balls']
        for stage, quantity in [('COLLECTED', 510), ('UNLOADED', 500), ('WASHED', 480), ('SUPPLIED', 470)]:
            outcome = sample('OutcomeRequest')
            outcome.update(request_id='result-' + stage, confirmation_id=c['confirmation_id'],
                           task_id=c['task_id'], stage=stage, quantity_balls=quantity,
                           started_at_utc='2026-09-16T08:00:05Z',
                           completed_at_utc='2026-09-16T08:00:15Z', supersedes_outcome_id=None)
            status, result = call(server, 'POST', '/api/v1/planning/outcomes', outcome)
            assert status == 200, result
            validator('#/$defs/SuccessEnvelope').validate(result)
        _, state = call(server, 'GET', '/api/v1/planning')
        assert len(state['data']['outcomes']) == 4
        assert state['data']['latest_input']['inventory_clean_balls'] == original_inventory
        assert service.state_snapshot()['available'] is False
    finally:
        server.shutdown()
        service.stop()


def test_duplicate_concurrent_confirmations_and_restart_execute_once(runner):
    runtime, clock = runner
    _, _, confirmation = setup_plan(runtime, clock)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: post(runtime, 'confirmations', {**confirmation, 'request_id': f'confirm-{i}'}), range(12)))
    assert len({r['record']['confirmation_id'] for r in results}) == 1
    assert sum(r['disposition'] == 'created' for r in results) == 1
    assert len({r['request_id'] for r in results}) == 1
    runtime.close()  # durable intent, no schedule exists yet
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        cycle(resumed, clock)
        assert len(resumed.snapshot()['schedules']) == 1
        assert execution_count(runtime.root) == 1
        assert post(resumed, 'confirmations', confirmation)['disposition'] == 'duplicate'
        with pytest.raises(SiteAgentError) as exc:
            post(resumed, 'confirmations', {**confirmation, 'plan_version': 2})
        assert exc.value.code == 'planning_conflict'
    finally:
        resumed.close()


@pytest.mark.parametrize('change', ['revision', 'closure', 'weather_expiry'])
def test_input_changes_or_expiry_block_pending_schedule_at_due(runner, change):
    runtime, clock = runner
    source, _, confirmation = setup_plan(runtime, clock, delay=120)
    post(runtime, 'confirmations', confirmation)
    runtime.planning.recover()
    if change == 'weather_expiry':
        clock.advance(580)
    else:
        source.update(request_id='input-two', expected_revision=1, reason='Corrected conditions')
        if change == 'closure':
            source['zones'][0]['collection_allowed']['value'] = False
        post(runtime, 'inputs', source)
        clock.advance(120)
    runtime.tick()
    assert runtime.failure is None
    row = runtime.snapshot()['schedules'][0]
    assert row['status'] in {'REJECTED', 'MISSED'}
    assert row['reason_code'] in {'planning_conflict', 'dispatch_window_missed'}
    assert runtime.snapshot()['tasks'] == {}
    assert execution_count(runtime.root) == 0


def test_schedule_write_then_lost_ack_recovers_same_schedule(runner, monkeypatch):
    runtime, clock = runner
    _, _, confirmation = setup_plan(runtime, clock)
    c = post(runtime, 'confirmations', confirmation)['record']
    real = runtime.schedules.create
    def lost(*args, **kwargs):
        real(*args, **kwargs)
        raise OSError('lost schedule acknowledgement')
    monkeypatch.setattr(runtime.schedules, 'create', lost)
    runtime.tick()
    assert runtime.failure is not None
    runtime.close()
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        cycle(resumed, clock)
        rows = resumed.snapshot()['schedules']
        assert len(rows) == 1 and rows[0]['schedule_id'] == c['schedule_id']
        assert execution_count(runtime.root) == 1
    finally:
        resumed.close()


def test_confirmation_lost_ack_is_unknown_then_queryable_without_duplicate(runner, monkeypatch):
    runtime, clock = runner
    _, _, confirmation = setup_plan(runtime, clock)
    real = runtime.journal.append_via
    def lost(builder):
        records = real(builder)
        if any(r.record_kind == CONFIRMATION for r in records):
            raise OSError('lost durable confirmation receipt')
        return records
    monkeypatch.setattr(runtime.journal, 'append_via', lost)
    with pytest.raises(SiteAgentError) as exc:
        post(runtime, 'confirmations', confirmation)
    assert exc.value.code == 'planning_result_unknown'
    monkeypatch.setattr(runtime.journal, 'append_via', real)
    known = runtime.route_planning('GET', '/api/v1/planning/requests/confirm-one', {})
    assert known['record']['plan_id'] == confirmation['plan_id']
    runtime.close()
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        assert post(resumed, 'confirmations', confirmation)['disposition'] == 'duplicate'
        cycle(resumed, clock)
        assert len(resumed.snapshot()['schedules']) == 1
        assert execution_count(runtime.root) == 1
    finally:
        resumed.close()


def test_dispatched_task_is_immutable_when_new_input_closes_zone(runner):
    runtime, clock = runner
    source, _, confirmation = setup_plan(runtime, clock)
    post(runtime, 'confirmations', confirmation)
    cycle(runtime, clock)
    before = [r.to_dict() for r in runtime.journal.read() if r.record_kind == TASK_CREATED]
    source.update(request_id='closed-zone', expected_revision=1, reason='Close zone for maintenance')
    source['zones'][0]['collection_allowed']['value'] = False
    post(runtime, 'inputs', source)
    cycle(runtime, clock, 3)
    after = [r.to_dict() for r in runtime.journal.read() if r.record_kind == TASK_CREATED]
    assert before == after and len(after) == 1
    assert execution_count(runtime.root) == 1
    snapshot = runtime.planning.snapshot(clock())
    assert snapshot['confirmations'][0]['task_id'] == before[0]['payload']['task_id']
    assert snapshot['outcomes'] == []


def test_expired_recovery_materializes_original_intent_but_creates_no_task(runner):
    runtime, clock = runner
    _, _, confirmation = setup_plan(runtime, clock)
    original = post(runtime, 'confirmations', confirmation)['record']
    runtime.close()
    clock.advance(3600)
    resumed = PilotDispatchRuntime(runtime.root, clock=clock, step_interval_s=0)
    try:
        resumed.start(background=False)
        resumed.tick()
        assert resumed.failure is None
        row = resumed.snapshot()['schedules'][0]
        assert row['status'] == 'MISSED'
        assert row['schedule_id'] == original['schedule_id']
        assert execution_count(runtime.root) == 0
    finally:
        resumed.close()


def test_cancel_before_dispatch_keeps_confirmation_consumed(runner):
    runtime, clock = runner
    _, _, confirmation = setup_plan(runtime, clock)
    c = post(runtime, 'confirmations', confirmation)['record']
    runtime.planning.recover()
    runtime.route('POST', f"/api/v0/task-ops/schedules/{c['schedule_id']}/cancel", {'operator': 'manager'})
    assert post(runtime, 'confirmations', {**confirmation, 'request_id': 'repeat'})['disposition'] == 'duplicate'
    cycle(runtime, clock)
    assert runtime.snapshot()['schedules'][0]['status'] == 'CANCELLED'
    assert execution_count(runtime.root) == 0
