"""Durability and fail-closed tests for the staffing advisory ledger."""

from __future__ import annotations

import gc
import json
import multiprocessing
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from nxt_pilot_ops.serialization import canonical_json_bytes, stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    AppendEventDecision,
    CommittedReceipt,
    ConflictDecision,
    ConflictReceipt,
    DuplicateReceipt,
    GenerationInterruptedPayload,
    GenerationReservedPayload,
    GenerationRouteEvidence,
    ReturnReceiptDecision,
    RosterImportedPayload,
    StaffingError,
    StaffingEvent,
    StaffingReceipt,
)
from nxt_pilot_ops.staffing.ledger import (
    GENESIS_HASH,
    EventCommit,
    StaffingLedger,
    StaffingLedgerIntegrityError,
    parse_record,
)
from nxt_pilot_ops.staffing.plans import build_staffing_basis
from nxt_pilot_ops.staffing.projection import project_generation_request
from nxt_pilot_ops.staffing.roster import validate_roster_import
from nxt_pilot_ops.staffing.workflow import staffing_event_id, staffing_generation_id

from .staffing_fixtures import roster_import_request


UTC = timezone.utc
SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-edge-task-sim-v0"
NOW = datetime(2026, 10, 5, 0, 0, 0, 123456, tzinfo=UTC)


def _error(code: str, function, *args, **kwargs) -> StaffingError:
    with pytest.raises(StaffingError) as raised:
        function(*args, **kwargs)
    assert raised.value.code == code
    return raised.value


def _roster_payload(*, request_id: str = "roster-request-1") -> RosterImportedPayload:
    request = roster_import_request()
    request["request_id"] = request_id
    roster = validate_roster_import(
        request,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    return RosterImportedPayload(
        request_id,
        stable_digest(request),
        roster.revision,
        roster.roster_digest,
        roster,
        "course-manager",
        "weekly.csv",
    )


def _event(
    event_type: str,
    payload: object,
    sequence: int,
    *,
    cause: str | None = None,
    occurred_at: datetime | None = None,
) -> StaffingEvent:
    timestamp = NOW + timedelta(microseconds=sequence) if occurred_at is None else occurred_at
    return StaffingEvent(
        event_type,
        staffing_event_id(
            event_type,
            sequence,
            SITE_ID,
            DEPLOYMENT_ID,
            timestamp,
            cause,
            payload,
        ),
        sequence,
        SITE_ID,
        DEPLOYMENT_ID,
        timestamp,
        cause,
        payload,
    )


def _roster_event(sequence: int = 1) -> StaffingEvent:
    return _event("roster_imported", _roster_payload(), sequence)


def _reservation_event(
    history,
    *,
    request_id: str = "generation-request-1",
    nonce: bytes = b"0123456789abcdef",
) -> StaffingEvent:
    request_digest = stable_digest({"request_id": request_id})
    basis = build_staffing_basis(history, date(2026, 10, 5))
    projection = project_generation_request(
        basis,
        alias_nonce=nonce,
        prompt_template_version="staffing-adjustment/v1",
        language="zh-CN",
    )
    generation_id = staffing_generation_id(
        SITE_ID, DEPLOYMENT_ID, request_id, request_digest
    )
    payload = GenerationReservedPayload(
        request_id,
        request_digest,
        generation_id,
        date(2026, 10, 5),
        projection.basis_snapshot,
        projection.provider_payload,
        projection.alias_nonce_digest,
        projection.worker_alias_to_staff_id,
        projection.assignment_alias_to_assignment_id,
        projection.input_digest,
        "staffing-adjustment/v1",
        "zh-CN",
        GenerationRouteEvidence("CN", "READY", "KIMI", "kimi-k2", None, None),
        None,
        "course-manager",
    )
    return _event("generation_reserved", payload, history.record_count + 1)


def _interrupted_event(history, generation_id: str) -> StaffingEvent:
    reservation = history.reservation(generation_id)
    occurred_at = NOW + timedelta(microseconds=history.record_count + 1)
    payload = GenerationInterruptedPayload(
        reservation.request_digest,
        generation_id,
        "RESULT_UNKNOWN",
        occurred_at,
    )
    return _event(
        "generation_interrupted",
        payload,
        history.record_count + 1,
        cause=generation_id,
        occurred_at=occurred_at,
    )


def _ledger(root: Path) -> StaffingLedger:
    return StaffingLedger(root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)


def _append_roster(ledger: StaffingLedger) -> CommittedReceipt:
    result = ledger.append_via(
        lambda state: AppendEventDecision(_roster_event(state.record_count + 1))
    )
    assert type(result) is CommittedReceipt
    return result


def _append_reservation(
    ledger: StaffingLedger,
    *,
    request_id: str = "generation-request-1",
    nonce: bytes = b"0123456789abcdef",
) -> CommittedReceipt:
    result = ledger.append_via(
        lambda state: AppendEventDecision(
            _reservation_event(state.history, request_id=request_id, nonce=nonce)
        )
    )
    assert type(result) is CommittedReceipt
    return result


def _records(root: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (root / "staffing.jsonl").read_text().splitlines()]


def _anchor(root: Path) -> dict[str, object]:
    return json.loads((root / "staffing.anchor.json").read_bytes())


def _write_records(root: Path, records: list[dict[str, object]]) -> None:
    data = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    (root / "staffing.jsonl").write_bytes(data)


def _write_anchor(root: Path, *, count: int, head_hash: str) -> None:
    (root / "staffing.anchor.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "nxt-staffing-ledger-anchor/v1",
                "site_id": SITE_ID,
                "deployment_id": DEPLOYMENT_ID,
                "record_count": count,
                "head_hash": head_hash,
            }
        )
    )


