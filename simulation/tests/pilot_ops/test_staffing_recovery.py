from __future__ import annotations

import importlib
import multiprocessing
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from types import MappingProxyType

import pytest

from nxt_pilot_ops.serialization import canonical_json, stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    CommittedReceipt,
    ConflictReceipt,
    DuplicateReceipt,
    GenerationRouteEvidence,
    OPERATION_KINDS,
    ExceptionCommittedProjection,
    GenerationCommittedProjection,
    ManagerCommittedProjection,
    ResultEvidence,
    RosterCommittedProjection,
    StaffingError,
)
from nxt_pilot_ops.staffing.ledger import EventCommit, StaffingLedger

from .staffing_fixtures import roster_import_request


UTC = timezone.utc
SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-edge-task-sim-v0"
NOW = datetime(2026, 10, 5, 0, 0, 0, 123456, tzinfo=UTC)
RACE_HARD_TIMEOUT_SECONDS = 10
RACE_PROCESS_STOP_SECONDS = 2


def _assert_race_completes_with_hard_timeout(target: Callable[[], None]) -> None:
    context = multiprocessing.get_context("fork")
    receive_result, send_result = context.Pipe(duplex=False)

    def run_target() -> None:
        try:
            target()
        except BaseException:
            send_result.send(("error", traceback.format_exc()))
            raise
        else:
            send_result.send(("ok", None))
        finally:
            send_result.close()

    process = context.Process(target=run_target)
    process.start()
    send_result.close()
    process.join(RACE_HARD_TIMEOUT_SECONDS)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join(RACE_PROCESS_STOP_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(RACE_PROCESS_STOP_SECONDS)

    stopped = not process.is_alive()
    exitcode = process.exitcode
    result = None
    if receive_result.poll():
        try:
            result = receive_result.recv()
        except EOFError:
            pass
    receive_result.close()
    if stopped:
        process.close()

    if timed_out:
        assert stopped, "timed-out race subprocess could not be stopped"
        pytest.fail(
            f"race witness exceeded hard timeout of {RACE_HARD_TIMEOUT_SECONDS}s",
            pytrace=False,
        )
    if result is not None and result[0] == "error":
        pytest.fail(f"isolated race witness failed:\n{result[1]}", pytrace=False)
    assert exitcode == 0
    assert result == ("ok", None)


def _isolated_race_witness(
    target: Callable[..., None],
) -> Callable[..., None]:
    @wraps(target)
    def wrapper(*args, **kwargs) -> None:
        _assert_race_completes_with_hard_timeout(lambda: target(*args, **kwargs))

    return wrapper


class _DictSubclass(dict):
    pass


class _ListSubclass(list):
    pass


class _SampleEnum(Enum):
    VALUE = "value"


@dataclass
class _SampleDataclass:
    value: int


class _HookMapping(dict):
    def __init__(self) -> None:
        super().__init__({"request_id": "request-1"})
        self.touched = False

    def items(self):
        self.touched = True
        raise KeyboardInterrupt


def test_operations_module_exposes_closed_request_detacher() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")

    source = {"request_id": "req_1", "nested": [1, {"ok": True}]}
    detached = operations._detach_request_body(
        source,
        code="staffing_invalid_request",
    )

    assert detached == source
    assert detached is not source
    assert detached["nested"] is not source["nested"]


@pytest.mark.parametrize(
    "bad",
    [
        {"value": 2**63},
        {"value": -(2**63) - 1},
        {"value": float("inf")},
        {"value": (1, 2)},
        {"value": datetime(2026, 10, 5)},
        {1: "not-a-string-key"},
    ],
)
def test_request_detacher_rejects_nonportable_json(bad: object) -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")

    with pytest.raises(StaffingError) as raised:
        operations._detach_request_body(bad, code="staffing_invalid_request")

    assert raised.value.code == "staffing_invalid_request"


@pytest.mark.parametrize(
    "bad",
    [
        _DictSubclass({"value": 1}),
        {"value": _ListSubclass([1])},
        MappingProxyType({"value": 1}),
        {"value": _SampleDataclass(1)},
        {"value": _SampleEnum.VALUE},
        {"value": {1, 2}},
        {"value": b"bytes"},
        {"value": "\ud800"},
    ],
)
def test_request_detacher_rejects_every_non_exact_json_family(bad: object) -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")

    with pytest.raises(StaffingError) as raised:
        operations._detach_request_body(bad, code="staffing_invalid_request")

    assert raised.value.code == "staffing_invalid_request"


def test_request_detacher_rejects_proxy_before_invoking_hooks() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    hostile = _HookMapping()

    with pytest.raises(StaffingError) as raised:
        operations._detach_request_body(hostile, code="staffing_invalid_request")

    assert raised.value.code == "staffing_invalid_request"
    assert hostile.touched is False


def test_request_detacher_allows_shared_subtrees_but_returns_independent_copies() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    shared = [{"value": 1}]

    detached = operations._detach_request_body(
        {"left": shared, "right": shared}, code="staffing_invalid_request"
    )

    assert detached["left"] == detached["right"]
    assert detached["left"] is not detached["right"]
    assert detached["left"][0] is not detached["right"][0]


def test_request_detacher_rejects_cycles_and_depth_33_but_accepts_depth_32() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    accepted: object = 0
    for _ in range(32):
        accepted = [accepted]
    rejected: object = 0
    for _ in range(33):
        rejected = [rejected]

    assert operations._detach_request_body(
        {"nested": accepted}, code="staffing_invalid_request"
    )
    for value in (cyclic, {"nested": rejected}):
        with pytest.raises(StaffingError) as raised:
            operations._detach_request_body(value, code="staffing_invalid_request")
        assert raised.value.code == "staffing_invalid_request"


def test_request_detacher_occurrence_boundary_is_exact() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    accepted = {"values": [None] * (operations.MAX_REQUEST_OCCURRENCES - 2)}
    rejected = {"values": [None] * (operations.MAX_REQUEST_OCCURRENCES - 1)}

    assert len(
        operations._detach_request_body(
            accepted, code="staffing_invalid_request"
        )["values"]
    ) == operations.MAX_REQUEST_OCCURRENCES - 2
    with pytest.raises(StaffingError) as raised:
        operations._detach_request_body(rejected, code="staffing_invalid_request")
    assert raised.value.code == "staffing_invalid_request"


def test_request_detacher_does_not_swallow_base_exception(monkeypatch) -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    monkeypatch.setattr(operations, "_InvalidRequestTree", KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        operations._detach_request_body(
            {"value": object()}, code="staffing_invalid_request"
        )


def test_request_detacher_normalizes_concurrent_exact_dict_mutation() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    large = [None] * 100
    value = {"large": large, "stable": True}
    traversal_started = threading.Event()
    mutation_finished = threading.Event()

    def mutate() -> None:
        traversal_started.wait()
        value["racing"] = 1
        mutation_finished.set()

    def trace(frame, event, _arg):
        if (
            event == "call"
            and frame.f_code.co_name == "visit"
            and frame.f_locals.get("item") is large
        ):
            traversal_started.set()
            mutation_finished.wait()
        return trace

    worker = threading.Thread(target=mutate)
    worker.start()
    previous_trace = sys.gettrace()
    sys.settrace(trace)
    try:
        with pytest.raises(StaffingError) as raised:
            operations._detach_request_body(
                value, code="staffing_invalid_request"
            )
        assert raised.value.code == "staffing_invalid_request"
    finally:
        sys.settrace(previous_trace)
        traversal_started.set()
        worker.join()


def test_parse_generation_request_closes_and_canonicalizes_the_wire_body() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    payload = {
        "schema": "nxt-staffing-suggestion-generate/v1",
        "request_id": "generation-request-1",
        "operator": "course-manager",
        "service_date": "2026-10-05",
        "expected_revisions": {"roster": 1, "exception_set": 0, "effective_plan": 0},
        "retry_of": None,
    }

    parsed = operations.parse_generation_request(payload)

    assert parsed == payload
    assert parsed is not payload
    assert operations._request_digest("suggestion-generate", parsed) == stable_digest(payload)


def test_manager_digest_binds_the_route_suggestion_id() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    body = {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": "manager-request-1",
        "operator": "course-manager",
        "kind": "REJECT",
        "expected_revisions": {"roster": 1, "exception_set": 0, "effective_plan": 0},
        "candidate_index": None,
        "edited_operations": None,
        "reason_code": "MANUAL_HANDLING",
        "note": "manual fallback",
    }

    request, wire = operations.parse_manager_response(
        body,
        suggestion_id="generation_0123456789abcdef01234567",
    )
    first = operations._request_digest(
        "manager-response",
        wire,
        suggestion_id=request.generation_id,
    )
    second = operations._request_digest(
        "manager-response",
        wire,
        suggestion_id="generation_abcdef0123456789abcdef01",
    )

    assert first != second
    assert request.kind == "REJECT"
    assert request.edited_operations == ()


def test_roster_import_is_ledger_backed_idempotent_and_projectable(tmp_path) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    ledger = StaffingLedger(
        tmp_path / "ledger",
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    operations = operations_module.StaffingOperations(
        ledger,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    request = roster_import_request()

    committed = operations.import_roster(request, recorded_at=NOW)
    duplicate = operations.import_roster(deepcopy(request), recorded_at=NOW)
    changed = deepcopy(request)
    changed["source_ref"] = "changed.csv"
    conflict = operations.import_roster(changed, recorded_at=NOW)
    projection = operations.date_projection(date(2026, 10, 5))

    assert type(committed) is CommittedReceipt
    assert type(duplicate) is DuplicateReceipt
    assert type(conflict) is ConflictReceipt
    assert conflict.code == "IDEMPOTENCY_CONFLICT"
    assert projection.roster_revision == 1
    assert projection.worker_count == 1
    assert projection.assignments[0].display_name == "本地员工甲"


def test_generation_reservation_probe_and_recovery_are_at_most_once(tmp_path) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    ledger = StaffingLedger(
        tmp_path / "ledger",
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    operations = operations_module.StaffingOperations(
        ledger,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    request = {
        "schema": "nxt-staffing-suggestion-generate/v1",
        "request_id": "generation-request-1",
        "operator": "course-manager",
        "service_date": "2026-10-05",
        "expected_revisions": {"roster": 1, "exception_set": 0, "effective_plan": 0},
        "retry_of": None,
    }

    assert operations.probe_request("suggestion-generate", request) is None
    committed = operations.reserve_generation(
        request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    duplicate = operations.probe_request("suggestion-generate", request)

    # The stable generation ID is part of the request projection, not the event ID.
    request_view = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    )
    work = operations.generation_work(request_view.generation_id)
    assert request_view.lifecycle_state == "RESERVED"
    recovered = operations.recover_interrupted_generations(recorded_at=NOW)
    again = operations.interrupt_generation(request_view.generation_id, recorded_at=NOW)

    assert type(committed) is CommittedReceipt
    assert type(duplicate) is DuplicateReceipt
    assert work.basis_snapshot.basis.basis_digest == request_view.generation.basis_digest
    assert len(recovered) == 1
    assert type(recovered[0]) is EventCommit
    assert type(again) is ConflictReceipt
    assert again.code == "INVALID_TRANSITION"


def _operations_with_roster(tmp_path):
    _, operations = _open_operations(tmp_path / "ledger")
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    return operations


def _open_operations(root, *, site_timezone="Asia/Shanghai"):
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    ledger = StaffingLedger(
        root,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    operations = operations_module.StaffingOperations(
        ledger,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone=site_timezone,
    )
    return ledger, operations


def _generation_request(
    *,
    request_id="generation-request-1",
    service_date="2026-10-05",
    retry_of=None,
    revisions=(1, 0, 0),
):
    return {
        "schema": "nxt-staffing-suggestion-generate/v1",
        "request_id": request_id,
        "operator": "course-manager",
        "service_date": service_date,
        "expected_revisions": {
            "roster": revisions[0],
            "exception_set": revisions[1],
            "effective_plan": revisions[2],
        },
        "retry_of": retry_of,
    }


def _reserve(operations):
    request = _generation_request()
    operations.reserve_generation(
        request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    return operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation_id


def _reserve_request(
    operations,
    *,
    request_id,
    retry_of=None,
    alias_nonce=b"fedcba9876543210",
):
    request = _generation_request(request_id=request_id, retry_of=retry_of)
    result = operations.reserve_generation(
        request,
        alias_nonce=alias_nonce,
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    generation_id = operations.request_projection(
        "suggestion-generate", request_id
    ).generation_id
    return request, result, generation_id


def _issue_valid_suggestion(operations, generation_id):
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "keep the covered baseline",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    return operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )


def _successful_evidence(operations, generation_id, output):
    work = operations.generation_work(generation_id)
    output_digest = stable_digest(output)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    started = AttemptStartedEvidence(
        generation_id, 0, route, work.input_digest, 10.0
    )
    finished = AttemptFinishedEvidence(
        generation_id,
        0,
        route,
        "SUCCEEDED",
        None,
        False,
        False,
        None,
        "stop",
        output_digest,
        None,
        None,
    )
    result = ResultEvidence(
        generation_id,
        "SUCCEEDED",
        None,
        "KIMI",
        "kimi-k2",
        None,
        "stop",
        work.input_digest,
        output_digest,
        (finished,),
        1,
        None,
    )
    return started, finished, result


@pytest.mark.parametrize(
    "phase",
    [
        "reservation",
        "attempt-start",
        "outbound-before-finish",
        "attempt-finish",
        "terminal-fsynced",
        "response-lost",
    ],
)
def test_close_reopen_generation_crash_matrix_is_at_most_once(tmp_path, phase) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root)
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    request, _, generation_id = _reserve_request(
        operations,
        request_id="generation-request-1",
        alias_nonce=b"0123456789abcdef",
    )
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "baseline",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    if phase in {
        "attempt-start",
        "outbound-before-finish",
        "attempt-finish",
        "terminal-fsynced",
        "response-lost",
    }:
        operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    if phase in {"attempt-finish", "terminal-fsynced", "response-lost"}:
        operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    if phase in {"terminal-fsynced", "response-lost"}:
        operations.commit_generation_result(
            generation_id, result, output, recorded_at=NOW
        )
    durable_count = operations.ledger.verify()[0]
    durable_event_ids = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).event_ids
    durable_work = operations.generation_work(generation_id)
    ledger.close()

    reopened_ledger, reopened = _open_operations(root)
    reopened_work = reopened.generation_work(generation_id)
    assert to_primitive(reopened_work) == to_primitive(durable_work)
    assert reopened_work is not durable_work
    assert reopened_work.basis_snapshot is not durable_work.basis_snapshot
    assert reopened_work.provider_payload is not durable_work.provider_payload
    recovered = reopened.recover_interrupted_generations(recorded_at=NOW)
    outbound_calls = 0
    if reopened.probe_request("suggestion-generate", request) is None:
        outbound_calls += 1
    view = reopened.request_projection(
        "suggestion-generate", "generation-request-1"
    )
    if phase in {"terminal-fsynced", "response-lost"}:
        assert recovered == ()
        assert reopened.ledger.verify()[0] == durable_count
        assert view.lifecycle_state == "SUCCEEDED"
        assert view.event_ids == durable_event_ids
    else:
        assert len(recovered) == 1
        assert type(recovered[0]) is EventCommit
        assert reopened.recover_interrupted_generations(recorded_at=NOW) == ()
        assert view.lifecycle_state == "RESULT_UNKNOWN"
        retry_request, retry_commit, retry_id = _reserve_request(
            reopened,
            request_id="generation-retry-1",
            retry_of=generation_id,
            alias_nonce=b"retry-alias-nonce",
        )
        retry_duplicate = reopened.reserve_generation(
            retry_request,
            alias_nonce=b"retry-alias-nonce",
            route_evidence=GenerationRouteEvidence(
                "CN", "READY", "KIMI", "kimi-k2", None, None
            ),
            prompt_template_version="staffing-adjustment/v1",
            language="zh-CN",
            recorded_at=NOW,
        )
        assert retry_id != generation_id
        assert type(retry_commit) is CommittedReceipt
        assert type(retry_duplicate) is DuplicateReceipt
    assert type(
        reopened.probe_request("suggestion-generate", request)
    ) is DuplicateReceipt
    assert outbound_calls == 0
    reopened_ledger.close()


def test_reservation_rejects_nonce_reuse_and_illegal_retry_edges_without_append(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    _, _, original_id = _reserve_request(
        operations,
        request_id="generation-original",
        alias_nonce=b"0123456789abcdef",
    )

    invalid_cases = (
        (
            _generation_request(request_id="generation-reused-nonce"),
            b"0123456789abcdef",
        ),
        (
            _generation_request(
                request_id="generation-unknown-parent",
                retry_of="generation_ffffffffffffffffffffffff",
            ),
            b"unknown-parent-01",
        ),
    )
    for request, nonce in invalid_cases:
        before = operations.ledger.verify()
        with pytest.raises(StaffingError) as raised:
            operations.reserve_generation(
                request,
                alias_nonce=nonce,
                route_evidence=GenerationRouteEvidence(
                    "CN", "READY", "KIMI", "kimi-k2", None, None
                ),
                prompt_template_version="staffing-adjustment/v1",
                language="zh-CN",
                recorded_at=NOW,
            )
        assert raised.value.code == "staffing_invalid_event"
        assert operations.ledger.verify() == before

    operations.interrupt_generation(original_id, recorded_at=NOW)
    _reserve_request(
        operations,
        request_id="generation-first-child",
        retry_of=original_id,
        alias_nonce=b"retry-child-one1",
    )
    branch = _generation_request(
        request_id="generation-second-child", retry_of=original_id
    )
    before_branch = operations.ledger.verify()
    with pytest.raises(StaffingError) as branch_error:
        operations.reserve_generation(
            branch,
            alias_nonce=b"retry-child-two2",
            route_evidence=GenerationRouteEvidence(
                "CN", "READY", "KIMI", "kimi-k2", None, None
            ),
            prompt_template_version="staffing-adjustment/v1",
            language="zh-CN",
            recorded_at=NOW,
        )
    assert branch_error.value.code == "staffing_invalid_event"
    assert operations.ledger.verify() == before_branch

    _, _, successful_id = _reserve_request(
        operations,
        request_id="generation-successful-parent",
        alias_nonce=b"successful-origin",
    )
    _issue_valid_suggestion(operations, successful_id)
    terminal_retry = _generation_request(
        request_id="generation-terminal-retry", retry_of=successful_id
    )
    before_terminal_retry = operations.ledger.verify()
    with pytest.raises(StaffingError) as terminal_error:
        operations.reserve_generation(
            terminal_retry,
            alias_nonce=b"successful-retry1",
            route_evidence=GenerationRouteEvidence(
                "CN", "READY", "KIMI", "kimi-k2", None, None
            ),
            prompt_template_version="staffing-adjustment/v1",
            language="zh-CN",
            recorded_at=NOW,
        )
    assert terminal_error.value.code == "staffing_invalid_event"
    assert operations.ledger.verify() == before_terminal_retry


def _commit_success_output(tmp_path, output, *, digest_source=None, candidate_count=1):
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    digest = stable_digest(output if digest_source is None else digest_source)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    started = AttemptStartedEvidence(
        generation_id, 0, route, work.input_digest, 10.0
    )
    finished = AttemptFinishedEvidence(
        generation_id,
        0,
        route,
        "SUCCEEDED",
        None,
        False,
        False,
        None,
        "stop",
        digest,
        None,
        None,
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    result = ResultEvidence(
        generation_id,
        "SUCCEEDED",
        None,
        "KIMI",
        "kimi-k2",
        None,
        "stop",
        work.input_digest,
        digest,
        (finished,),
        candidate_count,
        None,
    )
    commit = operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )
    view = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation
    return operations, generation_id, work, commit, view


class _OnceMapping(Mapping):
    def __init__(self, source):
        self.source = source
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise RuntimeError("second traversal")
        return iter(self.source)

    def __len__(self):
        return len(self.source)

    def __getitem__(self, key):
        return self.source[key]

    def items(self):
        for key in self:
            yield key, self[key]


class _ExplodingMapping(Mapping):
    def __init__(self, mode):
        self.mode = mode

    def __iter__(self):
        if self.mode == "iter":
            raise StaffingError("provider_sensitive_key", "SECRET")
        return iter(("candidates",))

    def __len__(self):
        if self.mode == "len":
            raise RuntimeError("SECRET length")
        return 1

    def __getitem__(self, key):
        if self.mode == "get":
            raise RuntimeError("SECRET get")
        return []

    def items(self):
        if self.mode == "items":
            raise RuntimeError("SECRET items")
        if self.mode == "len":
            len(self)
        return super().items()


class _DuplicateKeyMapping(Mapping):
    def __iter__(self):
        return iter(("candidates",))

    def __len__(self):
        return 2

    def __getitem__(self, key):
        return []

    def items(self):
        return iter((("candidates", []), ("candidates", [])))


def test_result_output_is_read_once_and_only_detached_tree_is_used(tmp_path) -> None:
    source = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "baseline",
                "operational_warnings": [],
            }
        ]
    }
    backing = _OnceMapping(source)
    proxy = MappingProxyType(backing)

    _, _, _, _, view = _commit_success_output(
        tmp_path, proxy, digest_source=source
    )

    assert backing.iterations == 1
    assert view.lifecycle_state == "SUCCEEDED"
    assert view.candidates[0].rationale == "baseline"


def test_oversized_provider_tree_persists_defensive_invalid_response(tmp_path) -> None:
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "baseline",
                "operational_warnings": [""] * 4097,
            }
        ]
    }

    operations, _, _, commit, view = _commit_success_output(tmp_path, output)

    assert type(commit) is EventCommit
    assert view.lifecycle_state == "INVALID_RESPONSE"
    assert view.failure_code == "invalid_provider_shape"
    assert view.candidates == ()
    assert operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).result_evidence.status == "SUCCEEDED"


