"""Manager-review regressions over an isolated synthetic staffing ledger.

Covers the local explanation rendering (provider aliases replaced by local
labels only in the projection, with UTC offsets across a daylight-saving
fall-back), the candidate action window (the earliest affected end), and the
expired-suggestion admission rule: an ACCEPT or MODIFY any of whose affected
shifts has ended at the injected audit instant is a closed conflict that
mutates nothing, and editing cannot revive an expired suggestion.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from nxt_pilot_ops.serialization import stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    AddOperation,
    Assignment,
    AttemptFinishedEvidence,
    AttemptRouteEvidence,
    AttemptStartedEvidence,
    CommittedReceipt,
    ConflictReceipt,
    GenerationRouteEvidence,
    RemoveOperation,
    ResultEvidence,
    StaffingError,
    Worker,
)
from nxt_pilot_ops.staffing.ledger import StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_pilot_ops.staffing.prompt import PROMPT_TEMPLATE_VERSION
from nxt_pilot_ops.staffing.review import (
    ALIAS_TOKEN_PATTERN,
    action_window_end,
    assignment_label,
    end_of_service_day,
    is_expired,
    local_alias_labels,
    render_local_text,
)
from tests.pilot_ops.staffing_fixtures import roster_import_request

SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-edge-task-sim-v0"
ZONE = "Asia/Shanghai"
SERVICE_DATE = date(2026, 10, 5)  # Monday, weekday 0 of the shared fixture
MORNING = datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)  # 08:30 site time
SHIFT_END = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)  # 17:00 site time
EVENING = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)  # 21:00 site time
WORKER_ALIAS = "worker_" + "0f" * 12
ASSIGNMENT_ALIAS = "assignment_" + "1e" * 12


def _shift(start_hour: int, end_hour: int, *, day: date = SERVICE_DATE) -> Assignment:
    return Assignment(
        "assignment-1",
        "staff-001",
        "RANGE_ATTENDANT",
        "RANGE_A",
        datetime(day.year, day.month, day.day, start_hour, tzinfo=timezone.utc),
        datetime(day.year, day.month, day.day, end_hour, tzinfo=timezone.utc),
    )


def _workers() -> tuple[Worker, ...]:
    return (
        Worker("staff-001", "本地员工甲", ("BALL_PICKING",), (("RANGE_ATTENDANT", "RANGE_A"),), 480),
        Worker("staff-002", "本地员工乙", ("BALL_PICKING",), (("RANGE_ATTENDANT", "RANGE_A"),), 600),
    )


def test_render_local_text_replaces_bound_aliases_and_keeps_unknown_tokens() -> None:
    labels = local_alias_labels(
        workers=_workers(),
        assignments=(_shift(1, 9),),
        worker_alias_to_staff_id=((WORKER_ALIAS, "staff-002"), ("worker_" + "ab" * 12, "staff-unknown")),
        assignment_alias_to_assignment_id=((ASSIGNMENT_ALIAS, "assignment-1"), ("assignment_" + "cd" * 12, "assignment-missing")),
        site_timezone=ZONE,
    )
    assert labels == {
        WORKER_ALIAS: "本地员工乙",
        ASSIGNMENT_ALIAS: "本地员工甲 (RANGE_ATTENDANT/RANGE_A 09:00–17:00)",
    }
    unknown = "worker_" + "ee" * 12
    raw = f"由 {WORKER_ALIAS} 顶替 {ASSIGNMENT_ALIAS} 班次；{unknown} 待确认 worker_short xworker_{'0f' * 12}"
    rendered = render_local_text(raw, labels)
    assert rendered == (
        f"由 本地员工乙 顶替 本地员工甲 (RANGE_ATTENDANT/RANGE_A 09:00–17:00) 班次；{unknown} 待确认 worker_short xworker_{'0f' * 12}"
    )
    assert render_local_text("no alias here", labels) == "no alias here"
    assert ALIAS_TOKEN_PATTERN.findall(rendered) == [unknown]
    with pytest.raises(StaffingError):
        render_local_text(None, labels)  # type: ignore[arg-type]


def test_assignment_label_uses_site_time_and_dates_only_across_midnight() -> None:
    assert assignment_label(_shift(1, 9), "本地员工甲", ZONE) == "本地员工甲 (RANGE_ATTENDANT/RANGE_A 09:00–17:00)"
    overnight = Assignment(
        "assignment-2", "staff-001", "RANGE_ATTENDANT", "RANGE_A",
        datetime(2026, 10, 5, 14, tzinfo=timezone.utc), datetime(2026, 10, 5, 18, tzinfo=timezone.utc),
    )
    assert assignment_label(overnight, "本地员工甲", ZONE) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 2026-10-05 22:00–2026-10-06 02:00)"
    )
    with pytest.raises(StaffingError):
        assignment_label(_shift(1, 9), "本地员工甲", "Not/AZone")


def _new_york(start: datetime, end: datetime, assignment_id: str = "assignment-ny") -> Assignment:
    return Assignment(assignment_id, "staff-001", "RANGE_ATTENDANT", "RANGE_A", start, end)


def test_assignment_label_disambiguates_a_daylight_saving_fallback() -> None:
    zone = "America/New_York"
    utc = lambda hour, minute=0, day=1, month=11: datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)  # noqa: E731
    # The one-hour shift across the 2026-11-01 fall-back: 01:30 EDT to 01:30 EST.
    assert assignment_label(_new_york(utc(5, 30), utc(6, 30)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 01:30 UTC-04:00–01:30 UTC-05:00)"
    )
    # Same offset at both ends, but the end wall time is repeated that night.
    assert assignment_label(_new_york(utc(4, 30), utc(5, 30)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 00:30 UTC-04:00–01:30 UTC-04:00)"
    )
    # Same offset at both ends, repeated start wall time.
    assert assignment_label(_new_york(utc(6, 30), utc(7, 30)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 01:30 UTC-05:00–02:30 UTC-05:00)"
    )
    # Spring forward on 2026-03-08: the offsets differ, so the one-hour shift is marked.
    assert assignment_label(_new_york(utc(6, 30, 8, 3), utc(7, 30, 8, 3)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 01:30 UTC-05:00–03:30 UTC-04:00)"
    )
    # Across local midnight and the fall-back.
    assert assignment_label(_new_york(utc(2, 0), utc(7, 0)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 2026-10-31 22:00 UTC-04:00–2026-11-01 02:00 UTC-05:00)"
    )
    # An ordinary New York shift and the Shanghai fixture carry no suffix.
    assert assignment_label(_new_york(utc(7, 30), utc(12, 30)), "本地员工甲", zone) == (
        "本地员工甲 (RANGE_ATTENDANT/RANGE_A 02:30–07:30)"
    )
    assert assignment_label(_shift(1, 9), "本地员工甲", ZONE) == "本地员工甲 (RANGE_ATTENDANT/RANGE_A 09:00–17:00)"


def test_action_window_end_closes_at_the_earliest_affected_end_and_falls_back_to_service_day_end() -> None:
    ends = {ASSIGNMENT_ALIAS: SHIFT_END}
    add = AddOperation("ADD", WORKER_ALIAS, "RANGE_ATTENDANT", "RANGE_A", MORNING, MORNING + timedelta(hours=3))
    remove = RemoveOperation("REMOVE", ASSIGNMENT_ALIAS)
    assert action_window_end((add,), assignment_end_by_alias=ends, service_date=SERVICE_DATE, site_timezone=ZONE) == MORNING + timedelta(hours=3)
    assert action_window_end((remove,), assignment_end_by_alias=ends, service_date=SERVICE_DATE, site_timezone=ZONE) == SHIFT_END
    # Every affected shift must still be open: the earlier end closes the window.
    assert action_window_end((add, remove), assignment_end_by_alias=ends, service_date=SERVICE_DATE, site_timezone=ZONE) == MORNING + timedelta(hours=3)
    assert action_window_end((remove, add), assignment_end_by_alias=ends, service_date=SERVICE_DATE, site_timezone=ZONE) == MORNING + timedelta(hours=3)
    unresolved = RemoveOperation("REMOVE", "assignment_" + "cd" * 12)
    day_end = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)  # 2026-10-06 00:00 site time
    assert end_of_service_day(SERVICE_DATE, ZONE) == day_end
    assert action_window_end((unresolved,), assignment_end_by_alias=ends, service_date=SERVICE_DATE, site_timezone=ZONE) == day_end
    assert action_window_end((), assignment_end_by_alias={}, service_date=SERVICE_DATE, site_timezone=ZONE) == day_end
    with pytest.raises(StaffingError):
        action_window_end(("ADD",), assignment_end_by_alias={}, service_date=SERVICE_DATE, site_timezone=ZONE)  # type: ignore[arg-type]


def test_is_expired_is_half_open_at_the_window_end() -> None:
    assert is_expired(SHIFT_END, SHIFT_END) is True
    assert is_expired(SHIFT_END, SHIFT_END - timedelta(microseconds=1)) is False
    assert is_expired(SHIFT_END, SHIFT_END.astimezone(timezone(timedelta(hours=8)))) is True
    with pytest.raises(StaffingError):
        is_expired(SHIFT_END, datetime(2026, 10, 5, 9))


# --- ledger-backed flow -----------------------------------------------------


def _roster() -> dict[str, object]:
    payload = roster_import_request()
    payload["workers"].append(
        {
            "staff_id": "staff-002",
            "display_name": "本地员工乙",
            "skill_codes": ["BALL_PICKING"],
            "eligibility": [{"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}],
            "max_daily_minutes": 720,
        }
    )
    payload["availability"].append(
        {"staff_id": "staff-002", "weekday": 0, "start_local": "08:00", "end_local": "21:00"}
    )
    # The LEAVE removes staff-001's shift from the generation basis, so this
    # adjacent evening shift is the one aliased assignment a provider can name.
    payload["regular_assignments"].append(
        {
            "staff_id": "staff-002",
            "weekday": 0,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "17:00",
            "end_local": "21:00",
        }
    )
    return payload


def _open(root: Path) -> tuple[StaffingLedger, StaffingOperations]:
    ledger = StaffingLedger(root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    owner = StaffingOperations(ledger, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID, site_timezone=ZONE)
    return ledger, owner


def _leave(request_id: str = "leave-1") -> dict[str, object]:
    return {
        "schema": "nxt-staffing-exception/v1",
        "request_id": request_id,
        "expected_roster_revision": 1,
        "expected_exception_set_revision": 0,
        "service_date": SERVICE_DATE.isoformat(),
        "staff_id": "staff-001",
        "kind": "LEAVE",
        "time_local": None,
        "operator": "course-manager",
        "note": "synthetic leave",
    }


def _issue_suggestion(
    owner: StaffingOperations,
    *,
    recorded_at: datetime,
    end_local: str = "17:00",
    remove_evening: bool = False,
    second_candidate_removes_evening: bool = False,
) -> str:
    """Reserve and issue one VALID candidate whose text names real aliases.

    With ``remove_evening`` the candidate also removes the aliased 17:00–21:00
    shift, so it affects two shifts with different ends.  With
    ``second_candidate_removes_evening`` a second, REMOVE-only candidate is
    issued beside it; its window closes at 21:00 site time, later than the
    first candidate's, which lets a test reach the edited-operations check.
    """

    owner.reserve_generation(
        {
            "schema": "nxt-staffing-suggestion-generate/v1",
            "request_id": "generate-1",
            "operator": "course-manager",
            "service_date": SERVICE_DATE.isoformat(),
            "expected_revisions": {"roster": 1, "exception_set": 1, "effective_plan": 0},
            "retry_of": None,
        },
        alias_nonce=b"0123456789abcdef",
        route_evidence=GenerationRouteEvidence("CN", "READY", "KIMI", "kimi-k2", None, None),
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="zh-CN",
        recorded_at=recorded_at,
    )
    generation_id = owner.request_projection("suggestion-generate", "generate-1").generation_id
    reservation = owner.ledger.read().reservation(generation_id)
    worker_alias = next(alias for alias, staff_id in reservation.worker_alias_to_staff_id if staff_id == "staff-002")
    assignment_alias = next(alias for alias, _ in reservation.assignment_alias_to_assignment_id)
    operations: list[dict[str, object]] = [
        {
            "operation": "ADD",
            "worker_alias": worker_alias,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_at": f"{SERVICE_DATE.isoformat()}T09:00:00+08:00",
            "end_at": f"{SERVICE_DATE.isoformat()}T{end_local}:00+08:00",
        }
    ]
    if remove_evening:
        operations.append({"operation": "REMOVE", "assignment_alias": assignment_alias})
    candidates: list[dict[str, object]] = [
        {
            "candidate_index": 1,
            "operations": operations,
            "rationale": f"由 {worker_alias} 顶替请假班次，保留 {assignment_alias}。",
            "operational_warnings": [f"{worker_alias} 需提前到岗"],
        }
    ]
    if second_candidate_removes_evening:
        candidates.append(
            {
                "candidate_index": 2,
                "operations": [{"operation": "REMOVE", "assignment_alias": assignment_alias}],
                "rationale": "仅取消晚班。",
                "operational_warnings": [],
            }
        )
    output = {"candidates": candidates}
    work = owner.generation_work(generation_id)
    route = AttemptRouteEvidence("KIMI", "CN", "PRIMARY", "cn-kimi-v1", "kimi-k2")
    started = AttemptStartedEvidence(generation_id, 0, route, work.input_digest, 10.0)
    finished = AttemptFinishedEvidence(
        generation_id, 0, route, "SUCCEEDED", None, False, False, None, "stop", stable_digest(output), None, None
    )
    result = ResultEvidence(
        generation_id, "SUCCEEDED", None, "KIMI", "kimi-k2", None, "stop", work.input_digest,
        stable_digest(output), (finished,), len(candidates), None,
    )
    owner.record_attempt_started(generation_id, started, recorded_at=recorded_at)
    owner.record_attempt_finished(generation_id, finished, recorded_at=recorded_at)
    owner.commit_generation_result(generation_id, result, output, recorded_at=recorded_at)
    return generation_id


def _manager_body(kind: str, request_id: str, *, operations: list[dict[str, object]] | None = None) -> dict[str, object]:
    reason = {"ACCEPT": "APPROVED", "MODIFY": "APPROVED_WITH_CHANGES", "REJECT": "MANUAL_HANDLING"}[kind]
    return {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": request_id,
        "operator": "course-manager",
        "kind": kind,
        "expected_revisions": {"roster": 1, "exception_set": 1, "effective_plan": 0},
        "candidate_index": None if kind == "REJECT" else 1,
        "edited_operations": operations if kind == "MODIFY" else None,
        "reason_code": reason,
        "note": None,
    }


def _ledger_bytes(root: Path) -> tuple[bytes, bytes]:
    return (root / "staffing.jsonl").read_bytes(), (root / "staffing.anchor.json").read_bytes()


@pytest.fixture
def issued(tmp_path: Path) -> tuple[Path, StaffingLedger, StaffingOperations, str]:
    root = tmp_path / "ledger"
    ledger, owner = _open(root)
    owner.import_roster(_roster(), recorded_at=MORNING)
    owner.record_exception(_leave(), recorded_at=MORNING)
    generation_id = _issue_suggestion(owner, recorded_at=MORNING)
    return root, ledger, owner, generation_id


def test_projection_renders_local_explanations_without_leaking_aliases_or_touching_raw_text(issued) -> None:
    _, _, owner, generation_id = issued
    reservation = owner.ledger.read().reservation(generation_id)
    projection = owner.date_projection(SERVICE_DATE)
    (generation,) = projection.generations
    (candidate,) = generation.candidates

    assert candidate.valid is True
    assert ALIAS_TOKEN_PATTERN.search(candidate.rationale) is not None, "raw provider text is kept as written"
    assert candidate.rationale_local == "由 本地员工乙 顶替请假班次，保留 本地员工乙 (RANGE_ATTENDANT/RANGE_A 17:00–21:00)。"
    assert candidate.operational_warnings_local == ("本地员工乙 需提前到岗",)
    assert ALIAS_TOKEN_PATTERN.search(candidate.rationale_local) is None
    assert candidate.action_window_end == SHIFT_END

    rendered = to_primitive(candidate)
    assert "alias" not in str(rendered).replace("rationale", "")
    assert "worker_alias" not in rendered and "assignment_alias" not in rendered
    # The provider wire the reservation froze carries no local identity.
    frozen = str(to_primitive(reservation.provider_payload))
    for private in ("本地员工", "staff-00", "assignment-"):
        assert private not in frozen, private
    # The same request projection and generation evidence render identically.
    request = owner.request_projection("suggestion-generate", "generate-1")
    assert request.candidates == generation.candidates


def test_expired_accept_is_a_closed_conflict_with_no_ledger_mutation(issued) -> None:
    root, ledger, owner, generation_id = issued
    before_bytes = _ledger_bytes(root)
    before = ledger.verify()

    refused = owner.commit_manager_response(_manager_body("ACCEPT", "accept-late"), suggestion_id=generation_id, recorded_at=SHIFT_END)
    assert refused == ConflictReceipt("manager-response", "accept-late", "EXPIRED_SUGGESTION")
    assert _ledger_bytes(root) == before_bytes
    assert ledger.verify() == before
    with pytest.raises(StaffingError) as missing:
        owner.request_projection("manager-response", "accept-late")
    assert missing.value.code == "REQUEST_NOT_FOUND"
    later = owner.commit_manager_response(_manager_body("ACCEPT", "accept-late"), suggestion_id=generation_id, recorded_at=EVENING)
    assert later == ConflictReceipt("manager-response", "accept-late", "EXPIRED_SUGGESTION")
    assert _ledger_bytes(root) == before_bytes
    assert owner.date_projection(SERVICE_DATE).effective_plan.status == "NO_PLAN"

    # One minute before the shift ends the same body is still a current action.
    committed = owner.commit_manager_response(
        _manager_body("ACCEPT", "accept-in-window"), suggestion_id=generation_id, recorded_at=SHIFT_END - timedelta(minutes=1)
    )
    assert isinstance(committed, CommittedReceipt)
    projection = owner.date_projection(SERVICE_DATE)
    assert projection.effective_plan.status == "CURRENT"
    assert projection.effective_plan.revision == 1
    assert {item.staff_id for item in projection.effective_plan_schedule} == {"staff-002"}


_DAY_ADD = {
    "operation": "ADD", "staff_id": "staff-002", "role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A",
    "start_at": "2026-10-05T09:00:00+08:00", "end_at": "2026-10-05T17:00:00+08:00",
}


def _evening_assignment_id(owner: StaffingOperations, generation_id: str) -> str:
    reservation = owner.ledger.read().reservation(generation_id)
    (assignment_id,) = [assignment_id for _, assignment_id in reservation.assignment_alias_to_assignment_id]
    return assignment_id


def test_modify_is_refused_when_any_edited_shift_has_ended(issued) -> None:
    root, ledger, owner, generation_id = issued
    before = (ledger.verify(), _ledger_bytes(root))
    refused = owner.commit_manager_response(_manager_body("MODIFY", "modify-ended", operations=[_DAY_ADD]), suggestion_id=generation_id, recorded_at=EVENING)
    assert refused == ConflictReceipt("manager-response", "modify-ended", "EXPIRED_SUGGESTION")
    assert (ledger.verify(), _ledger_bytes(root)) == before

    # At 17:30 the evening REMOVE is still open but the 09:00–17:00 ADD has
    # ended: one ended shift is enough to refuse the whole edit.
    mixed = [{"operation": "REMOVE", "assignment_id": _evening_assignment_id(owner, generation_id)}, _DAY_ADD]
    refused = owner.commit_manager_response(
        _manager_body("MODIFY", "modify-mixed", operations=mixed), suggestion_id=generation_id, recorded_at=SHIFT_END + timedelta(minutes=30)
    )
    assert refused == ConflictReceipt("manager-response", "modify-mixed", "EXPIRED_SUGGESTION")
    assert (ledger.verify(), _ledger_bytes(root)) == before
    assert owner.date_projection(SERVICE_DATE).effective_plan.status == "NO_PLAN"


def test_modify_cannot_revive_an_expired_suggestion(issued) -> None:
    root, ledger, owner, generation_id = issued
    before = (ledger.verify(), _ledger_bytes(root))
    # The edited operations are all still open (the evening shift runs until
    # 21:00), but the stored candidate's own window closed at 17:00.
    still_open = [{"operation": "REMOVE", "assignment_id": _evening_assignment_id(owner, generation_id)}]
    for request_id, at in (("modify-revive-1", SHIFT_END + timedelta(minutes=30)), ("modify-revive-2", EVENING - timedelta(minutes=1))):
        refused = owner.commit_manager_response(_manager_body("MODIFY", request_id, operations=still_open), suggestion_id=generation_id, recorded_at=at)
        assert refused == ConflictReceipt("manager-response", request_id, "EXPIRED_SUGGESTION")
        assert (ledger.verify(), _ledger_bytes(root)) == before
        with pytest.raises(StaffingError) as missing:
            owner.request_projection("manager-response", request_id)
        assert missing.value.code == "REQUEST_NOT_FOUND"
    rejected = owner.commit_manager_response(_manager_body("REJECT", "reject-after"), suggestion_id=generation_id, recorded_at=EVENING)
    assert isinstance(rejected, CommittedReceipt)
    assert owner.date_projection(SERVICE_DATE).effective_plan.status == "NO_PLAN"


def test_modify_with_open_edits_inside_the_stored_window_commits(issued) -> None:
    _, _, owner, generation_id = issued
    edits = [{"operation": "REMOVE", "assignment_id": _evening_assignment_id(owner, generation_id)}, _DAY_ADD]
    committed = owner.commit_manager_response(
        _manager_body("MODIFY", "modify-open", operations=edits), suggestion_id=generation_id, recorded_at=SHIFT_END - timedelta(minutes=30)
    )
    assert isinstance(committed, CommittedReceipt)
    plan = owner.date_projection(SERVICE_DATE).effective_plan
    assert plan.status == "CURRENT" and plan.revision == 1


def test_modify_edited_window_is_checked_when_the_stored_window_is_still_open(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    ledger, owner = _open(root)
    owner.import_roster(_roster(), recorded_at=MORNING)
    owner.record_exception(_leave(), recorded_at=MORNING)
    generation_id = _issue_suggestion(owner, recorded_at=MORNING, second_candidate_removes_evening=True)
    first, second = owner.date_projection(SERVICE_DATE).generations[0].candidates
    assert first.action_window_end == SHIFT_END
    assert second.action_window_end == EVENING, "the REMOVE-only candidate stays open until the evening shift ends"
    before = (ledger.verify(), _ledger_bytes(root))

    # Candidate 2's stored window is still open at 17:30, so the pre-check
    # passes; the edits re-add the ended 09:00–17:00 shift and are refused on
    # their own window.
    edits = [{"operation": "REMOVE", "assignment_id": _evening_assignment_id(owner, generation_id)}, _DAY_ADD]
    body = dict(_manager_body("MODIFY", "modify-second-late", operations=edits), candidate_index=2)
    refused = owner.commit_manager_response(body, suggestion_id=generation_id, recorded_at=SHIFT_END + timedelta(minutes=30))
    assert refused == ConflictReceipt("manager-response", "modify-second-late", "EXPIRED_SUGGESTION")
    assert (ledger.verify(), _ledger_bytes(root)) == before

    body = dict(_manager_body("MODIFY", "modify-second-early", operations=edits), candidate_index=2)
    committed = owner.commit_manager_response(body, suggestion_id=generation_id, recorded_at=SHIFT_END - timedelta(minutes=1))
    assert isinstance(committed, CommittedReceipt)
    plan = owner.date_projection(SERVICE_DATE).effective_plan
    assert plan.status == "CURRENT" and plan.revision == 1


def test_modify_precedence_reports_expiry_before_edit_resolution(issued) -> None:
    root, ledger, owner, generation_id = issued
    before = (ledger.verify(), _ledger_bytes(root))
    bogus = [{"operation": "REMOVE", "assignment_id": "assignment-does-not-exist"}]
    refused = owner.commit_manager_response(
        _manager_body("MODIFY", "modify-bogus-late", operations=bogus), suggestion_id=generation_id, recorded_at=SHIFT_END + timedelta(minutes=30)
    )
    assert refused == ConflictReceipt("manager-response", "modify-bogus-late", "EXPIRED_SUGGESTION")
    # Inside the stored window, invalid edits are still an INVALID_TRANSITION
    # before any edited-window comparison.
    outside_availability = [dict(_DAY_ADD, start_at="2026-10-05T02:00:00+08:00", end_at="2026-10-05T03:00:00+08:00")]
    refused = owner.commit_manager_response(
        _manager_body("MODIFY", "modify-invalid-early", operations=outside_availability), suggestion_id=generation_id, recorded_at=SHIFT_END - timedelta(minutes=30)
    )
    assert refused == ConflictReceipt("manager-response", "modify-invalid-early", "INVALID_TRANSITION")
    assert (ledger.verify(), _ledger_bytes(root)) == before


def test_accept_is_refused_once_any_affected_shift_has_ended(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    ledger, owner = _open(root)
    owner.import_roster(_roster(), recorded_at=MORNING)
    owner.record_exception(_leave(), recorded_at=MORNING)
    generation_id = _issue_suggestion(owner, recorded_at=MORNING, remove_evening=True)
    (candidate,) = owner.date_projection(SERVICE_DATE).generations[0].candidates
    assert candidate.valid is True
    assert [item.operation for item in candidate.operations] == ["ADD", "REMOVE"]
    assert candidate.action_window_end == SHIFT_END, "the 17:00 ADD end closes the window, not the 21:00 REMOVE end"
    before = (ledger.verify(), _ledger_bytes(root))

    refused = owner.commit_manager_response(_manager_body("ACCEPT", "accept-mixed"), suggestion_id=generation_id, recorded_at=SHIFT_END + timedelta(minutes=30))
    assert refused == ConflictReceipt("manager-response", "accept-mixed", "EXPIRED_SUGGESTION")
    assert (ledger.verify(), _ledger_bytes(root)) == before

    committed = owner.commit_manager_response(_manager_body("ACCEPT", "accept-mixed-early"), suggestion_id=generation_id, recorded_at=SHIFT_END - timedelta(minutes=1))
    assert isinstance(committed, CommittedReceipt)
    projection = owner.date_projection(SERVICE_DATE)
    assert projection.effective_plan.status == "CURRENT" and projection.effective_plan.revision == 1
    assert {item.staff_id for item in projection.effective_plan_schedule} == {"staff-002"}
    assert all(item.end_at != EVENING for item in projection.effective_plan_schedule)


def test_reject_remains_a_recorded_decision_after_expiry(issued) -> None:
    _, _, owner, generation_id = issued
    rejected = owner.commit_manager_response(_manager_body("REJECT", "reject-late"), suggestion_id=generation_id, recorded_at=EVENING)
    assert isinstance(rejected, CommittedReceipt)
    projection = owner.date_projection(SERVICE_DATE)
    assert projection.effective_plan.status == "NO_PLAN"
    (response,) = projection.manager_responses
    assert (response.decision, response.reason_code) == ("REJECT", "MANUAL_HANDLING")


def test_in_window_acceptance_replays_unchanged_after_reopen(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    ledger, owner = _open(root)
    owner.import_roster(_roster(), recorded_at=MORNING)
    owner.record_exception(_leave(), recorded_at=MORNING)
    generation_id = _issue_suggestion(owner, recorded_at=MORNING)
    accepted = owner.commit_manager_response(_manager_body("ACCEPT", "accept-1"), suggestion_id=generation_id, recorded_at=MORNING + timedelta(hours=1))
    assert isinstance(accepted, CommittedReceipt)
    head = ledger.verify()
    del owner, ledger

    # Replay never re-applies the admission clock: the evening reader still
    # sees the committed plan, and the window evidence is unchanged.
    reopened_ledger, reopened = _open(root)
    assert reopened_ledger.verify() == head
    projection = reopened.date_projection(SERVICE_DATE)
    assert projection.effective_plan.status == "CURRENT"
    (candidate,) = projection.generations[0].candidates
    assert candidate.action_window_end == SHIFT_END
    assert candidate.rationale_local.startswith("由 本地员工乙 顶替请假班次，保留 本地员工乙 (")
    duplicate = reopened.commit_manager_response(_manager_body("ACCEPT", "accept-1"), suggestion_id=generation_id, recorded_at=EVENING)
    assert not isinstance(duplicate, ConflictReceipt)
    assert duplicate.receipt.event_id == accepted.receipt.event_id
