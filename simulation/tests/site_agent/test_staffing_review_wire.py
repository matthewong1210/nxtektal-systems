"""Wire and API regressions for candidate review: local explanations, the
action window, the ``actionability`` label, and the ``staffing_suggestion_expired``
refusal, over an isolated synthetic ledger with a movable audit clock."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nxt_model_gateway import DeploymentRegion, ModelGateway, Provider, ProviderConfig, RoutePolicy
from nxt_model_gateway.kimi import KimiAdapter
from nxt_pilot_ops.staffing.ledger import StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_site_agent import SiteAgentError

from scripts.staffing_operations import (
    BoundedGenerationWorker,
    ConfiguredGateway,
    StaffingApiOperations,
    StaffingProjectionError,
    StaffingRouteAdapter,
    candidate_to_wire,
)
from tests.site_agent.test_staffing_composition import (
    FLOW_SERVICE_DATE,
    AliasAwareTransport,
    _free_worker_alias,
    candidate,
    cn_settings,
    flow_generation,
    flow_leave,
    flow_roster,
    validator,
    wait_for_state,
)

WINDOW_END = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)  # candidate() ends at 10:00Z
MORNING = datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc)  # 08:30 Asia/Shanghai
EVENING = datetime(2026, 10, 6, 13, 0, tzinfo=timezone.utc)  # 21:00 Asia/Shanghai


def test_candidate_wire_labels_actionability_from_the_review_clock() -> None:
    schema = validator("#/$defs/CandidateProjection")
    current = candidate_to_wire(candidate(), now=WINDOW_END - timedelta(microseconds=1))
    expired = candidate_to_wire(candidate(), now=WINDOW_END)
    schema.validate(current)
    schema.validate(expired)
    assert current["actionability"] == "CURRENT"
    assert expired["actionability"] == "EXPIRED"
    assert current["action_window_end_at"] == "2026-10-06T10:00:00Z"
    assert current["rationale_local"] == "coverage restored"
    assert current["operational_warnings_local"] == []
    # Only the review label differs between the two readings.
    assert {key: value for key, value in current.items() if key != "actionability"} == {
        key: value for key, value in expired.items() if key != "actionability"
    }
    # The site offset is a presentation of the same instant.
    shifted = candidate_to_wire(candidate(), now=WINDOW_END.astimezone(timezone(timedelta(hours=8))))
    assert shifted["actionability"] == "EXPIRED"

    with pytest.raises(StaffingProjectionError):
        candidate_to_wire(candidate(), now=datetime(2026, 10, 6, 10))
    with pytest.raises(StaffingProjectionError):
        candidate_to_wire(replace(candidate(), rationale_local="x" * 2001), now=WINDOW_END)
    with pytest.raises(StaffingProjectionError):
        candidate_to_wire(replace(candidate(), operational_warnings_local=("extra",)), now=WINDOW_END)
    with pytest.raises(StaffingProjectionError):
        candidate_to_wire(replace(candidate(), rationale_local="line\nbreak"), now=WINDOW_END)


class MovableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _aliased_answer(wire: dict[str, object]) -> dict[str, object]:
    worker_alias = _free_worker_alias(wire)
    assignment_alias = wire["assignments"][0]["assignment_alias"]
    return {
        "candidates": [
            {
                "candidate_index": 1,
                "operations": [
                    {
                        "operation": "ADD",
                        "worker_alias": worker_alias,
                        "role_code": "RANGE_ATTENDANT",
                        "area_code": "RANGE_A",
                        "start_at": f"{FLOW_SERVICE_DATE}T09:00:00+08:00",
                        "end_at": f"{FLOW_SERVICE_DATE}T17:00:00+08:00",
                    }
                ],
                "rationale": f"由 {worker_alias} 顶替请假班次，保留 {assignment_alias}。",
                "operational_warnings": [f"{worker_alias} 需提前到岗"],
            }
        ]
    }


def _evening_roster(request_id: str) -> dict[str, object]:
    payload = flow_roster(request_id, 0)
    payload["workers"][1]["max_daily_minutes"] = 720
    payload["availability"][1]["end_local"] = "21:00"
    payload["regular_assignments"].append(
        {
            "staff_id": "staff-002",
            "weekday": 1,
            "role_code": "RANGE_ATTENDANT",
            "area_code": "RANGE_A",
            "start_local": "17:00",
            "end_local": "21:00",
        }
    )
    return payload


def _flow(tmp_path: Path, clock: MovableClock):
    transport = AliasAwareTransport([_aliased_answer])
    gateway = ModelGateway(
        kimi=KimiAdapter(config=ProviderConfig(Provider.KIMI, "kimi-model", "secret"), transport=transport),
        monotonic=time.monotonic,
    )
    route = RoutePolicy(DeploymentRegion.CN)
    configured = ConfiguredGateway(gateway, route, gateway.readiness(route))
    ledger = StaffingLedger(tmp_path / "flow", site_id="site-cn-1", deployment_id="deployment-1")
    owner = StaffingOperations(ledger, site_id="site-cn-1", deployment_id="deployment-1", site_timezone="Asia/Shanghai")
    nonces = iter(bytes([value]) * 32 for value in range(1, 20))
    worker = BoundedGenerationWorker(
        owner=owner,
        configured=configured,
        settings=cn_settings(),
        audit_clock=clock,
        nonce_factory=lambda: next(nonces),
    )
    router = StaffingRouteAdapter(
        owner=owner,
        worker=worker,
        configured=configured,
        site_id="site-cn-1",
        deployment_id="deployment-1",
        site_timezone="Asia/Shanghai",
        audit_clock=clock,
    )
    return StaffingApiOperations(router=router, worker=worker, ledger=ledger), ledger, transport


def _manager(kind: str, request_id: str) -> dict[str, object]:
    reason = {"ACCEPT": "APPROVED", "REJECT": "MANUAL_HANDLING"}[kind]
    return {
        "schema": "nxt-staffing-manager-response/v1",
        "request_id": request_id,
        "operator": "manager-1",
        "kind": kind,
        "expected_revisions": {"roster": 1, "exception_set": 1, "effective_plan": 0},
        "candidate_index": None if kind == "REJECT" else 1,
        "edited_operations": None,
        "reason_code": reason,
        "note": None,
    }


def test_synthetic_flow_renders_local_explanations_and_refuses_an_ended_shift(tmp_path: Path) -> None:
    clock = MovableClock(MORNING)
    api, ledger, transport = _flow(tmp_path, clock)
    try:
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        api.route("POST", "/api/v1/staffing/exceptions", flow_leave("leave-1", roster=1, exception_set=0))
        api.route("POST", "/api/v1/staffing/suggestions", flow_generation("generate-1", roster=1, exception_set=1, effective_plan=0))
        issued = wait_for_state(api, "generate-1", {"SUCCEEDED"})
        validator("#/$defs/StaffingReceipt").validate(issued)
        (row,) = issued["record"]["candidates"]

        # Readable to a supervisor, raw provider text preserved, nothing local on the provider wire.
        assert row["status"] == "VALID"
        assert "worker_" in row["rationale"] and "assignment_" in row["rationale"]
        assert row["rationale_local"] == "由 Synthetic Worker Two 顶替请假班次，保留 Synthetic Worker Two (RANGE_ATTENDANT/RANGE_A 17:00–21:00)。"
        assert row["operational_warnings_local"] == ["Synthetic Worker Two 需提前到岗"]
        assert row["action_window_end_at"] == f"{FLOW_SERVICE_DATE}T09:00:00Z"  # 17:00 site time, rendered in UTC
        assert row["actionability"] == "CURRENT"
        sent = transport.bodies[0].decode("utf-8")
        for private in ("Operator One", "Synthetic Worker Two", "staff-001", "staff-002", "assignment-"):
            assert private not in sent, private

        snapshot = api.route("GET", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}", {})
        validator("#/$defs/StaffingDateSnapshot").validate(snapshot)
        assert snapshot["generations"][-1]["candidates"][0]["actionability"] == "CURRENT"

        # After the shift has ended the same evidence is historical.
        clock.now = EVENING
        before = ledger.verify()
        snapshot = api.route("GET", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}", {})
        validator("#/$defs/StaffingDateSnapshot").validate(snapshot)
        assert snapshot["server_time_utc"] == "2026-10-06T13:00:00.000000Z"
        (expired_row,) = snapshot["generations"][-1]["candidates"]
        assert expired_row["actionability"] == "EXPIRED"
        assert expired_row["status"] == "VALID", "validity is evidence; only actionability changed"
        suggestion_id = issued["record"]["suggestion_id"]

        with pytest.raises(SiteAgentError) as refused:
            api.route("POST", f"/api/v1/staffing/suggestions/{suggestion_id}/accept", _manager("ACCEPT", "accept-late"))
        assert refused.value.code == "staffing_suggestion_expired"
        assert "staff-00" not in refused.value.detail
        assert ledger.verify() == before
        with pytest.raises(SiteAgentError) as missing:
            api.route("GET", "/api/v1/staffing/requests/manager-response/accept-late", {})
        assert missing.value.code == "staffing_request_not_found"
        snapshot = api.route("GET", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}", {})
        assert snapshot["effective_plan"] is None
        assert snapshot["generations"][-1]["manager_response"] is None

        # Reject still records the decision; the plan stays empty.
        rejected = api.route("POST", f"/api/v1/staffing/suggestions/{suggestion_id}/reject", _manager("REJECT", "reject-late"))
        assert rejected["state"] == "COMMITTED" and rejected["record"]["effective_plan"] is None
        assert ledger.verify() != before
    finally:
        api.close()


def test_synthetic_flow_accepts_the_same_candidate_inside_its_window(tmp_path: Path) -> None:
    clock = MovableClock(MORNING)
    api, _, _ = _flow(tmp_path, clock)
    try:
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        api.route("POST", "/api/v1/staffing/exceptions", flow_leave("leave-1", roster=1, exception_set=0))
        api.route("POST", "/api/v1/staffing/suggestions", flow_generation("generate-1", roster=1, exception_set=1, effective_plan=0))
        issued = wait_for_state(api, "generate-1", {"SUCCEEDED"})
        suggestion_id = issued["record"]["suggestion_id"]
        clock.now = datetime(2026, 10, 6, 8, 59, tzinfo=timezone.utc)  # 16:59 site time
        accepted = api.route("POST", f"/api/v1/staffing/suggestions/{suggestion_id}/accept", _manager("ACCEPT", "accept-1"))
        assert accepted["state"] == "COMMITTED"
        assert accepted["record"]["effective_plan"]["revision"] == 1
        clock.now = EVENING
        snapshot = api.route("GET", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}", {})
        assert snapshot["effective_plan"]["status"] == "CURRENT"
        (row,) = snapshot["generations"][-1]["candidates"]
        assert row["actionability"] == "EXPIRED"
        assert snapshot["generations"][-1]["manager_response"]["response_kind"] == "ACCEPT"
    finally:
        api.close()