def test_over_4096_operation_nodes_remain_provider_wire_invalid_response(tmp_path) -> None:
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "REMOVE",
                        "assignment_alias": "assignment_untrusted",
                    }
                    for _ in range(1400)
                ],
                "rationale": "oversized",
                "operational_warnings": [],
            }
        ]
    }

    _, _, _, commit, view = _commit_success_output(tmp_path, output)

    assert type(commit) is EventCommit
    assert view.lifecycle_state == "INVALID_RESPONSE"
    assert view.failure_code == "invalid_provider_shape"


def test_two_candidate_terminal_keeps_individual_and_ordered_set_digests(tmp_path) -> None:
    output = {
        "candidates": [
            {
                "candidate_index": index,
                "operations": [],
                "rationale": f"candidate {index}",
                "operational_warnings": [],
            }
            for index in (1, 2)
        ]
    }

    operations, _, _, _, view = _commit_success_output(
        tmp_path, output, candidate_count=2
    )
    terminal = operations.ledger.read().events[-1].payload

    assert len(view.candidates) == 2
    assert all(candidate.materialized_schedule_digest for candidate in view.candidates)
    assert terminal.candidate_set_digest == stable_digest(
        to_primitive(terminal.candidates)
    )
    assert (
        terminal.candidates[0].materialized_schedule_digest
        == terminal.candidates[1].materialized_schedule_digest
    )


