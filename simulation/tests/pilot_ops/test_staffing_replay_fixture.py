"""Replay of a ledger written under prompt template ``staffing-adjustment/v1``.

The fixture under ``fixtures/staffing_v1_ledger`` was produced by the real
composition against the unmodified v1 code with a fixed audit clock, fixed
nonces, and a scripted provider double. It holds, in order: one roster import,
one LEAVE exception, one generation whose schema-valid answer was refused by
the local decoder (``INVALID_RESPONSE`` / ``invalid_provider_shape``), one
generation with a VALID candidate, the manager ACCEPT that created effective
plan revision 1, and one reservation that never reached a terminal.

Bumping the current template must keep every v1 record replayable, bind any
v1 reservation to the v1 prompt, and reserve new work under the current
template only.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nxt_pilot_ops.serialization import stable_digest, to_primitive
from nxt_pilot_ops.staffing.contracts import (
    PROMPT_TEMPLATE_VERSION,
    SUPPORTED_PROMPT_TEMPLATE_VERSIONS,
    CommittedReceipt,
    GenerationRouteEvidence,
    StaffingError,
)
from nxt_pilot_ops.staffing.ledger import EventCommit, StaffingLedger
from nxt_pilot_ops.staffing.operations import StaffingOperations
from nxt_pilot_ops.staffing.plans import build_staffing_basis
from nxt_pilot_ops.staffing.projection import (
    SYSTEM_PROMPT_V1,
    SYSTEM_PROMPT_V2,
    canonical_generation_input,
    system_prompt_for,
)
from nxt_pilot_ops.staffing.prompt import build_prompt


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "staffing_v1_ledger"
SITE_ID = "site-cn-1"
DEPLOYMENT_ID = "deployment-1"
SERVICE_DATE = date(2026, 10, 6)
NOW = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
V1 = "staffing-adjustment/v1"


def _restore(tmp_path: Path) -> Path:
    root = tmp_path / "stable" / "staffing-v1"
    root.mkdir(parents=True, mode=0o700)
    for name in ("staffing.jsonl", "staffing.anchor.json"):
        target = root / name
        shutil.copyfile(FIXTURE / name, target)
        os.chmod(target, 0o600)
    os.chmod(root, 0o700)
    return root


def _open(tmp_path: Path) -> tuple[StaffingLedger, StaffingOperations]:
    ledger = StaffingLedger(_restore(tmp_path), site_id=SITE_ID, deployment_id=DEPLOYMENT_ID)
    owner = StaffingOperations(
        ledger,
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
        site_timezone="Asia/Shanghai",
    )
    return ledger, owner


def test_fixture_is_a_pure_v1_ledger() -> None:
    assert PROMPT_TEMPLATE_VERSION != V1
    assert V1 in SUPPORTED_PROMPT_TEMPLATE_VERSIONS
    records = [json.loads(line) for line in (FIXTURE / "staffing.jsonl").read_bytes().splitlines()]
    reservations = [row for row in records if row["event_type"] == "generation_reserved"]
    assert len(records) == 12
    assert len(reservations) == 3
    assert {row["payload"]["prompt_template_version"] for row in reservations} == {V1}
    anchor = json.loads((FIXTURE / "staffing.anchor.json").read_text(encoding="utf-8"))
    assert anchor["record_count"] == 12
    assert anchor["head_hash"] == records[-1]["record_hash"]


def test_v1_ledger_verifies_and_projects_every_terminal_after_the_bump(tmp_path: Path) -> None:
    ledger, owner = _open(tmp_path)
    count, head = ledger.verify()
    assert count == 12

    invalid = owner.request_projection("suggestion-generate", "fixture-generation-invalid")
    assert invalid.lifecycle_state == "INVALID_RESPONSE"
    assert invalid.generation is not None
    assert invalid.generation.failure_code == "invalid_provider_shape"
    assert invalid.candidates == ()

    issued = owner.request_projection("suggestion-generate", "fixture-generation-valid")
    assert issued.lifecycle_state == "SUCCEEDED"
    assert [item.candidate_index for item in issued.candidates] == [1]
    assert issued.candidates[0].rejection_codes == ()
    assert issued.manager_response is not None
    assert issued.manager_response.decision == "ACCEPT"
    assert issued.manager_response.effective_plan_revision == 1

    projection = owner.date_projection(SERVICE_DATE)
    assert projection.roster_revision == 1
    assert projection.exception_set_revision == 1
    assert len(projection.exceptions) == 1
    assert projection.effective_plan.revision == 1
    assert projection.effective_plan.status == "CURRENT"
    assert projection.effective_plan_schedule is not None
    assert {row.staff_id for row in projection.effective_plan_schedule} == {"staff-002"}
    assert ledger.verify() == (count, head)


def test_v1_reservation_still_binds_the_v1_prompt_and_digest(tmp_path: Path) -> None:
    _, owner = _open(tmp_path)
    unfinished = owner.request_projection("suggestion-generate", "fixture-generation-unfinished")
    assert unfinished.lifecycle_state == "RESERVED"
    assert unfinished.generation_id is not None
    work = owner.generation_work(unfinished.generation_id)
    assert work.basis_snapshot.prompt_template_version == V1
    system, _ = build_prompt(work)
    assert system["content"] == SYSTEM_PROMPT_V1 == system_prompt_for(V1)
    assert SYSTEM_PROMPT_V2 == system_prompt_for(PROMPT_TEMPLATE_VERSION)
    semantic = canonical_generation_input(work.basis_snapshot, work.provider_payload, V1)
    assert semantic["template_version"] == V1
    assert stable_digest(to_primitive(semantic)) == work.input_digest
    # The v1 projection cannot be re-rendered under the current template.
    with pytest.raises(StaffingError) as refused:
        canonical_generation_input(work.basis_snapshot, work.provider_payload, PROMPT_TEMPLATE_VERSION)
    assert refused.value.code == "staffing_invalid_evidence"


def test_restart_recovers_the_unfinished_v1_reservation_and_reserves_v2_retry(tmp_path: Path) -> None:
    ledger, owner = _open(tmp_path)
    recovered = owner.recover_interrupted_generations(recorded_at=NOW)
    assert len(recovered) == 1 and type(recovered[0]) is EventCommit
    unfinished = owner.request_projection("suggestion-generate", "fixture-generation-unfinished")
    assert unfinished.lifecycle_state == "RESULT_UNKNOWN"

    retry = owner.reserve_generation(
        {
            "schema": "nxt-staffing-suggestion-generate/v1",
            "request_id": "fixture-generation-retry",
            "operator": "manager-1",
            "service_date": SERVICE_DATE.isoformat(),
            "expected_revisions": {"roster": 1, "exception_set": 1, "effective_plan": 1},
            "retry_of": unfinished.generation_id,
        },
        alias_nonce=b"r" * 32,
        route_evidence=GenerationRouteEvidence("CN", "READY", "KIMI", "kimi-model", None, None),
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        language="zh-CN",
        recorded_at=NOW,
    )
    assert type(retry) is CommittedReceipt
    new_work = owner.generation_work(
        owner.request_projection("suggestion-generate", "fixture-generation-retry").generation_id
    )
    assert new_work.basis_snapshot.prompt_template_version == PROMPT_TEMPLATE_VERSION
    assert build_prompt(new_work)[0]["content"] == SYSTEM_PROMPT_V2
    assert ledger.verify()[0] == 14

    # Reserving under the superseded template is refused; the ledger holds.
    with pytest.raises(StaffingError) as refused:
        owner.reserve_generation(
            {
                "schema": "nxt-staffing-suggestion-generate/v1",
                "request_id": "fixture-generation-v1-again",
                "operator": "manager-1",
                "service_date": SERVICE_DATE.isoformat(),
                "expected_revisions": {"roster": 1, "exception_set": 1, "effective_plan": 1},
                "retry_of": None,
            },
            alias_nonce=b"s" * 32,
            route_evidence=GenerationRouteEvidence("CN", "READY", "KIMI", "kimi-model", None, None),
            prompt_template_version=V1,
            language="zh-CN",
            recorded_at=NOW,
        )
    assert refused.value.code == "staffing_invalid_evidence"
    assert ledger.verify()[0] == 14


def test_basis_builder_accepts_only_supported_template_versions(tmp_path: Path) -> None:
    ledger, _ = _open(tmp_path)
    history = ledger.read()
    for version in sorted(SUPPORTED_PROMPT_TEMPLATE_VERSIONS):
        snapshot = build_staffing_basis(history, SERVICE_DATE, prompt_template_version=version)
        assert snapshot.prompt_template_version == version
    assert build_staffing_basis(history, SERVICE_DATE).prompt_template_version == PROMPT_TEMPLATE_VERSION
    for bad in ("staffing-adjustment/v0", "", None, 2):
        with pytest.raises(StaffingError) as refused:
            build_staffing_basis(history, SERVICE_DATE, prompt_template_version=bad)  # type: ignore[arg-type]
        assert refused.value.code == "staffing_invalid_evidence"