def _snapshot(root: Path) -> tuple[bytes | None, bytes | None]:
    ledger = root / "staffing.jsonl"
    anchor = root / "staffing.anchor.json"
    return (
        ledger.read_bytes() if ledger.exists() else None,
        anchor.read_bytes() if anchor.exists() else None,
    )


def test_first_append_writes_canonical_record_anchor_and_exact_result_types(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    assert ledger.verify() == (0, GENESIS_HASH)
    assert ledger.read().events == ()

    committed = _append_roster(ledger)
    assert type(committed) is CommittedReceipt
    assert type(committed.receipt) is StaffingReceipt
    assert committed.receipt.duplicate is False
    assert ledger.verify() == (1, committed.receipt.record_hash)
    assert ledger.read().events == (_roster_event(),)

    line = (root / "staffing.jsonl").read_bytes()
    assert line.endswith(b"\n") and not line.endswith(b"\n\n")
    record = json.loads(line)
    assert set(record) == {
        "schema_version",
        "sequence",
        "event_id",
        "event_type",
        "site_id",
        "deployment_id",
        "occurred_at_utc",
        "payload",
        "causation_id",
        "previous_hash",
        "record_hash",
    }
    assert record["schema_version"] == 1
    assert type(record["schema_version"]) is int
    assert record["previous_hash"] == GENESIS_HASH
    supplied_hash = record.pop("record_hash")
    assert supplied_hash == stable_digest(record)
    record["record_hash"] = supplied_hash
    assert line == canonical_json_bytes(record) + b"\n"

    anchor_bytes = (root / "staffing.anchor.json").read_bytes()
    assert not anchor_bytes.endswith(b"\n")
    assert anchor_bytes == canonical_json_bytes(_anchor(root))
    assert _anchor(root) == {
        "schema": "nxt-staffing-ledger-anchor/v1",
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "record_count": 1,
        "head_hash": supplied_hash,
    }

    parsed = parse_record(line, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    assert parsed.event == _roster_event()
    assert parsed.canonical_line == line


def test_business_and_internal_append_paths_are_separate(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    _append_roster(ledger)
    reservation_receipt = _append_reservation(ledger)
    generation_id = ledger.read().events[-1].payload.generation_id

    before = _snapshot(root)
    _error(
        "staffing_invalid_event",
        ledger.append_event_via,
        lambda state: AppendEventDecision(
            _reservation_event(
                state.history,
                request_id="generation-request-wrong-path",
                nonce=b"fedcba9876543210",
            )
        ),
    )
    assert _snapshot(root) == before

    _error(
        "staffing_invalid_event",
        ledger.append_via,
        lambda state: AppendEventDecision(
            _interrupted_event(state.history, generation_id)
        ),
    )
    assert _snapshot(root) == before

    committed = ledger.append_event_via(
        lambda state: AppendEventDecision(
            _interrupted_event(state.history, generation_id)
        )
    )
    assert type(committed) is EventCommit
    assert committed.sequence == 3
    assert committed.event_id == ledger.read().events[-1].event_id
    assert committed.record_hash == ledger.verify()[1]
    assert reservation_receipt.receipt.sequence == 2


@pytest.mark.parametrize(
    "mutate",
    (
        "partial-line",
        "crlf",
        "whitespace",
        "duplicate-key",
        "invalid-utf8",
        "nonfinite",
        "changed-payload",
        "changed-event-id",
        "sequence-gap",
        "broken-previous-hash",
        "foreign-identity",
        "unknown-schema",
        "unknown-event",
    ),
)
def test_record_corruption_fails_closed_without_payload_disclosure(
    tmp_path: Path, mutate: str
):
    root = tmp_path / mutate
    ledger = _ledger(root)
    _append_roster(ledger)
    original = (root / "staffing.jsonl").read_bytes()
    record = json.loads(original)

    if mutate == "partial-line":
        corrupted = original[:-1]
    elif mutate == "crlf":
        corrupted = original[:-1] + b"\r\n"
    elif mutate == "whitespace":
        corrupted = json.dumps(record, ensure_ascii=False).encode() + b"\n"
    elif mutate == "duplicate-key":
        corrupted = original.replace(
            b'"schema_version":1',
            b'"schema_version":1,"schema_version":1',
            1,
        )
    elif mutate == "invalid-utf8":
        corrupted = b"\xff" + original
    elif mutate == "nonfinite":
        corrupted = original.replace(b'"payload":{', b'"payload":{"leak":NaN,', 1)
    else:
        rehash = False
        if mutate == "changed-payload":
            record["payload"]["source_ref"] = "secret-payload-value"
        elif mutate == "changed-event-id":
            record["event_id"] = "staffing_event_" + "a" * 24
            rehash = True
        elif mutate == "sequence-gap":
            record["sequence"] = 2
            record["event_id"] = staffing_event_id(
                "roster_imported",
                2,
                SITE_ID,
                DEPLOYMENT_ID,
                NOW + timedelta(microseconds=1),
                None,
                record["payload"],
            )
            rehash = True
        elif mutate == "broken-previous-hash":
            record["previous_hash"] = "f" * 64
            rehash = True
        elif mutate == "foreign-identity":
            record["site_id"] = "foreign-site"
            record["event_id"] = staffing_event_id(
                "roster_imported",
                1,
                "foreign-site",
                DEPLOYMENT_ID,
                NOW + timedelta(microseconds=1),
                None,
                record["payload"],
            )
            rehash = True
        elif mutate == "unknown-schema":
            record["schema_version"] = 2
            rehash = True
        elif mutate == "unknown-event":
            record["event_type"] = "invented_event"
            rehash = True
        if rehash:
            body = dict(record)
            body.pop("record_hash")
            record["record_hash"] = stable_digest(body)
        corrupted = canonical_json_bytes(record) + b"\n"
    (root / "staffing.jsonl").write_bytes(corrupted)

    with pytest.raises(StaffingLedgerIntegrityError) as raised:
        _ledger(root).verify()
    assert "secret-payload-value" not in str(raised.value)
    assert "NaN" not in str(raised.value)


def test_pathological_json_parser_failure_is_normalized_to_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / "staffing"
    ledger = _ledger(root)
    _append_roster(ledger)

    def parser_limit(*_args, **_kwargs):
        raise RecursionError("parser recursion detail must not escape")

    monkeypatch.setattr(ledger_module.json, "loads", parser_limit)

    with pytest.raises(StaffingLedgerIntegrityError) as raised:
        _ledger(root).verify()
    assert "parser recursion detail" not in str(raised.value)


def test_hash_chain_and_semantic_replay_are_both_verified(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    _append_roster(ledger)
    _append_reservation(ledger)
    records = _records(root)

    records[1]["previous_hash"] = "f" * 64
    body = dict(records[1])
    body.pop("record_hash")
    records[1]["record_hash"] = stable_digest(body)
    _write_records(root, records)
    _write_anchor(root, count=2, head_hash=records[1]["record_hash"])
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(root).verify()

    semantic_root = tmp_path / "semantic"
    semantic = _ledger(semantic_root)
    _append_roster(semantic)
    first = _records(semantic_root)[0]
    duplicate = dict(first)
    duplicate["sequence"] = 2
    duplicate["occurred_at_utc"] = "2026-10-05T00:00:00.123458Z"
    event_body = {
        key: duplicate[key]
        for key in (
            "event_type",
            "event_id",
            "sequence",
            "site_id",
            "deployment_id",
            "occurred_at_utc",
            "causation_id",
            "payload",
        )
    }
    event_body["event_id"] = staffing_event_id(
        "roster_imported",
        2,
        SITE_ID,
        DEPLOYMENT_ID,
        NOW + timedelta(microseconds=2),
        None,
        _roster_payload(),
    )
    duplicate.update(event_body)
    duplicate["previous_hash"] = first["record_hash"]
    duplicate.pop("record_hash")
    duplicate["record_hash"] = stable_digest(duplicate)
    _write_records(semantic_root, [first, duplicate])
    _write_anchor(semantic_root, count=2, head_hash=duplicate["record_hash"])
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(semantic_root).read()


def test_anchor_detects_missing_ahead_rewritten_and_valid_prefix_rollback(tmp_path: Path):
    missing_root = tmp_path / "missing"
    missing = _ledger(missing_root)
    _append_roster(missing)
    (missing_root / "staffing.anchor.json").unlink()
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(missing_root).verify()

    ahead_root = tmp_path / "ahead"
    ahead = _ledger(ahead_root)
    first = _append_roster(ahead)
    _write_anchor(ahead_root, count=2, head_hash=first.receipt.record_hash)
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(ahead_root).verify()

    rewritten_root = tmp_path / "rewritten"
    rewritten = _ledger(rewritten_root)
    _append_roster(rewritten)
    _write_anchor(rewritten_root, count=1, head_hash="f" * 64)
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(rewritten_root).verify()

    rollback_root = tmp_path / "rollback"
    rollback = _ledger(rollback_root)
    _append_roster(rollback)
    _append_reservation(rollback)
    first_record = _records(rollback_root)[0]
    (rollback_root / "staffing.jsonl").write_bytes(
        canonical_json_bytes(first_record) + b"\n"
    )
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(rollback_root).verify()


@pytest.mark.parametrize(
    "mutation",
    (
        "newline",
        "whitespace",
        "duplicate-key",
        "nonfinite",
        "wrong-schema",
        "foreign-identity",
        "boolean-count",
        "invalid-hash",
    ),
)
def test_anchor_json_and_schema_are_strict(tmp_path: Path, mutation: str):
    root = tmp_path / mutation
    ledger = _ledger(root)
    _append_roster(ledger)
    anchor_path = root / "staffing.anchor.json"
    anchor = _anchor(root)
    if mutation == "newline":
        damaged = anchor_path.read_bytes() + b"\n"
    elif mutation == "whitespace":
        damaged = json.dumps(anchor, ensure_ascii=False).encode()
    elif mutation == "duplicate-key":
        damaged = anchor_path.read_bytes().replace(
            b'"schema":"nxt-staffing-ledger-anchor/v1"',
            b'"schema":"nxt-staffing-ledger-anchor/v1","schema":"nxt-staffing-ledger-anchor/v1"',
            1,
        )
    elif mutation == "nonfinite":
        damaged = anchor_path.read_bytes().replace(b'"record_count":1', b'"record_count":NaN')
    else:
        if mutation == "wrong-schema":
            anchor["schema"] = "nxt-staffing-ledger-anchor/v2"
        elif mutation == "foreign-identity":
            anchor["site_id"] = "foreign-site"
        elif mutation == "boolean-count":
            anchor["record_count"] = True
        elif mutation == "invalid-hash":
            anchor["head_hash"] = "A" * 64
        damaged = canonical_json_bytes(anchor)
    anchor_path.write_bytes(damaged)

    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(root).verify()


def test_lagging_anchor_is_valid_prefix_but_only_successful_append_advances_it(
    tmp_path: Path,
):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    first = _append_roster(ledger)
    second = _append_reservation(ledger)
    _write_anchor(root, count=1, head_hash=first.receipt.record_hash)
    lagging_bytes = (root / "staffing.anchor.json").read_bytes()

    duplicate = ledger.append_via(
        lambda state: ReturnReceiptDecision(state.receipts[0])
    )
    assert type(duplicate) is DuplicateReceipt
    assert duplicate.receipt.duplicate is True
    assert (root / "staffing.anchor.json").read_bytes() == lagging_bytes

    conflict = ledger.append_via(
        lambda _state: ConflictDecision(
            "suggestion-generate", "generation-request-1", "STALE_REQUEST"
        )
    )
    assert conflict == ConflictReceipt(
        "suggestion-generate", "generation-request-1", "STALE_REQUEST"
    )
    assert (root / "staffing.anchor.json").read_bytes() == lagging_bytes

    generation_id = ledger.read().events[-1].payload.generation_id
    internal = ledger.append_event_via(
        lambda state: AppendEventDecision(
            _interrupted_event(state.history, generation_id)
        )
    )
    assert type(internal) is EventCommit
    assert _anchor(root) == {
        "schema": "nxt-staffing-ledger-anchor/v1",
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "record_count": 3,
        "head_hash": internal.record_hash,
    }
    assert second.receipt.sequence == 2


@pytest.mark.parametrize("after_first", (True, False))
def test_crash_after_ledger_fsync_leaves_acceptable_anchor_lag_and_next_append_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_first: bool
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / ("first" if after_first else "later")
    ledger = _ledger(root)
    if not after_first:
        _append_roster(ledger)
    real_replace = ledger_module.os.replace
    anchor_replacements = 0

    def crash_anchor_replace(src, dst, *args, **kwargs):
        nonlocal anchor_replacements
        if dst == "staffing.anchor.json":
            anchor_replacements += 1
            # The first-append path writes genesis, then the final anchor.
            threshold = 2 if after_first else 1
            if anchor_replacements == threshold:
                raise OSError("simulated power loss")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(ledger_module.os, "replace", crash_anchor_replace)
    with pytest.raises(StaffingLedgerIntegrityError):
        if after_first:
            _append_roster(ledger)
        else:
            _append_reservation(ledger)
    monkeypatch.setattr(ledger_module.os, "replace", real_replace)

    reopened = _ledger(root)
    expected_count = 1 if after_first else 2
    assert reopened.verify()[0] == expected_count
    if after_first:
        repaired = _append_reservation(reopened)
    else:
        repaired = _append_reservation(
            reopened,
            request_id="generation-request-2",
            nonce=b"fedcba9876543210",
        )
    assert _anchor(root)["record_count"] == expected_count + 1
    assert _anchor(root)["head_hash"] == repaired.receipt.record_hash


def test_duplicate_conflict_refusal_invalid_decision_and_forged_receipt_never_write(
    tmp_path: Path,
):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    committed = _append_roster(ledger)
    before = _snapshot(root)

    duplicate = ledger.append_via(
        lambda state: ReturnReceiptDecision(state.receipts[0])
    )
    assert type(duplicate) is DuplicateReceipt
    assert _snapshot(root) == before

    conflict = ledger.append_via(
        lambda _state: ConflictDecision("roster-import", "roster-request-1", "STALE_REQUEST")
    )
    assert type(conflict) is ConflictReceipt
    assert _snapshot(root) == before

    def refuse(_state):
        raise StaffingError("STALE_REQUEST", "expected revision")

    error = _error("STALE_REQUEST", ledger.append_via, refuse)
    assert error.detail == "expected revision"
    assert _snapshot(root) == before

    _error("staffing_invalid_evidence", ledger.append_via, lambda _state: object())
    assert _snapshot(root) == before

    forged = replace(committed.receipt)
    assert forged == committed.receipt and forged is not committed.receipt
    _error(
        "staffing_invalid_evidence",
        ledger.append_via,
        lambda _state: ReturnReceiptDecision(forged),
    )
    assert _snapshot(root) == before

    _error(
        "staffing_invalid_evidence",
        ledger.append_via,
        lambda _state: ConflictDecision("wrong", "request-1", "STALE_REQUEST"),
    )
    assert _snapshot(root) == before


def test_success_result_is_built_before_first_write(tmp_path: Path, monkeypatch):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / "staffing"
    ledger = _ledger(root)
    before = _snapshot(root)

    def fail_result(_receipt):
        raise RuntimeError("result construction failed")

    monkeypatch.setattr(ledger_module, "CommittedReceipt", fail_result)
    with pytest.raises(RuntimeError, match="result construction failed"):
        ledger.append_via(lambda state: AppendEventDecision(_roster_event(state.record_count + 1)))
    assert _snapshot(root) == before


def test_probe_request_is_read_only_and_validates_exact_inputs(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    committed = _append_roster(ledger)
    before = _snapshot(root)

    assert (
        ledger.probe_request(
            "roster-import", "roster-request-1", committed.receipt.request_digest
        )
        == DuplicateReceipt(replace(committed.receipt, duplicate=True))
    )
    assert ledger.probe_request("roster-import", "new-request", "a" * 64) is None
    assert ledger.probe_request("roster-import", "roster-request-1", "b" * 64) == ConflictReceipt(
        "roster-import", "roster-request-1", "IDEMPOTENCY_CONFLICT"
    )
    assert _snapshot(root) == before

    for values in (
        ("unknown", "new-request", "a" * 64),
        ("roster-import", "", "a" * 64),
        ("roster-import", "new-request", "A" * 64),
    ):
        _error("staffing_invalid_evidence", ledger.probe_request, *values)
    assert _snapshot(root) == before


def test_two_instances_serialize_builders_before_replay_and_precondition(tmp_path: Path):
    root = tmp_path / "staffing"
    first = _ledger(root)
    second = _ledger(root)
    entered_first = threading.Event()
    release_first = threading.Event()
    entered_second = threading.Event()

    def first_builder(state):
        entered_first.set()
        assert release_first.wait(5)
        return AppendEventDecision(_roster_event(state.record_count + 1))

    def second_builder(state):
        entered_second.set()
        return AppendEventDecision(_reservation_event(state.history))

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_first = pool.submit(first.append_via, first_builder)
        assert entered_first.wait(5)
        pending_second = pool.submit(second.append_via, second_builder)
        assert not entered_second.wait(0.2)
        release_first.set()
        assert type(pending_first.result(timeout=5)) is CommittedReceipt
        assert type(pending_second.result(timeout=5)) is CommittedReceipt
    assert entered_second.is_set()
    assert first.verify()[0] == 2


def test_second_instance_read_verify_and_probe_wait_for_exclusive_writer(tmp_path: Path):
    root = tmp_path / "staffing"
    writer = _ledger(root)
    reader = _ledger(root)
    entered = threading.Event()
    release = threading.Event()

    def builder(state):
        entered.set()
        assert release.wait(5)
        return AppendEventDecision(_roster_event(state.record_count + 1))

    with ThreadPoolExecutor(max_workers=4) as pool:
        write_future = pool.submit(writer.append_via, builder)
        assert entered.wait(5)
        reads = (
            pool.submit(reader.read),
            pool.submit(reader.verify),
            pool.submit(reader.probe_request, "roster-import", "none", "a" * 64),
        )
        time.sleep(0.2)
        assert all(not item.done() for item in reads)
        release.set()
        assert type(write_future.result(timeout=5)) is CommittedReceipt
        assert reads[0].result(timeout=5).record_count == 1
        assert reads[1].result(timeout=5)[0] == 1
        assert reads[2].result(timeout=5) is None


def _process_hold_append(root: str, entered, release) -> None:
    ledger = StaffingLedger(root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)

    def builder(state):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("parent did not release process")
        return AppendEventDecision(_roster_event(state.record_count + 1))

    ledger.append_via(builder)


def _process_append_after_lock_replacement(root: str, entered) -> None:
    ledger = StaffingLedger(root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)

    def builder(state):
        entered.set()
        event = (
            _roster_event(state.record_count + 1)
            if state.record_count == 0
            else _reservation_event(state.history)
        )
        return AppendEventDecision(event)

    ledger.append_via(builder)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_fcntl_lock_blocks_a_separate_process(tmp_path: Path):
    root = tmp_path / "staffing"
    _ledger(root)
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    process = context.Process(target=_process_hold_append, args=(str(root), entered, release))
    process.start()
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(_ledger(root).verify)
            time.sleep(0.2)
            assert not pending.done()
            release.set()
            assert pending.result(timeout=5)[0] == 1
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_pinned_root_lock_survives_lock_entry_replacement(tmp_path: Path):
    root = tmp_path / "staffing"
    setup = _ledger(root)
    context = multiprocessing.get_context("fork")
    entered_first = context.Event()
    release_first = context.Event()
    entered_second = context.Event()
    first = context.Process(
        target=_process_hold_append,
        args=(str(root), entered_first, release_first),
    )
    second = context.Process(
        target=_process_append_after_lock_replacement,
        args=(str(root), entered_second),
    )
    first.start()
    try:
        assert entered_first.wait(5)
        lock_path = root / ".staffing.lock"
        lock_path.unlink()
        replacement = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(replacement)
        second.start()
        entered_before_release = entered_second.wait(0.3)
        release_first.set()
        first.join(5)
        second.join(5)
        assert not entered_before_release
        assert first.exitcode == 0
        assert second.exitcode == 0
        assert _ledger(root).verify()[0] == 2
    finally:
        release_first.set()
        for process in (first, second):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
        close = getattr(setup, "close", None)
        if close is not None:
            close()


def test_first_append_fsyncs_genesis_then_ledger_then_final_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / "staffing"
    ledger = _ledger(root)
    actions: list[str] = []
    real_fsync = ledger_module.os.fsync
    real_replace = ledger_module.os.replace

    def tracked_fsync(fd: int):
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            actions.append("fsync-directory")
        else:
            try:
                ledger_stat = os.stat("staffing.jsonl", dir_fd=ledger._root_fd)
            except FileNotFoundError:
                ledger_stat = None
            current = os.fstat(fd)
            actions.append(
                "fsync-ledger"
                if ledger_stat is not None
                and (current.st_dev, current.st_ino)
                == (ledger_stat.st_dev, ledger_stat.st_ino)
                else "fsync-anchor-temp"
            )
        return real_fsync(fd)

    def tracked_replace(src, dst, *args, **kwargs):
        actions.append("replace-anchor")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(ledger_module.os, "fsync", tracked_fsync)
    monkeypatch.setattr(ledger_module.os, "replace", tracked_replace)
    _append_roster(ledger)
    assert actions == [
        "fsync-anchor-temp",
        "replace-anchor",
        "fsync-directory",
        "fsync-ledger",
        "fsync-directory",
        "fsync-anchor-temp",
        "replace-anchor",
        "fsync-directory",
    ]


def test_permissions_are_forced_and_all_children_are_regular(tmp_path: Path):
    root = tmp_path / "staffing"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)
    ledger = _ledger(root)
    _append_roster(ledger)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for name in (".staffing.lock", "staffing.jsonl", "staffing.anchor.json"):
        item = root / name
        assert item.is_file() and not item.is_symlink()
        assert stat.S_IMODE(item.stat().st_mode) == 0o600
    assert not (root / "staffing.anchor.json.tmp").exists()


@pytest.mark.parametrize(
    "target_name",
    ("staffing.jsonl", "staffing.anchor.json", ".staffing.lock", "staffing.anchor.json.tmp"),
)
def test_child_symlink_and_nonregular_targets_fail_closed(tmp_path: Path, target_name: str):
    for variant in ("symlink", "directory"):
        root = tmp_path / f"{target_name}-{variant}"
        root.mkdir()
        target = root / target_name
        if variant == "symlink":
            outside = tmp_path / f"outside-{target_name}"
            outside.write_bytes(b"outside")
            target.symlink_to(outside)
        else:
            target.mkdir()
        with pytest.raises(StaffingLedgerIntegrityError):
            _ledger(root)


def test_replacing_live_lock_entry_with_symlink_fails_closed(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"outside")
    (root / ".staffing.lock").unlink()
    (root / ".staffing.lock").symlink_to(outside)

    with pytest.raises(StaffingLedgerIntegrityError):
        ledger.verify()


def test_root_and_intermediate_symlinks_or_nondirectories_fail_before_storage_mutation(
    tmp_path: Path,
):
    target = tmp_path / "actual"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(linked)
    assert list(target.iterdir()) == []

    intermediate_target = tmp_path / "intermediate-target"
    intermediate_target.mkdir()
    intermediate = tmp_path / "intermediate-link"
    intermediate.symlink_to(intermediate_target, target_is_directory=True)
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(intermediate / "staffing")
    assert list(intermediate_target.iterdir()) == []

    nondirectory = tmp_path / "plain-file"
    nondirectory.write_bytes(b"plain")
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(nondirectory / "staffing")


def test_ancestor_swap_cannot_redirect_the_pinned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    base = tmp_path / "base"
    ancestor = base / "ancestor"
    root = ancestor / "staffing"
    parked = base / "ancestor-original"
    outside = tmp_path / "outside"
    outside_root = outside / "staffing"
    root.mkdir(parents=True)
    outside_root.mkdir(parents=True)
    real_open = ledger_module.os.open
    swapped = False

    def swap_before_final_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        path_text = os.fspath(path)
        is_current_full_path_open = dir_fd is None and Path(path_text) == root
        is_pinned_final_component_open = dir_fd is not None and path_text == "staffing"
        if not swapped and (is_current_full_path_open or is_pinned_final_component_open):
            ancestor.rename(parked)
            ancestor.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(ledger_module.os, "open", swap_before_final_open)
    ledger = _ledger(root)
    assert swapped
    assert list(outside_root.iterdir()) == []
    assert (parked / "staffing" / ".staffing.lock").is_file()
    assert ledger.verify() == (0, GENESIS_HASH)
    close = getattr(ledger, "close", None)
    if close is not None:
        close()


def test_identity_and_posix_capability_are_checked_before_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    invalid_root = tmp_path / "invalid-identity"
    _error(
        "staffing_invalid_evidence",
        StaffingLedger,
        invalid_root,
        site_id="",
        deployment_id=DEPLOYMENT_ID,
    )
    assert not invalid_root.exists()

    unsupported_root = tmp_path / "unsupported"
    monkeypatch.setattr(ledger_module, "_fcntl", None)
    with pytest.raises(StaffingLedgerIntegrityError):
        StaffingLedger(unsupported_root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    assert not unsupported_root.exists()


def test_lock_permission_failure_is_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / "permission-failure"
    real_fchmod = ledger_module.os.fchmod
    calls = 0

    def fail_lock_chmod(fd: int, mode: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("host path must not escape")
        return real_fchmod(fd, mode)

    monkeypatch.setattr(ledger_module.os, "fchmod", fail_lock_chmod)
    with pytest.raises(StaffingLedgerIntegrityError) as raised:
        _ledger(root)
    assert "host path" not in str(raised.value)


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def test_close_context_and_finalizer_release_owned_descriptors(tmp_path: Path):
    gc.collect()
    baseline = _open_fd_count()
    for index in range(12):
        ledger = _ledger(tmp_path / f"staffing-{index}")
        root_fd = ledger._root_fd
        lock_fd = ledger._lock_fd
        with ledger as active:
            assert active is ledger
            assert active.verify() == (0, GENESIS_HASH)
            os.fstat(root_fd)
            os.fstat(lock_fd)
        ledger.close()
        assert ledger._lock_fd == -1
        assert ledger._root_fd == -1
        with pytest.raises(OSError):
            os.fstat(lock_fd)
        with pytest.raises(OSError):
            os.fstat(root_fd)
        with pytest.raises(StaffingLedgerIntegrityError):
            ledger.verify()
        del ledger
    for index in range(12, 24):
        ledger = _ledger(tmp_path / f"staffing-{index}")
        ledger.verify()
        del ledger
    gc.collect()
    assert _open_fd_count() == baseline


def test_close_waits_for_active_builder_then_reopen_verifies_commit(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    builder_entered = threading.Event()
    release_builder = threading.Event()
    close_called = threading.Event()
    close_returned = threading.Event()

    def blocking_builder(state):
        builder_entered.set()
        assert release_builder.wait(5)
        return AppendEventDecision(_roster_event(state.record_count + 1))

    def close_ledger() -> None:
        close_called.set()
        ledger.close()
        close_returned.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        append_future = pool.submit(ledger.append_via, blocking_builder)
        assert builder_entered.wait(5)
        close_future = pool.submit(close_ledger)
        assert close_called.wait(5)
        try:
            assert not close_returned.wait(0.2)
            assert not close_future.done()
        finally:
            release_builder.set()
        committed = append_future.result(timeout=5)
        assert type(committed) is CommittedReceipt
        assert close_future.result(timeout=5) is None
        assert close_returned.is_set()

    def builder_must_not_run(_state):
        raise AssertionError("closed ledger invoked builder")

    closed_operations = (
        ledger.read,
        ledger.verify,
        lambda: ledger.probe_request("roster-import", "request-1", "a" * 64),
        lambda: ledger.append_via(builder_must_not_run),
        lambda: ledger.append_event_via(builder_must_not_run),
    )
    for operation in closed_operations:
        with pytest.raises(StaffingLedgerIntegrityError):
            operation()

    with _ledger(root) as reopened:
        assert reopened.verify() == (1, committed.receipt.record_hash)
        assert reopened.read().record_count == 1


@pytest.mark.parametrize(
    "failure_point",
    ("root-fstat", "root-fchmod", "existing-child-fchmod", "lock-open", "lock-fchmod"),
)
def test_every_constructor_failure_releases_owned_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / failure_point
    if failure_point == "existing-child-fchmod":
        root.mkdir()
        (root / "staffing.jsonl").write_bytes(b"")
    real_open = ledger_module.os.open
    real_fstat = ledger_module.os.fstat
    real_fchmod = ledger_module.os.fchmod
    fstat_calls = 0
    fchmod_calls = 0

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        if failure_point == "lock-open" and os.fspath(path) == ".staffing.lock":
            raise PermissionError("private lock path")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def failing_fstat(fd: int):
        nonlocal fstat_calls
        fstat_calls += 1
        if failure_point == "root-fstat" and fstat_calls == 1:
            raise PermissionError("private root path")
        return real_fstat(fd)

    def failing_fchmod(fd: int, mode: int):
        nonlocal fchmod_calls
        fchmod_calls += 1
        target_call = 2 if failure_point in {"existing-child-fchmod", "lock-fchmod"} else 1
        if failure_point.endswith("fchmod") and fchmod_calls == target_call:
            raise PermissionError("private chmod path")
        return real_fchmod(fd, mode)

    monkeypatch.setattr(ledger_module.os, "open", failing_open)
    monkeypatch.setattr(ledger_module.os, "fstat", failing_fstat)
    monkeypatch.setattr(ledger_module.os, "fchmod", failing_fchmod)
    gc.collect()
    baseline = _open_fd_count()
    with pytest.raises(StaffingLedgerIntegrityError):
        _ledger(root)
    gc.collect()
    assert _open_fd_count() == baseline


def test_original_event_crosses_persistence_serializer_once(tmp_path: Path, monkeypatch):
    from nxt_pilot_ops.staffing import ledger as ledger_module

    root = tmp_path / "staffing"
    ledger = _ledger(root)
    supplied = _roster_event()
    real_to_primitive = ledger_module.to_primitive
    original_visits = 0

    def tracked(value):
        nonlocal original_visits
        if value is supplied:
            original_visits += 1
            if original_visits > 1:
                raise RuntimeError("hostile object traversed twice")
        return real_to_primitive(value)

    monkeypatch.setattr(ledger_module, "to_primitive", tracked)
    result = ledger.append_via(lambda _state: AppendEventDecision(supplied))
    assert type(result) is CommittedReceipt
    assert original_visits == 1
    assert _ledger(root).verify()[0] == 1


def test_stateful_timezone_cannot_be_traversed_twice_for_persistence(tmp_path: Path):
    class OnePersistenceTraversalUtc(tzinfo):
        def __init__(self) -> None:
            self.calls = 0
            self.armed = False

        def utcoffset(self, _value):
            self.calls += 1
            # Direct transition validation consumes three offset reads and the
            # one persistence detach consumes two. A second persistence walk
            # would cross this fixed boundary and fail before any write.
            if self.armed and self.calls > 5:
                raise RuntimeError("second persistence traversal")
            return timedelta(0)

        def dst(self, _value):
            return timedelta(0)

        def tzname(self, _value):
            return "hostile-utc"

    hostile = OnePersistenceTraversalUtc()
    occurred_at = datetime(2026, 10, 5, 0, 0, 0, 123457, tzinfo=hostile)
    supplied = _event(
        "roster_imported", _roster_payload(), 1, occurred_at=occurred_at
    )
    hostile.calls = 0
    hostile.armed = True

    root = tmp_path / "staffing"
    result = _ledger(root).append_via(lambda _state: AppendEventDecision(supplied))
    assert type(result) is CommittedReceipt
    assert hostile.calls == 5
    assert _ledger(root).verify()[0] == 1


def test_event_commit_validates_exact_fields():
    assert EventCommit("event-1", 1, "a" * 64).sequence == 1
    for values in (
        ("", 1, "a" * 64),
        ("event-1", True, "a" * 64),
        ("event-1", 0, "a" * 64),
        ("event-1", 1, "A" * 64),
    ):
        _error("staffing_invalid_evidence", EventCommit, *values)


def test_coordinated_deletion_is_outside_anchor_detection_boundary(tmp_path: Path):
    root = tmp_path / "staffing"
    ledger = _ledger(root)
    _append_roster(ledger)
    # If an attacker coordinates deletion of both files, no local high-water
    # evidence remains. This is an explicitly documented physical boundary,
    # not something the ledger can honestly claim to detect.
    (root / "staffing.jsonl").unlink()
    (root / "staffing.anchor.json").unlink()
    assert _ledger(root).verify() == (0, GENESIS_HASH)