def _nested_result(levels):
    value = None
    for _ in range(levels - 1):
        value = [value]
    return {"x": value}


def test_result_evidence_depth_and_occurrence_boundaries_are_exact() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")

    assert operations._detach_result_output(_nested_result(20))
    with pytest.raises(StaffingError) as depth_error:
        operations._detach_result_output(_nested_result(21))
    assert depth_error.value.code == "staffing_invalid_evidence"
    assert depth_error.value.detail == "decoded_output"

    accepted = operations._detach_result_output(
        {"x": [None] * (operations.MAX_RESULT_EVIDENCE_OCCURRENCES - 2)}
    )
    assert len(accepted["x"]) == operations.MAX_RESULT_EVIDENCE_OCCURRENCES - 2
    with pytest.raises(StaffingError) as count_error:
        operations._detach_result_output(
            {"x": [None] * (operations.MAX_RESULT_EVIDENCE_OCCURRENCES - 1)}
        )
    assert count_error.value.code == "staffing_invalid_evidence"
    assert count_error.value.detail == "decoded_output"


@pytest.mark.parametrize("mode", ["iter", "items", "get", "len"])
def test_result_evidence_hostile_mapping_is_a_fixed_local_error(mode) -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")

    with pytest.raises(StaffingError) as raised:
        operations._detach_result_output(
            MappingProxyType(_ExplodingMapping(mode))
        )

    assert type(raised.value) is StaffingError
    assert str(raised.value) == "staffing_invalid_evidence: decoded_output"
    assert "SECRET" not in str(raised.value)


def test_result_evidence_rejects_cycle_unsupported_and_duplicate_keys() -> None:
    operations = importlib.import_module("nxt_pilot_ops.staffing.operations")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    for value in (
        cyclic,
        {"x": object()},
        MappingProxyType(_DuplicateKeyMapping()),
    ):
        with pytest.raises(StaffingError) as raised:
            operations._detach_result_output(value)
        assert raised.value.code == "staffing_invalid_evidence"


def test_result_evidence_cannot_bind_a_digest_from_another_output(tmp_path) -> None:
    evidence_source = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "A",
                "operational_warnings": [],
            }
        ]
    }
    supplied = deepcopy(evidence_source)
    supplied["candidates"][0]["rationale"] = "B"
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    digest = stable_digest(evidence_source)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    started = AttemptStartedEvidence(
        generation_id, 0, route, work.input_digest, 10.0
    )
    finished = AttemptFinishedEvidence(
        generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", digest, None, None,
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    result = ResultEvidence(
        generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", work.input_digest, digest, (finished,), 1, None,
    )
    before = operations.ledger.verify()

    with pytest.raises(StaffingError) as raised:
        operations.commit_generation_result(
            generation_id, result, supplied, recorded_at=NOW
        )

    assert raised.value.code == "staffing_invalid_evidence"
    assert operations.ledger.verify() == before


def test_result_occurrence_overflow_is_local_and_appends_no_terminal(tmp_path) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    output = {
        "candidates": [],
        "padding": [None] * (operations_module.MAX_RESULT_EVIDENCE_OCCURRENCES - 2),
    }
    digest = stable_digest(output)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    started = AttemptStartedEvidence(
        generation_id, 0, route, work.input_digest, 10.0
    )
    finished = AttemptFinishedEvidence(
        generation_id, 0, route, "SUCCEEDED", None, False, False,
        None, "stop", digest, None, None,
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    result = ResultEvidence(
        generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None,
        "stop", work.input_digest, digest, (finished,), 0, None,
    )
    before = operations.ledger.verify()

    with pytest.raises(StaffingError) as raised:
        operations.commit_generation_result(
            generation_id, result, output, recorded_at=NOW
        )

    assert raised.value.code == "staffing_invalid_evidence"
    assert operations.ledger.verify() == before


def test_failed_result_rejects_non_null_decoded_output_without_append(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    result = ResultEvidence(
        generation_id,
        "UNAVAILABLE",
        "DEADLINE_EXHAUSTED",
        None,
        None,
        None,
        None,
        work.input_digest,
        None,
        (),
        0,
        None,
    )
    before = operations.ledger.verify()

    with pytest.raises(StaffingError) as raised:
        operations.commit_generation_result(
            generation_id, result, {"candidates": []}, recorded_at=NOW
        )

    assert raised.value.code == "staffing_invalid_evidence"
    assert operations.ledger.verify() == before


@pytest.mark.parametrize(
    "status,failure_code,retryable,security_failure",
    [
        ("UNAVAILABLE", "DNS_FAILURE", True, False),
        ("REFUSED", "PROVIDER_REFUSED", False, False),
        ("INVALID_RESPONSE", "MALFORMED_PROVIDER_RESPONSE", False, False),
        ("PROVIDER_ERROR", "PROVIDER_CLIENT_ERROR", False, False),
        ("CONFIGURATION_ERROR", "AUTHENTICATION_FAILED", False, False),
        ("SECURITY_ERROR", "RESPONSE_TOO_LARGE", False, True),
    ],
)
def test_each_gateway_terminal_is_durably_wired_through_operations(
    tmp_path, status, failure_code, retryable, security_failure
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    started = AttemptStartedEvidence(
        generation_id, 0, route, work.input_digest, 10.0
    )
    finished = AttemptFinishedEvidence(
        generation_id,
        0,
        route,
        status,
        failure_code,
        retryable,
        security_failure,
        None,
        None,
        None,
        None,
        None,
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    result = ResultEvidence(
        generation_id,
        status,
        failure_code,
        "KIMI",
        "kimi-k2",
        None,
        None,
        work.input_digest,
        None,
        (finished,),
        0,
        None,
    )

    commit = operations.commit_generation_result(
        generation_id, result, None, recorded_at=NOW
    )
    view = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation

    assert type(commit) is EventCommit
    assert view.lifecycle_state == status
    assert view.failure_code == failure_code
    assert view.candidates == ()


@pytest.mark.parametrize(
    "status,failure_code",
    [
        ("CONFIGURATION_ERROR", "INPUT_TOO_LARGE"),
        ("UNAVAILABLE", "DEADLINE_EXHAUSTED"),
    ],
)
def test_zero_attempt_local_terminal_requires_null_output(
    tmp_path, status, failure_code
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    result = ResultEvidence(
        generation_id,
        status,
        failure_code,
        None,
        None,
        None,
        None,
        work.input_digest,
        None,
        (),
        0,
        None,
    )

    operations.commit_generation_result(
        generation_id, result, None, recorded_at=NOW
    )
    view = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation
    assert view.lifecycle_state == status
    assert view.failure_code == failure_code


def test_unavailable_route_commits_provider_unconfigured_without_an_attempt(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    operations.reserve_generation(
        _generation_request(),
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "UNAVAILABLE", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    projection = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    )
    generation_id = projection.generation_id
    work = operations.generation_work(generation_id)
    result = ResultEvidence(
        generation_id,
        "CONFIGURATION_ERROR",
        "PROVIDER_UNCONFIGURED",
        None,
        None,
        None,
        None,
        work.input_digest,
        None,
        (),
        0,
        None,
    )

    commit = operations.commit_generation_result(
        generation_id, result, None, recorded_at=NOW
    )
    terminal = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    )

    assert type(commit) is EventCommit
    assert terminal.lifecycle_state == "CONFIGURATION_ERROR"
    assert terminal.generation.failure_code == "PROVIDER_UNCONFIGURED"
    assert terminal.attempt_evidence == ()
    assert terminal.result_evidence.attempts == ()
    assert terminal.candidates == ()


def test_success_with_only_invalid_candidates_persists_no_valid_terminal_and_digests(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    assignment_alias = work.assignment_alias_to_assignment_id[0][0]
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "REMOVE",
                        "assignment_alias": assignment_alias,
                    }
                ],
                "rationale": "leave the range uncovered",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )
    history = operations.ledger.read()
    terminal = history.events[-1].payload
    view = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation

    assert view.lifecycle_state == "NO_VALID_SUGGESTION"
    assert view.failure_code == "NO_VALID_SUGGESTION"
    assert view.candidates[0].valid is False
    assert "COVERAGE_GAP" in view.candidates[0].rejection_codes
    assert terminal.candidate_set_digest == stable_digest(
        to_primitive(terminal.candidates)
    )


def test_attempt_order_route_timeout_and_duplicate_terminal_fail_closed(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [],
                "rationale": "baseline",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    before = operations.ledger.verify()
    with pytest.raises(StaffingError) as finish_error:
        operations.record_attempt_finished(
            generation_id, finished, recorded_at=NOW
        )
    assert finish_error.value.code == "staffing_invalid_event"
    assert operations.ledger.verify() == before

    wrong_route = replace(
        started,
        route=AttemptRouteEvidence(
            "KIMI", "CN", "PRIMARY", "wrong-route", "kimi-k2"
        ),
    )
    too_slow = replace(started, timeout_s=15.1)
    for bad in (wrong_route, too_slow):
        with pytest.raises(StaffingError) as raised:
            operations.record_attempt_started(
                generation_id, bad, recorded_at=NOW
            )
        assert raised.value.code == "staffing_invalid_evidence"

    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    assert operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).lifecycle_state == "IN_PROGRESS"
    with pytest.raises(StaffingError) as order_error:
        operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    assert order_error.value.code == "staffing_invalid_event"
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    assert operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).lifecycle_state == "IN_PROGRESS"
    operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )
    terminal_count = operations.ledger.verify()[0]
    with pytest.raises(StaffingError) as terminal_error:
        operations.commit_generation_result(
            generation_id, result, output, recorded_at=NOW
        )
    assert terminal_error.value.code == "staffing_invalid_event"
    assert operations.ledger.verify()[0] == terminal_count


def test_successful_result_and_manager_accept_survive_restart(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)

    terminal = _issue_valid_suggestion(operations, generation_id)
    manager = operations.commit_manager_response(
        {
            "schema": "nxt-staffing-manager-response/v1",
            "request_id": "manager-request-1",
            "operator": "course-manager",
            "kind": "ACCEPT",
            "expected_revisions": {
                "roster": 1,
                "exception_set": 0,
                "effective_plan": 0,
            },
            "candidate_index": 1,
            "edited_operations": None,
            "reason_code": "APPROVED",
            "note": "approved",
        },
        suggestion_id=generation_id,
        recorded_at=NOW,
    )
    projection = operations.date_projection(date(2026, 10, 5))
    request = operations.request_projection("manager-response", "manager-request-1")
    terminal_payload = next(
        event.payload
        for event in operations.ledger.read().events
        if event.event_type == "suggestion_issued"
    )

    assert type(terminal) is EventCommit
    assert type(manager) is CommittedReceipt
    assert projection.effective_plan.revision == 1
    assert projection.effective_plan.status == "CURRENT"
    assert projection.generations[0].candidates[0].valid is True
    assert request.lifecycle_state == "COMMITTED"
    assert request.manager_response.decision == "ACCEPT"
    assert terminal_payload.candidate_set_digest == stable_digest(
        to_primitive(terminal_payload.candidates)
    )
    assert terminal_payload.candidates[0].materialized_schedule_digest


def test_attempt_argument_cannot_write_evidence_for_another_generation(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    work = operations.generation_work(generation_id)
    route = AttemptRouteEvidence(
        "KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2"
    )
    mismatched = AttemptStartedEvidence(
        "generation_aaaaaaaaaaaaaaaaaaaaaaaa", 0, route, work.input_digest, 10.0
    )

    with pytest.raises(StaffingError) as raised:
        operations.record_attempt_started(
            generation_id, mismatched, recorded_at=NOW
        )

    assert raised.value.code == "staffing_invalid_evidence"
    assert operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).started_attempts == ()


def _exception_request(*, request_id="exception-request-1", expected_revision=0):
    return {
        "schema": "nxt-staffing-exception/v1",
        "request_id": request_id,
        "expected_roster_revision": 1,
        "expected_exception_set_revision": expected_revision,
        "service_date": "2026-10-05",
        "staff_id": "staff-001",
        "kind": "LATE",
        "time_local": "10:00",
        "operator": "course-manager",
        "note": "called ahead",
    }


def test_exception_record_and_cancel_use_locked_revision_cas(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    committed = operations.record_exception(_exception_request(), recorded_at=NOW)
    record = operations.request_projection("exception-record", "exception-request-1")
    exception_id = record.committed_record.exception_id

    stale = operations.cancel_exception(
        {
            "schema": "nxt-staffing-exception-cancel/v1",
            "request_id": "cancel-request-stale",
            "exception_id": exception_id,
            "expected_exception_set_revision": 0,
            "operator": "course-manager",
            "note": None,
        },
        recorded_at=NOW,
    )
    cancelled = operations.cancel_exception(
        {
            "schema": "nxt-staffing-exception-cancel/v1",
            "request_id": "cancel-request-1",
            "exception_id": exception_id,
            "expected_exception_set_revision": 1,
            "operator": "course-manager",
            "note": "returned",
        },
        recorded_at=NOW,
    )

    assert type(committed) is CommittedReceipt
    assert type(stale) is ConflictReceipt
    assert stale.code == "STALE_REQUEST"
    assert type(cancelled) is CommittedReceipt
    assert operations.date_projection(date(2026, 10, 5)).exceptions == ()


def test_manager_basis_drift_and_request_revision_staleness_are_distinct(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    mismatches = {
        "roster": (0, 0, 0),
        "exception-set": (1, 1, 0),
        "effective-plan": (1, 0, 1),
    }
    for name, revisions in mismatches.items():
        stale_body = _accept_body(
            request_id=f"manager-stale-{name}", revisions=revisions
        )
        before = operations.ledger.verify()
        stale_request = operations.commit_manager_response(
            stale_body, suggestion_id=generation_id, recorded_at=NOW
        )
        assert type(stale_request) is ConflictReceipt
        assert stale_request.code == "STALE_REQUEST"
        assert operations.ledger.verify() == before

    operations.record_exception(_exception_request(), recorded_at=NOW)
    drifted = _accept_body(
        request_id="manager-stale-suggestion", revisions=(1, 1, 0)
    )
    stale_suggestion = operations.commit_manager_response(
        drifted, suggestion_id=generation_id, recorded_at=NOW
    )

    assert type(stale_suggestion) is ConflictReceipt
    assert stale_suggestion.code == "STALE_SUGGESTION"


def test_operations_owner_identity_must_match_the_ledger(tmp_path) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    ledger = StaffingLedger(
        tmp_path / "ledger",
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )

    with pytest.raises(StaffingError) as raised:
        operations_module.StaffingOperations(
            ledger,
            site_id="another-site",
            deployment_id=DEPLOYMENT_ID,
            site_timezone="Asia/Shanghai",
        )

    assert raised.value.code == "staffing_identity_mismatch"


def test_nonportable_requests_fail_before_ledger_and_never_become_duplicates(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    initial_count, initial_hash = operations.ledger.verify()
    invalid_roster = roster_import_request()
    invalid_roster["source_ref"] = b"not-json"
    invalid_generation = {
        "schema": "nxt-staffing-suggestion-generate/v1",
        "request_id": "generation-invalid-json",
        "operator": "course-manager",
        "service_date": "2026-10-05",
        "expected_revisions": {
            "roster": 2**63,
            "exception_set": 0,
            "effective_plan": 0,
        },
        "retry_of": None,
    }
    invalid_manager = {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": "manager-invalid-json",
        "operator": "course-manager",
        "kind": "REJECT",
        "expected_revisions": {
            "roster": 1,
            "exception_set": 0,
            "effective_plan": 0,
        },
        "candidate_index": None,
        "edited_operations": None,
        "reason_code": "MANUAL_HANDLING",
        "note": _SampleEnum.VALUE,
    }

    calls = (
        lambda: operations.import_roster(invalid_roster, recorded_at=NOW),
        lambda: operations.reserve_generation(
            invalid_generation,
            alias_nonce=b"0123456789abcdef",
            route_evidence=GenerationRouteEvidence(
                "CN", "READY", "KIMI", "kimi-k2", None, None
            ),
            prompt_template_version="staffing-adjustment/v1",
            language="zh-CN",
            recorded_at=NOW,
        ),
        lambda: operations.commit_manager_response(
            invalid_manager,
            suggestion_id="generation_0123456789abcdef01234567",
            recorded_at=NOW,
        ),
    )
    for call in calls:
        with pytest.raises(StaffingError):
            call()
        assert operations.ledger.verify() == (initial_count, initial_hash)


def _operation_case(tmp_path, kind):
    if kind == "roster-import":
        _, operations = _open_operations(tmp_path / "ledger")
        payload = roster_import_request()
        invoke = lambda body: operations.import_roster(body, recorded_at=NOW)
    else:
        operations = _operations_with_roster(tmp_path)
        if kind == "exception-record":
            payload = _exception_request()
            invoke = lambda body: operations.record_exception(body, recorded_at=NOW)
        elif kind in {"exception-cancel", "exception-correct"}:
            operations.record_exception(_exception_request(), recorded_at=NOW)
            exception_id = operations.request_projection(
                "exception-record", "exception-request-1"
            ).committed_record.exception_id
            if kind == "exception-cancel":
                payload = {
                    "schema": "nxt-staffing-exception-cancel/v1",
                    "request_id": "cancel-request-1",
                    "exception_id": exception_id,
                    "expected_exception_set_revision": 1,
                    "operator": "course-manager",
                    "note": "returned",
                }
                invoke = lambda body: operations.cancel_exception(
                    body, recorded_at=NOW
                )
            else:
                payload = {
                    "schema": "nxt-staffing-exception-correct/v1",
                    "request_id": "correct-request-1",
                    "exception_id": exception_id,
                    "expected_exception_set_revision": 1,
                    "operator": "course-manager",
                    "replacement": {
                        "kind": "EARLY_DEPARTURE",
                        "time_local": "15:00",
                        "note": "changed",
                    },
                }
                invoke = lambda body: operations.correct_exception(
                    body, recorded_at=NOW
                )
        elif kind == "suggestion-generate":
            payload = _generation_request()
            invoke = lambda body: operations.reserve_generation(
                body,
                alias_nonce=b"0123456789abcdef",
                route_evidence=GenerationRouteEvidence(
                    "CN", "READY", "KIMI", "kimi-k2", None, None
                ),
                prompt_template_version="staffing-adjustment/v1",
                language="zh-CN",
                recorded_at=NOW,
            )
        else:
            generation_id = _reserve(operations)
            _issue_valid_suggestion(operations, generation_id)
            payload = {
                "schema": "nxt-staffing-manager-response/v1",
                "request_id": "manager-request-1",
                "operator": "course-manager",
                "kind": "ACCEPT",
                "expected_revisions": {
                    "roster": 1,
                    "exception_set": 0,
                    "effective_plan": 0,
                },
                "candidate_index": 1,
                "edited_operations": None,
                "reason_code": "APPROVED",
                "note": None,
            }
            invoke = lambda body: operations.commit_manager_response(
                body, suggestion_id=generation_id, recorded_at=NOW
            )
    return operations, payload, invoke


@pytest.mark.parametrize("kind", sorted(OPERATION_KINDS))
def test_all_operation_kinds_are_atomic_idempotent_and_conflict_on_drift(
    tmp_path, kind
) -> None:
    operations, payload, invoke = _operation_case(tmp_path, kind)
    before = operations.ledger.verify()[0]
    barrier = threading.Barrier(3)

    def submit():
        barrier.wait()
        return invoke(deepcopy(payload))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(submit), pool.submit(submit))
        barrier.wait()
        results = tuple(future.result() for future in futures)

    committed = next(item for item in results if type(item) is CommittedReceipt)
    duplicate = next(item for item in results if type(item) is DuplicateReceipt)
    assert operations.ledger.verify()[0] == before + 1
    assert committed.receipt.event_id == duplicate.receipt.event_id
    assert committed.receipt.sequence == duplicate.receipt.sequence
    assert committed.receipt.record_hash == duplicate.receipt.record_hash
    changed = deepcopy(payload)
    changed["operator"] = "another-manager"
    count = operations.ledger.verify()[0]
    conflict = invoke(changed)
    assert type(conflict) is ConflictReceipt
    assert conflict.code == "IDEMPOTENCY_CONFLICT"
    assert operations.ledger.verify()[0] == count


def test_same_request_id_is_independent_across_operation_namespaces(tmp_path) -> None:
    _, operations = _open_operations(tmp_path / "ledger")
    roster = roster_import_request()
    roster["request_id"] = "shared-request-id"
    exception = _exception_request(request_id="shared-request-id")

    roster_result = operations.import_roster(roster, recorded_at=NOW)
    exception_result = operations.record_exception(exception, recorded_at=NOW)

    assert type(roster_result) is CommittedReceipt
    assert type(exception_result) is CommittedReceipt
    assert roster_result.receipt.operation_kind == "roster-import"
    assert exception_result.receipt.operation_kind == "exception-record"


@pytest.mark.parametrize(
    "kind",
    [
        "roster-import",
        "exception-record",
        "exception-cancel",
        "exception-correct",
    ],
)
def test_concurrent_distinct_requests_share_one_locked_revision_cas(tmp_path, kind) -> None:
    if kind == "roster-import":
        _, operations = _open_operations(tmp_path / "ledger")
        first = roster_import_request()
        second = deepcopy(first)
        second["request_id"] = "roster-competitor"
        second["source_ref"] = "competitor.csv"
        invoke = lambda body: operations.import_roster(body, recorded_at=NOW)
    elif kind == "exception-record":
        operations = _operations_with_roster(tmp_path)
        first = _exception_request(request_id="exception-first")
        second = _exception_request(request_id="exception-second")
        invoke = lambda body: operations.record_exception(body, recorded_at=NOW)
    else:
        operations = _operations_with_roster(tmp_path)
        operations.record_exception(_exception_request(), recorded_at=NOW)
        exception_id = operations.request_projection(
            "exception-record", "exception-request-1"
        ).committed_record.exception_id
        if kind == "exception-cancel":
            first = {
                "schema": "nxt-staffing-exception-cancel/v1",
                "request_id": "cancel-first",
                "exception_id": exception_id,
                "expected_exception_set_revision": 1,
                "operator": "course-manager",
                "note": "returned",
            }
            second = deepcopy(first)
            second["request_id"] = "cancel-second"
            second["note"] = "covered by another employee"
            invoke = lambda body: operations.cancel_exception(
                body, recorded_at=NOW
            )
        else:
            first = {
                "schema": "nxt-staffing-exception-correct/v1",
                "request_id": "correct-first",
                "exception_id": exception_id,
                "expected_exception_set_revision": 1,
                "operator": "course-manager",
                "replacement": {
                    "kind": "EARLY_DEPARTURE",
                    "time_local": "15:00",
                    "note": "changed",
                },
            }
            second = deepcopy(first)
            second["request_id"] = "correct-second"
            second["replacement"]["time_local"] = "14:30"
            invoke = lambda body: operations.correct_exception(
                body, recorded_at=NOW
            )
    barrier = threading.Barrier(3)

    def submit(body):
        barrier.wait()
        return invoke(body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(submit, first), pool.submit(submit, second))
        barrier.wait()
        results = tuple(future.result() for future in futures)

    assert sum(type(item) is CommittedReceipt for item in results) == 1
    conflicts = tuple(item for item in results if type(item) is ConflictReceipt)
    assert len(conflicts) == 1
    assert conflicts[0].code == "STALE_REQUEST"


def test_probe_conflict_and_toctou_are_rechecked_under_append_lock(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    request = _generation_request()
    assert operations.probe_request("suggestion-generate", request) is None

    operations.record_exception(_exception_request(), recorded_at=NOW)
    stale = operations.reserve_generation(
        request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    assert type(stale) is ConflictReceipt
    assert stale.code == "STALE_REQUEST"

    fresh = _generation_request(
        request_id="generation-request-2", revisions=(1, 1, 0)
    )
    operations.reserve_generation(
        fresh,
        alias_nonce=b"fedcba9876543210",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    changed = deepcopy(fresh)
    changed["operator"] = "another-manager"
    conflict = operations.probe_request("suggestion-generate", changed)
    assert type(conflict) is ConflictReceipt
    assert conflict.code == "IDEMPOTENCY_CONFLICT"


def _accept_body(*, request_id="manager-request-1", revisions=(1, 0, 0)):
    return {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": request_id,
        "operator": "course-manager",
        "kind": "ACCEPT",
        "expected_revisions": {
            "roster": revisions[0],
            "exception_set": revisions[1],
            "effective_plan": revisions[2],
        },
        "candidate_index": 1,
        "edited_operations": None,
        "reason_code": "APPROVED",
        "note": None,
    }


def _reject_body(*, request_id="manager-reject-1"):
    body = _accept_body(request_id=request_id)
    body.update(
        {
            "kind": "REJECT",
            "candidate_index": None,
            "reason_code": "MANUAL_HANDLING",
        }
    )
    return body


def _modify_body(operations, *, request_id="manager-modify-1"):
    projection = operations.date_projection(date(2026, 10, 5))
    assignment = projection.assignments[0]
    return {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": request_id,
        "operator": "course-manager",
        "kind": "MODIFY",
        "expected_revisions": {
            "roster": 1,
            "exception_set": 0,
            "effective_plan": 0,
        },
        "candidate_index": 1,
        "edited_operations": [
            {
                "operation": "REMOVE",
                "assignment_id": assignment.assignment_id,
            },
            {
                "operation": "ADD",
                "staff_id": assignment.staff_id,
                "role_code": assignment.role_code,
                "area_code": assignment.area_code,
                "start_at": "2026-10-05T09:00:00+08:00",
                "end_at": "2026-10-05T17:00:00+08:00",
            },
        ],
        "reason_code": "APPROVED_WITH_CHANGES",
        "note": "manager adjustment",
    }


def test_future_effective_roster_stales_an_older_date_suggestion(tmp_path) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    future = roster_import_request()
    future.update(
        {
            "request_id": "future-roster",
            "expected_roster_revision": 1,
            "effective_from_local_date": "2026-10-06",
            "source_ref": "future.csv",
        }
    )
    operations.import_roster(future, recorded_at=NOW)

    result = operations.commit_manager_response(
        _accept_body(), suggestion_id=generation_id, recorded_at=NOW
    )

    assert operations.date_projection(date(2026, 10, 5)).roster_revision == 1
    assert type(result) is ConflictReceipt
    assert result.code == "STALE_SUGGESTION"


@pytest.mark.parametrize("decision", ["MODIFY", "REJECT"])
def test_manager_modify_and_reject_persist_closed_composite_events(
    tmp_path, decision
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    body = _modify_body(operations) if decision == "MODIFY" else _reject_body()

    result = operations.commit_manager_response(
        body, suggestion_id=generation_id, recorded_at=NOW
    )
    request = operations.request_projection(
        "manager-response", body["request_id"]
    )

    assert type(result) is CommittedReceipt
    assert request.manager_response.decision == decision
    assert request.committed_record.decision == decision
    if decision == "MODIFY":
        assert request.manager_response.effective_plan_revision == 1
        assert request.manager_response.effective_schedule
        assert request.committed_record.schedule_digest
    else:
        assert request.manager_response.effective_plan_revision is None
        assert request.manager_response.effective_schedule is None
        assert request.committed_record.schedule_digest is None


def test_date_projection_keeps_roster_assignments_separate_from_effective_plan(
    tmp_path,
) -> None:
    _, operations = _open_operations(tmp_path / "ledger")
    roster = roster_import_request()
    replacement = deepcopy(roster["workers"][0])
    replacement.update(staff_id="staff-002", display_name="本地员工乙")
    roster["workers"].append(replacement)
    availability = deepcopy(roster["availability"][0])
    availability["staff_id"] = "staff-002"
    roster["availability"].append(availability)
    operations.import_roster(roster, recorded_at=NOW)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    response = _modify_body(operations)
    response["edited_operations"][1]["staff_id"] = "staff-002"

    committed = operations.commit_manager_response(
        response, suggestion_id=generation_id, recorded_at=NOW
    )
    projection = operations.date_projection(date(2026, 10, 5))

    assert type(committed) is CommittedReceipt
    assert [row.staff_id for row in projection.assignments] == ["staff-001"]
    assert projection.effective_plan_schedule is not None
    assert [row.staff_id for row in projection.effective_plan_schedule] == [
        "staff-002"
    ]

    substitute_exception = _exception_request(
        request_id="exception-for-plan-only-worker"
    )
    substitute_exception["staff_id"] = "staff-002"
    with pytest.raises(StaffingError) as raised:
        operations.record_exception(substitute_exception, recorded_at=NOW)
    assert raised.value.code == "unknown_staff_or_shift"

    regular_exception = operations.record_exception(
        _exception_request(request_id="exception-for-roster-worker"),
        recorded_at=NOW,
    )
    after_exception = operations.date_projection(date(2026, 10, 5))
    assert type(regular_exception) is CommittedReceipt
    assert [row.staff_id for row in after_exception.assignments] == ["staff-001"]
    assert [row.staff_id for row in after_exception.exceptions] == ["staff-001"]
    assert after_exception.assignments[0].start_at.isoformat() == (
        "2026-10-05T01:00:00+00:00"
    )
    assert after_exception.assignments[0].end_at.isoformat() == (
        "2026-10-05T09:00:00+00:00"
    )


def test_manager_early_missing_candidate_and_unknown_alias_are_lifecycle_conflicts(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    early = operations.commit_manager_response(
        _accept_body(request_id="manager-early"),
        suggestion_id=generation_id,
        recorded_at=NOW,
    )
    assert type(early) is ConflictReceipt
    assert early.code == "INVALID_TRANSITION"

    _issue_valid_suggestion(operations, generation_id)
    missing = _accept_body(request_id="manager-missing")
    missing["candidate_index"] = 2
    missing_result = operations.commit_manager_response(
        missing, suggestion_id=generation_id, recorded_at=NOW
    )
    unknown = _modify_body(operations, request_id="manager-unknown-alias")
    unknown["edited_operations"][0]["assignment_id"] = "unknown-assignment"
    unknown_result = operations.commit_manager_response(
        unknown, suggestion_id=generation_id, recorded_at=NOW
    )

    assert type(missing_result) is ConflictReceipt
    assert missing_result.code == "INVALID_TRANSITION"
    assert type(unknown_result) is ConflictReceipt
    assert unknown_result.code == "INVALID_TRANSITION"


def test_two_distinct_manager_responses_race_to_one_composite_winner(tmp_path) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root)
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    bodies = (
        _accept_body(request_id="manager-race-a"),
        _accept_body(request_id="manager-race-b"),
    )
    barrier = threading.Barrier(3)

    def submit(body):
        barrier.wait()
        return operations.commit_manager_response(
            body, suggestion_id=generation_id, recorded_at=NOW
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = tuple(pool.submit(submit, deepcopy(body)) for body in bodies)
        barrier.wait()
        results = tuple(future.result() for future in futures)
    winner_index = next(
        index for index, result in enumerate(results) if type(result) is CommittedReceipt
    )
    loser_index = 1 - winner_index
    assert type(results[loser_index]) is ConflictReceipt
    assert results[loser_index].code == "INVALID_TRANSITION"
    assert type(
        operations.commit_manager_response(
            bodies[winner_index], suggestion_id=generation_id, recorded_at=NOW
        )
    ) is DuplicateReceipt
    loser_again = operations.commit_manager_response(
        bodies[loser_index], suggestion_id=generation_id, recorded_at=NOW
    )
    assert type(loser_again) is ConflictReceipt
    assert loser_again.code == "INVALID_TRANSITION"
    ledger.close()

    reopened_ledger, reopened = _open_operations(root)
    manager_events = tuple(
        event
        for event in reopened.ledger.read().events
        if event.event_type == "manager_response_committed"
    )
    assert len(manager_events) == 1
    assert manager_events[0].payload.effective_plan_revision == 1
    reopened_ledger.close()


@_isolated_race_witness
def test_manager_accept_racing_new_exception_is_atomically_serialized(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    manager_body = _accept_body(request_id="manager-versus-exception")
    exception_body = _exception_request(request_id="exception-versus-manager")
    barrier = threading.Barrier(3)

    def accept():
        barrier.wait(timeout=5)
        return operations.commit_manager_response(
            deepcopy(manager_body),
            suggestion_id=generation_id,
            recorded_at=NOW,
        )

    def record_exception():
        barrier.wait(timeout=5)
        return operations.record_exception(
            deepcopy(exception_body), recorded_at=NOW
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        manager_future = pool.submit(accept)
        exception_future = pool.submit(record_exception)
        barrier.wait(timeout=5)
        manager_result = manager_future.result(timeout=5)
        exception_result = exception_future.result(timeout=5)

    assert type(exception_result) is CommittedReceipt
    assert type(manager_result) in {CommittedReceipt, ConflictReceipt}
    events = operations.ledger.read().events
    exception_events = tuple(
        event for event in events if event.event_type == "exception_recorded"
    )
    manager_events = tuple(
        event
        for event in events
        if event.event_type == "manager_response_committed"
    )
    assert len(exception_events) == 1
    assert len(manager_events) == int(type(manager_result) is CommittedReceipt)

    if type(manager_result) is CommittedReceipt:
        assert manager_events[0].sequence < exception_events[0].sequence
        assert manager_events[0].payload.effective_plan_revision == 1
        assert manager_events[0].payload.effective_schedule is not None
        assert manager_events[0].payload.schedule_digest is not None
        replay = operations.commit_manager_response(
            deepcopy(manager_body),
            suggestion_id=generation_id,
            recorded_at=NOW,
        )
        assert type(replay) is DuplicateReceipt
        assert replay.receipt.event_id == manager_result.receipt.event_id
    else:
        assert manager_result.code == "STALE_SUGGESTION"
        projection = operations.date_projection(date(2026, 10, 5))
        assert projection.effective_plan_schedule is None
        assert projection.manager_responses == ()
        before_replay = operations.ledger.verify()
        replay = operations.commit_manager_response(
            deepcopy(manager_body),
            suggestion_id=generation_id,
            recorded_at=NOW,
        )
        assert type(replay) is ConflictReceipt
        assert replay.code == "STALE_SUGGESTION"
        assert operations.ledger.verify() == before_replay


@_isolated_race_witness
def test_exception_correction_and_cancellation_race_to_one_atomic_winner(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)
    operations.record_exception(_exception_request(), recorded_at=NOW)
    exception_id = operations.request_projection(
        "exception-record", "exception-request-1"
    ).committed_record.exception_id
    correction = {
        "schema": "nxt-staffing-exception-correct/v1",
        "request_id": "correct-versus-cancel",
        "exception_id": exception_id,
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "replacement": {
            "kind": "EARLY_DEPARTURE",
            "time_local": "15:00",
            "note": "changed",
        },
    }
    cancellation = {
        "schema": "nxt-staffing-exception-cancel/v1",
        "request_id": "cancel-versus-correct",
        "exception_id": exception_id,
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "note": "returned",
    }
    barrier = threading.Barrier(3)
    before = operations.ledger.verify()[0]

    def correct():
        barrier.wait(timeout=5)
        return operations.correct_exception(deepcopy(correction), recorded_at=NOW)

    def cancel():
        barrier.wait(timeout=5)
        return operations.cancel_exception(deepcopy(cancellation), recorded_at=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        correction_future = pool.submit(correct)
        cancellation_future = pool.submit(cancel)
        barrier.wait(timeout=5)
        results = (
            correction_future.result(timeout=5),
            cancellation_future.result(timeout=5),
        )

    winner = next(result for result in results if type(result) is CommittedReceipt)
    loser = next(result for result in results if type(result) is ConflictReceipt)
    assert loser.code == "STALE_REQUEST"
    assert operations.ledger.verify()[0] == before + 1

    events = operations.ledger.read().events
    terminal_events = tuple(
        event
        for event in events
        if event.event_type in {"exception_corrected", "exception_cancelled"}
    )
    assert len(terminal_events) == 1
    if terminal_events[0].event_type == "exception_corrected":
        assert terminal_events[0].payload.exception_set_revision == 2
        assert terminal_events[0].payload.previous_exception.exception_id == exception_id
        assert terminal_events[0].payload.replacement_exception.exception_id == exception_id
        winning_body = correction
        losing_body = cancellation
        winning_call = operations.correct_exception
        losing_call = operations.cancel_exception
        assert len(operations.date_projection(date(2026, 10, 5)).exceptions) == 1
    else:
        assert terminal_events[0].payload.exception_set_revision == 2
        assert terminal_events[0].payload.cancelled_exception.exception_id == exception_id
        winning_body = cancellation
        losing_body = correction
        winning_call = operations.cancel_exception
        losing_call = operations.correct_exception
        assert operations.date_projection(date(2026, 10, 5)).exceptions == ()

    duplicate = winning_call(deepcopy(winning_body), recorded_at=NOW)
    stable_conflict = losing_call(deepcopy(losing_body), recorded_at=NOW)
    assert type(duplicate) is DuplicateReceipt
    assert duplicate.receipt.event_id == winner.receipt.event_id
    assert type(stable_conflict) is ConflictReceipt
    assert stable_conflict.code == "STALE_REQUEST"


def test_manager_wire_syntax_errors_are_400_before_ledger_access(tmp_path) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    operations = _operations_with_roster(tmp_path)
    base = _reject_body(request_id="manager-invalid")
    bad_values = []
    missing = deepcopy(base)
    missing.pop("note")
    bad_values.append(missing)
    extra = deepcopy(base)
    extra["suggestion_id"] = "hidden"
    bad_values.append(extra)
    hidden_generation = deepcopy(base)
    hidden_generation["generation_id"] = "hidden"
    bad_values.append(hidden_generation)
    for field, value in (
        ("schema", "wrong/v1"),
        ("request_id", "bad id"),
        ("kind", ["REJECT"]),
        ("reason_code", ["MANUAL_HANDLING"]),
        ("note", "bad\u0000note"),
    ):
        item = deepcopy(base)
        item[field] = value
        bad_values.append(item)
    boolean_revision = deepcopy(base)
    boolean_revision["expected_revisions"]["roster"] = True
    bad_values.append(boolean_revision)
    unknown_reason = deepcopy(base)
    unknown_reason["reason_code"] = "UNKNOWN_REASON"
    bad_values.append(unknown_reason)
    long_note = deepcopy(base)
    long_note["note"] = "x" * 501
    bad_values.append(long_note)
    modify_null = deepcopy(base)
    modify_null.update(
        {
            "kind": "MODIFY",
            "candidate_index": 1,
            "reason_code": "APPROVED_WITH_CHANGES",
        }
    )
    bad_values.append(modify_null)
    bad_timestamp = _modify_body(operations, request_id="manager-invalid-time")
    bad_timestamp["edited_operations"][1]["start_at"] = "2026-10-05 09:00:00"
    bad_values.extend(([], bad_timestamp))
    before = operations.ledger.verify()

    for body in bad_values:
        with pytest.raises(StaffingError) as direct:
            operations_module.parse_manager_response(
                body,
                suggestion_id="generation_0123456789abcdef01234567",
            )
        assert direct.value.code == "staffing_invalid_request"
        with pytest.raises(StaffingError) as public:
            operations.commit_manager_response(
                body,
                suggestion_id="generation_0123456789abcdef01234567",
                recorded_at=NOW,
            )
        assert public.value.code == "staffing_invalid_request"
        assert operations.ledger.verify() == before


@pytest.mark.parametrize(
    "field",
    [
        "kind",
        "reason_code",
        "request_id",
        "operator",
        "assignment_id",
        "staff_id",
        "role_code",
        "area_code",
    ],
)
@pytest.mark.parametrize(
    "bad_value",
    [["not", "scalar"], {"not": "scalar"}, True, "bad\u0000value", "x" * 600],
    ids=["list", "object", "boolean", "control", "overlong"],
)
def test_manager_scalar_fields_are_total_at_direct_and_public_boundaries(
    tmp_path, field, bad_value
) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    operations = _operations_with_roster(tmp_path)
    body = _modify_body(operations, request_id="manager-total-parser")
    value = deepcopy(bad_value)
    if field in {"kind", "reason_code", "request_id", "operator"}:
        body[field] = value
    elif field == "assignment_id":
        body["edited_operations"][0][field] = value
    else:
        body["edited_operations"][1][field] = value
    suggestion_id = "generation_0123456789abcdef01234567"
    before = operations.ledger.verify()

    with pytest.raises(StaffingError) as direct:
        operations_module.parse_manager_response(
            body, suggestion_id=suggestion_id
        )
    assert direct.value.code == "staffing_invalid_request"
    with pytest.raises(StaffingError) as public:
        operations.commit_manager_response(
            body, suggestion_id=suggestion_id, recorded_at=NOW
        )
    assert public.value.code == "staffing_invalid_request"
    assert operations.ledger.verify() == before


@pytest.mark.parametrize(
    "start_at,end_at",
    [
        ("2026-10-05T01:00:00Z", "2026-10-05T01:30:00Z"),
        ("2026-10-05T09:00:00+08:00", "2026-10-05T09:30:00+08:00"),
        ("2026-11-01T01:15:00-04:00", "2026-11-01T01:30:00-04:00"),
        ("2026-11-01T01:15:00-05:00", "2026-11-01T01:30:00-05:00"),
    ],
)
def test_manager_digest_binds_original_validated_timestamp_spelling(
    tmp_path, start_at, end_at
) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    operations = _operations_with_roster(tmp_path)
    body = _modify_body(operations)
    add = body["edited_operations"][1]
    add["start_at"] = start_at
    add["end_at"] = end_at
    suggestion_id = "generation_0123456789abcdef01234567"

    request, wire = operations_module.parse_manager_response(
        body, suggestion_id=suggestion_id
    )
    digest = operations_module._request_digest(
        "manager-response", wire, suggestion_id=suggestion_id
    )

    assert wire["edited_operations"][1]["start_at"] == start_at
    assert digest == stable_digest(
        {"suggestion_id": suggestion_id, "body": body}
    )
    assert request.edited_operations[1].start_at.tzinfo is not None


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-05T09:00:00+00:00",
        "2026-10-05T09:00:00-00:00",
        "2026-10-05T09:00:00z",
        "2026-10-05T09:00:00.000000+08:00",
        "2026-10-05T09:00+08:00",
        "2026-10-05T09:00:00",
        "2026-10-05T09:00:00+0800",
        "2026-10-05T09:00:01+08:00",
        "2026-10-05T09:00:00+24:00",
    ],
)
def test_manager_modify_rejects_noncanonical_timestamps_before_ledger_access(
    tmp_path, timestamp
) -> None:
    operations_module = importlib.import_module("nxt_pilot_ops.staffing.operations")
    operations = _operations_with_roster(tmp_path)
    body = _modify_body(operations, request_id="manager-bad-timestamp")
    body["edited_operations"][1]["start_at"] = timestamp
    suggestion_id = "generation_0123456789abcdef01234567"
    before = operations.ledger.verify()

    with pytest.raises(StaffingError) as direct:
        operations_module.parse_manager_response(
            body, suggestion_id=suggestion_id
        )
    assert direct.value.code == "staffing_invalid_request"
    with pytest.raises(StaffingError) as public:
        operations.commit_manager_response(
            body, suggestion_id=suggestion_id, recorded_at=NOW
        )
    assert public.value.code == "staffing_invalid_request"
    assert operations.ledger.verify() == before


def _timezone_roster(*, timezone_name, service_date, weekday, start, end):
    payload = roster_import_request()
    payload["site_timezone"] = timezone_name
    payload["effective_from_local_date"] = service_date
    payload["workers"].append(
        {
            "staff_id": "staff-002",
            "display_name": "本地员工乙",
            "skill_codes": ["BALL_PICKING"],
            "eligibility": [
                {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}
            ],
            "max_daily_minutes": 600,
        }
    )
    payload["availability"] = [
        {
            "staff_id": staff_id,
            "weekday": weekday,
            "start_local": start,
            "end_local": end,
        }
        for staff_id in ("staff-001", "staff-002")
    ]
    payload["regular_assignments"][0].update(
        {"weekday": weekday, "start_local": start, "end_local": end}
    )
    payload["coverage"][0].update(
        {"weekday": weekday, "start_local": start, "end_local": end}
    )
    return payload


@pytest.mark.parametrize(
    "timezone_name,service_date,weekday,window,start_at,end_at,offset_minutes,utc_start",
    [
        (
            "UTC",
            "2026-10-05",
            0,
            ("09:00", "17:00"),
            "2026-10-05T10:00:00Z",
            "2026-10-05T10:30:00Z",
            0,
            datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        ),
        (
            "Asia/Shanghai",
            "2026-10-05",
            0,
            ("09:00", "17:00"),
            "2026-10-05T10:00:00+08:00",
            "2026-10-05T10:30:00+08:00",
            480,
            datetime(2026, 10, 5, 2, 0, tzinfo=UTC),
        ),
        (
            "America/New_York",
            "2026-11-01",
            6,
            ("00:00", "03:00"),
            "2026-11-01T01:15:00-04:00",
            "2026-11-01T01:30:00-04:00",
            -240,
            datetime(2026, 11, 1, 5, 15, tzinfo=UTC),
        ),
        (
            "America/New_York",
            "2026-11-01",
            6,
            ("00:00", "03:00"),
            "2026-11-01T01:15:00-05:00",
            "2026-11-01T01:30:00-05:00",
            -300,
            datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
        ),
    ],
)
def test_restart_projections_restore_utc_and_both_dst_folds_through_one_adapter(
    tmp_path,
    monkeypatch,
    timezone_name,
    service_date,
    weekday,
    window,
    start_at,
    end_at,
    offset_minutes,
    utc_start,
) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root, site_timezone=timezone_name)
    operations.import_roster(
        _timezone_roster(
            timezone_name=timezone_name,
            service_date=service_date,
            weekday=weekday,
            start=window[0],
            end=window[1],
        ),
        recorded_at=NOW,
    )
    request = _generation_request(service_date=service_date)
    operations.reserve_generation(
        request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    generation_id = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation_id
    work = operations.generation_work(generation_id)
    worker_alias = next(
        alias
        for alias, staff_id in work.worker_alias_to_staff_id
        if staff_id == "staff-002"
    )
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "ADD",
                        "worker_alias": worker_alias,
                        "role_code": "RANGE_ATTENDANT",
                        "area_code": "RANGE_A",
                        "start_at": start_at,
                        "end_at": end_at,
                    }
                ],
                "rationale": "add reserve coverage",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )
    ledger.close()

    reopened_ledger, reopened = _open_operations(
        root, site_timezone=timezone_name
    )
    operations_module = importlib.import_module(
        "nxt_pilot_ops.staffing.operations"
    )
    original = operations_module.candidate_patch_for_revalidation
    calls = []

    def guarded(candidate):
        calls.append(candidate.candidate_index)
        return original(candidate)

    monkeypatch.setattr(
        operations_module, "candidate_patch_for_revalidation", guarded
    )
    service_day = date.fromisoformat(service_date)
    date_view = reopened.date_projection(service_day)
    request_view = reopened.request_projection(
        "suggestion-generate", "generation-request-1"
    )
    candidates = (
        date_view.generations[0].candidates[0],
        request_view.candidates[0],
        request_view.generation.candidates[0],
    )
    for candidate in candidates:
        add = next(
            operation
            for operation in candidate.operations
            if operation.operation == "ADD"
        )
        assert int(add.start_at.utcoffset().total_seconds() // 60) == offset_minutes
        assert add.start_at.astimezone(UTC) == utc_start
        assert candidate.materialized_schedule_digest == candidates[0].materialized_schedule_digest
    assert calls == [1, 1, 1]
    wire = canonical_json(request_view)
    for alias, _ in work.worker_alias_to_staff_id:
        assert alias not in wire
    for alias, _ in work.assignment_alias_to_assignment_id:
        assert alias not in wire
    for forbidden in (
        "provider_payload",
        "alias_nonce",
        "prompt_template_version",
        "raw_response",
        "reasoning",
    ):
        assert forbidden not in wire
    reopened_ledger.close()


def test_cold_start_and_unknown_request_projections_are_closed(tmp_path) -> None:
    _, operations = _open_operations(tmp_path / "ledger")
    cold = operations.date_projection(date(2026, 10, 5))

    assert cold.site_id == SITE_ID
    assert cold.deployment_id == DEPLOYMENT_ID
    assert cold.site_timezone == "Asia/Shanghai"
    assert cold.roster_revision is None
    assert cold.effective_plan.status == "NO_PLAN"
    assert cold.effective_plan.revision == 0
    assert cold.assignments == cold.exceptions == cold.generations == ()
    for kind, request_id in (
        ("suggestion-generate", "missing-request"),
        ("unknown-namespace", "missing-request"),
    ):
        with pytest.raises(StaffingError) as raised:
            operations.request_projection(kind, request_id)
        assert raised.value.code == "REQUEST_NOT_FOUND"


def test_existing_request_id_is_not_visible_through_another_namespace(
    tmp_path,
) -> None:
    operations = _operations_with_roster(tmp_path)

    with pytest.raises(StaffingError) as raised:
        operations.request_projection("manager-response", "roster-001")

    assert raised.value.code == "REQUEST_NOT_FOUND"


def test_all_six_committed_record_projections_reopen_detached_and_private(tmp_path) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root)
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    operations.record_exception(_exception_request(), recorded_at=NOW)
    exception_id = operations.request_projection(
        "exception-record", "exception-request-1"
    ).committed_record.exception_id
    correction = {
        "schema": "nxt-staffing-exception-correct/v1",
        "request_id": "correct-request-1",
        "exception_id": exception_id,
        "expected_exception_set_revision": 1,
        "operator": "course-manager",
        "replacement": {
            "kind": "EARLY_DEPARTURE",
            "time_local": "15:00",
            "note": "changed",
        },
    }
    operations.correct_exception(correction, recorded_at=NOW)
    cancellation = {
        "schema": "nxt-staffing-exception-cancel/v1",
        "request_id": "cancel-request-1",
        "exception_id": exception_id,
        "expected_exception_set_revision": 2,
        "operator": "course-manager",
        "note": "returned",
    }
    operations.cancel_exception(cancellation, recorded_at=NOW)
    generation_request = _generation_request(revisions=(1, 3, 0))
    operations.reserve_generation(
        generation_request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    generation_id = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation_id
    _issue_valid_suggestion(operations, generation_id)
    operations.commit_manager_response(
        _accept_body(revisions=(1, 3, 0)),
        suggestion_id=generation_id,
        recorded_at=NOW,
    )
    keys = (
        ("roster-import", "roster-001", RosterCommittedProjection),
        ("exception-record", "exception-request-1", ExceptionCommittedProjection),
        ("exception-correct", "correct-request-1", ExceptionCommittedProjection),
        ("exception-cancel", "cancel-request-1", ExceptionCommittedProjection),
        (
            "suggestion-generate",
            "generation-request-1",
            GenerationCommittedProjection,
        ),
        ("manager-response", "manager-request-1", ManagerCommittedProjection),
    )
    before = tuple(
        operations.request_projection(kind, request_id)
        for kind, request_id, _ in keys
    )
    for view, (_, _, expected_type) in zip(before, keys, strict=True):
        assert type(view.committed_record) is expected_type
        assert len(view.event_ids) == len(set(view.event_ids))
        wire = canonical_json(view)
        for forbidden in (
            "provider_payload",
            "worker_alias_to_staff_id",
            "assignment_alias_to_assignment_id",
            "alias_nonce_digest",
            "raw_response",
            "prompt",
            "reasoning",
            "secret",
        ):
            assert forbidden not in wire
    assert before[1].committed_record.exception is not None
    assert before[2].committed_record.replacement is not None
    assert before[3].committed_record.note == "returned"
    assert before[4].committed_record.basis_revisions == (1, 3, 0)
    assert before[5].committed_record.effective_plan_revision == 1
    ledger.close()

    reopened_ledger, reopened = _open_operations(root)
    after = tuple(
        reopened.request_projection(kind, request_id)
        for kind, request_id, _ in keys
    )
    assert to_primitive(after) == to_primitive(before)
    assert all(left is not right for left, right in zip(before, after, strict=True))
    reopened_ledger.close()


def test_historical_manager_schedule_names_do_not_follow_later_roster_rename(
    tmp_path,
) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root)
    operations.import_roster(roster_import_request(), recorded_at=NOW)
    generation_id = _reserve(operations)
    _issue_valid_suggestion(operations, generation_id)
    operations.commit_manager_response(
        _accept_body(), suggestion_id=generation_id, recorded_at=NOW
    )
    renamed = roster_import_request()
    renamed.update(
        {
            "request_id": "roster-request-2",
            "expected_roster_revision": 1,
            "source_ref": "renamed.csv",
        }
    )
    renamed["workers"][0]["display_name"] = "新名字"
    operations.import_roster(renamed, recorded_at=NOW)
    ledger.close()

    reopened_ledger, reopened = _open_operations(root)
    date_view = reopened.date_projection(date(2026, 10, 5))
    manager_view = reopened.request_projection(
        "manager-response", "manager-request-1"
    )

    assert date_view.assignments[0].display_name == "新名字"
    assert (
        date_view.manager_responses[0].effective_schedule[0].display_name
        == "本地员工甲"
    )
    assert (
        manager_view.manager_response.effective_schedule[0].display_name
        == "本地员工甲"
    )
    assert (
        manager_view.committed_record.effective_schedule[0].display_name
        == "本地员工甲"
    )
    reopened_ledger.close()


def test_restart_marks_accepted_plan_for_review_after_exception_cancellation(
    tmp_path,
) -> None:
    root = tmp_path / "ledger"
    ledger, operations = _open_operations(root)
    roster = _timezone_roster(
        timezone_name="Asia/Shanghai",
        service_date="2026-10-05",
        weekday=0,
        start="09:00",
        end="17:00",
    )
    operations.import_roster(roster, recorded_at=NOW)
    operations.record_exception(_exception_request(), recorded_at=NOW)
    exception_id = operations.request_projection(
        "exception-record", "exception-request-1"
    ).committed_record.exception_id
    generation_request = _generation_request(revisions=(1, 1, 0))
    operations.reserve_generation(
        generation_request,
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence(
            "CN", "READY", "KIMI", "kimi-k2", None, None
        ),
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
        recorded_at=NOW,
    )
    generation_id = operations.request_projection(
        "suggestion-generate", "generation-request-1"
    ).generation_id
    work = operations.generation_work(generation_id)
    worker_alias = next(
        alias
        for alias, staff_id in work.worker_alias_to_staff_id
        if staff_id == "staff-002"
    )
    output = {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "ADD",
                        "worker_alias": worker_alias,
                        "role_code": "RANGE_ATTENDANT",
                        "area_code": "RANGE_A",
                        "start_at": "2026-10-05T09:00:00+08:00",
                        "end_at": "2026-10-05T10:00:00+08:00",
                    }
                ],
                "rationale": "cover the late arrival",
                "operational_warnings": [],
            }
        ]
    }
    started, finished, result = _successful_evidence(
        operations, generation_id, output
    )
    operations.record_attempt_started(generation_id, started, recorded_at=NOW)
    operations.record_attempt_finished(generation_id, finished, recorded_at=NOW)
    operations.commit_generation_result(
        generation_id, result, output, recorded_at=NOW
    )
    operations.commit_manager_response(
        _accept_body(revisions=(1, 1, 0)),
        suggestion_id=generation_id,
        recorded_at=NOW,
    )
    accepted = operations.request_projection(
        "manager-response", "manager-request-1"
    ).manager_response
    operations.cancel_exception(
        {
            "schema": "nxt-staffing-exception-cancel/v1",
            "request_id": "cancel-after-plan",
            "exception_id": exception_id,
            "expected_exception_set_revision": 1,
            "operator": "course-manager",
            "note": "employee returned",
        },
        recorded_at=NOW,
    )
    ledger.close()

    reopened_ledger, reopened = _open_operations(root)
    date_view = reopened.date_projection(date(2026, 10, 5))
    manager_view = reopened.request_projection(
        "manager-response", "manager-request-1"
    ).manager_response

    assert date_view.exception_set_revision == 2
    assert date_view.effective_plan.status == "REVIEW_REQUIRED"
    assert date_view.effective_plan.revision == 1
    assert date_view.effective_plan.affected_exception_ids == (exception_id,)
    assert date_view.effective_plan.schedule_digest == accepted.schedule_digest
    assert manager_view.schedule_digest == accepted.schedule_digest
    assert date_view.effective_plan_schedule == manager_view.effective_schedule
    assert date_view.effective_plan_schedule
    reopened_ledger.close()
