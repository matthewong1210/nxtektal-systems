"""The Pilot Site Agent service shell.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

``SiteAgentService`` is the local application boundary around one
readiness-gated, fixture-backed Agent Runtime composition:

- it refuses to launch without a verified READY enablement report and
  a structurally valid fixture-only Shadow Mode launch plan;
- it drives the existing runtime one bounded cycle at a time and never
  re-implements any evaluation, admission, or workflow semantics;
- it persists the fixture source cursor after every cycle so a
  restarted process resumes deterministically instead of replaying;
- it exposes noncanonical projections (health, state, evaluations,
  recommendations, briefing) over existing canonical evidence; and
- it transports existing manager workflow operations (accept, reject,
  modify) whose legality the existing ledger enforces.

The service owns no observation, state, policy, recommendation,
trace, workflow, ledger, checkpoint, or physical command semantics.
Manager acceptance remains human workflow evidence only; nothing here
can reach a robot, an actuator, or an emergency-stop surface.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nxt_agent_runtime import (
    AgentRuntimeError,
    CycleOutcome,
    EvaluationJournal,
    ManagerDecisionQueueError,
)
from nxt_pilot_ops.contracts import RecommendationAction
from nxt_pilot_ops.ledger import JsonlEventLedger, LedgerTransitionError
from nxt_pilot_ops.serialization import to_primitive
from nxt_workflow_enablement import (
    RANGE_OPS_WORKFLOW_ID,
    RangeOpsLaunchPlan,
    ReadinessVerdict,
    RuntimeMode,
    TransportMode,
    WorkflowEnablementError,
    verify_report_payload,
)

from .briefing import briefing_projection
from .contracts import (
    ComposedRuntime,
    CompositionSeam,
    DISCLAIMER,
    LaunchMaterials,
    LaunchRefusedError,
    SERVICE_MODE_LABEL,
    ServiceState,
    SiteAgentError,
    SourceCursor,
)
from .projections import (
    evaluation_projection,
    no_state_projection,
    recommendation_projection,
    state_projection,
)
from .state_files import ServiceStorage

_RUN_DIR_PREFIX = "run-"


class _ServiceRuntimeSink:
    """Best-effort visibility sink; never a decision or control port.

    Captures per-cycle publish/reject notifications so the service can
    log noncanonical diagnostics.  Every callback swallows nothing and
    raises nothing of its own; the state orchestration layer already
    isolates sink errors from the canonical loop.
    """

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.published.clear()
        self.rejections.clear()

    def on_published(self, envelope: Any) -> None:
        self.published.append(
            {
                "envelope_id": getattr(envelope, "envelope_id", None),
                "sequence_number": getattr(envelope, "sequence_number", None),
                "observation_timestamp_s": getattr(
                    envelope, "observation_timestamp_s", None
                ),
            }
        )

    def on_rejected(self, failure: Any) -> None:
        code = getattr(failure, "code", None)
        self.rejections.append(
            {
                "failure_code": getattr(code, "value", None),
                "stage": getattr(
                    getattr(failure, "stage", None), "value", None
                ),
                "detail": getattr(failure, "detail", None),
                "sequence_number": getattr(failure, "sequence_number", None),
                "observation_timestamp_s": getattr(
                    failure, "observation_timestamp_s", None
                ),
                "retryable": getattr(failure, "retryable", None),
            }
        )


def _plain(value: Any) -> Any:
    """Deep-copy mappings/sequences into JSON-serializable plain types."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _run_number(name: str) -> int | None:
    if not name.startswith(_RUN_DIR_PREFIX):
        return None
    suffix = name[len(_RUN_DIR_PREFIX):]
    if suffix.isdigit() and len(suffix) == 3:
        return int(suffix)
    return None


def _existing_run_numbers(runs_root: Path) -> tuple[int, ...]:
    try:
        if not runs_root.exists():
            return ()
        numbers = sorted(
            number
            for child in runs_root.iterdir()
            if child.is_dir()
            and (number := _run_number(child.name)) is not None
        )
        return tuple(numbers)
    except OSError as exc:
        raise SiteAgentError(
            "service_state_unavailable",
            f"cannot inspect the runs directory: {exc}",
        ) from exc


def _run_dir(runs_root: Path, number: int) -> Path:
    return runs_root / f"{_RUN_DIR_PREFIX}{number:03d}"


class SiteAgentService:
    """One launched, lockable Pilot Site Agent service instance."""

    def __init__(
        self,
        *,
        storage: ServiceStorage,
        seam: CompositionSeam,
        plan: RangeOpsLaunchPlan,
        report_payload: Mapping[str, Any],
        composed: ComposedRuntime,
        runs_root: Path,
        run_number: int,
        sink: _ServiceRuntimeSink,
        resumed: bool,
    ) -> None:
        self._lock = threading.RLock()
        self._storage = storage
        self._seam = seam
        self._plan = plan
        self._report_payload = dict(report_payload)
        self._composed = composed
        self._runs_root = Path(runs_root)
        self._run_number = run_number
        self._sink = sink
        self._state = ServiceState.SERVING
        self._failure: tuple[str, str] | None = None
        self._event_append_failures = 0
        self._last_delivered_observation_ts: float | None = None
        self._simulation_midnight = datetime.fromisoformat(
            plan.simulation_midnight_iso
        )
        self._recover_or_fail(resumed=resumed)

    # -- launch ----------------------------------------------------------

    @classmethod
    def launch(
        cls,
        *,
        runs_root: Path,
        site_id: str,
        deployment_id: str,
        workflow_id: str,
        seam: CompositionSeam,
        force_fresh: bool = False,
    ) -> "SiteAgentService":
        """Fresh-launch or resume the service under ``runs_root``.

        Fresh launches evaluate readiness through the composition seam
        against a provably empty evidence root and refuse on anything
        but a verified READY verdict.  Resume revalidates the persisted
        plan and report bytes and the persisted source cursor; an
        evidence root that cannot be proven fresh or resumable is a
        collision and launch fails closed.
        """
        runs_root = Path(runs_root)
        numbers = _existing_run_numbers(runs_root)
        if force_fresh or not numbers:
            next_number = (numbers[-1] + 1) if numbers else 1
            return cls._launch_fresh(
                runs_root=runs_root,
                run_number=next_number,
                site_id=site_id,
                deployment_id=deployment_id,
                workflow_id=workflow_id,
                seam=seam,
            )
        latest = numbers[-1]
        storage = ServiceStorage(
            _run_dir(runs_root, latest),
            site_id=site_id,
            deployment_id=deployment_id,
            workflow_id=workflow_id,
        )
        if storage.workflow_root_is_empty() and not storage.has_service_records():
            return cls._launch_fresh(
                runs_root=runs_root,
                run_number=latest,
                site_id=site_id,
                deployment_id=deployment_id,
                workflow_id=workflow_id,
                seam=seam,
            )
        if not storage.has_service_records():
            raise LaunchRefusedError(
                "evidence_root_collision",
                "the latest run directory holds evidence without service "
                "launch records; refusing to guess a resume position "
                f"({storage.workflow_evidence_root})",
            )
        return cls._resume(
            runs_root=runs_root,
            run_number=latest,
            storage=storage,
            seam=seam,
        )

    @classmethod
    def _launch_fresh(
        cls,
        *,
        runs_root: Path,
        run_number: int,
        site_id: str,
        deployment_id: str,
        workflow_id: str,
        seam: CompositionSeam,
    ) -> "SiteAgentService":
        storage = ServiceStorage(
            _run_dir(runs_root, run_number),
            site_id=site_id,
            deployment_id=deployment_id,
            workflow_id=workflow_id,
        )
        if not storage.workflow_root_is_empty():
            raise LaunchRefusedError(
                "evidence_root_collision",
                "a fresh launch requires an empty workflow evidence root: "
                f"{storage.workflow_evidence_root}",
            )
        try:
            materials = seam.materials_for(storage.workflow_evidence_root)
        except (WorkflowEnablementError, SiteAgentError) as exc:
            raise LaunchRefusedError(
                "workflow_not_ready",
                f"readiness evaluation refused a launch plan: {exc}",
            ) from exc
        report_payload = cls._verify_materials(materials, storage=storage)
        storage.write_ready_report(materials.report_canonical_json)
        storage.write_launch_record(
            report_id=str(report_payload.get("report_id")),
            plan_payload=asdict(materials.plan),
        )
        cursor = SourceCursor(consumed_cycles=0, next_sequence_number=0)
        storage.write_cursor(cursor)
        sink = _ServiceRuntimeSink()
        composed = seam.composer(
            materials.plan, storage.workflow_evidence_root, cursor, sink
        )
        service = cls(
            storage=storage,
            seam=seam,
            plan=materials.plan,
            report_payload=report_payload,
            composed=composed,
            runs_root=runs_root,
            run_number=run_number,
            sink=sink,
            resumed=False,
        )
        return service

    @classmethod
    def _resume(
        cls,
        *,
        runs_root: Path,
        run_number: int,
        storage: ServiceStorage,
        seam: CompositionSeam,
    ) -> "SiteAgentService":
        launch_record = storage.read_launch_record()
        cursor = storage.read_cursor()
        plan_payload = dict(launch_record["plan"])
        if isinstance(plan_payload.get("evidence_paths"), list):
            plan_payload["evidence_paths"] = tuple(
                plan_payload["evidence_paths"]
            )
        try:
            plan = RangeOpsLaunchPlan(**plan_payload)
        except (TypeError, WorkflowEnablementError) as exc:
            raise LaunchRefusedError(
                "service_state_invalid",
                f"the persisted launch plan is not valid: {exc}",
            ) from exc
        report_text = storage.read_ready_report_text()
        materials = LaunchMaterials(
            plan=plan, report_canonical_json=report_text
        )
        report_payload = cls._verify_materials(materials, storage=storage)
        if str(report_payload.get("report_id")) != launch_record.get(
            "report_id"
        ):
            raise LaunchRefusedError(
                "service_state_invalid",
                "the stored readiness report does not match the launch "
                "record's report identity",
            )
        sink = _ServiceRuntimeSink()
        composed = seam.composer(
            plan, storage.workflow_evidence_root, cursor, sink
        )
        return cls(
            storage=storage,
            seam=seam,
            plan=plan,
            report_payload=report_payload,
            composed=composed,
            runs_root=runs_root,
            run_number=run_number,
            sink=sink,
            resumed=True,
        )

    @staticmethod
    def _verify_materials(
        materials: LaunchMaterials, *, storage: ServiceStorage
    ) -> dict[str, Any]:
        """Re-verify every structural readiness claim the service can."""
        plan = materials.plan
        try:
            payload = json.loads(materials.report_canonical_json)
        except ValueError as exc:
            raise LaunchRefusedError(
                "report_invalid",
                f"the readiness report is not valid JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise LaunchRefusedError(
                "report_invalid", "the readiness report must be an object"
            )
        try:
            verify_report_payload(payload)
        except WorkflowEnablementError as exc:
            raise LaunchRefusedError(
                "report_invalid", f"readiness report verification failed: {exc}"
            ) from exc
        if plan.workflow_id != RANGE_OPS_WORKFLOW_ID:
            raise LaunchRefusedError(
                "workflow_not_supported",
                f"workflow {plan.workflow_id!r} has no v0 service",
            )
        if (payload.get("site_id"), payload.get("deployment_id")) != (
            plan.site_id,
            plan.deployment_id,
        ) or (plan.site_id, plan.deployment_id) != (
            storage.site_id,
            storage.deployment_id,
        ):
            raise LaunchRefusedError(
                "identity_mismatch",
                "report, plan, and storage identities do not agree",
            )
        if plan.workflow_id != storage.workflow_id:
            raise LaunchRefusedError(
                "identity_mismatch",
                "plan workflow does not match the storage workflow",
            )
        workflows = payload.get("workflows")
        section = (
            workflows.get(plan.workflow_id)
            if isinstance(workflows, dict)
            else None
        )
        if not isinstance(section, dict):
            raise LaunchRefusedError(
                "report_invalid",
                "the readiness report has no section for the workflow",
            )
        verdict = section.get("verdict")
        if verdict != ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE.value:
            failures = section.get("failures") or []
            missing = section.get("missing") or []
            raise LaunchRefusedError(
                "workflow_not_ready",
                f"workflow verdict is {verdict!r}; "
                f"failures={list(failures)!r} missing={list(missing)!r}",
            )
        if section.get("runtime_assembly_eligible") is not True:
            raise LaunchRefusedError(
                "workflow_not_ready",
                "the readiness report does not mark the workflow eligible "
                "for runtime assembly",
            )
        if plan.transport_mode != TransportMode.FIXTURE_ONLY.value:
            raise LaunchRefusedError(
                "transport_not_fixture_only",
                "the v0 service composes fixture-only transports",
            )
        if plan.runtime_mode != RuntimeMode.SHADOW.value:
            raise LaunchRefusedError(
                "runtime_not_shadow",
                "the v0 service runs Shadow Mode only",
            )
        return payload

    # -- internal helpers ------------------------------------------------

    def _recover_or_fail(self, *, resumed: bool) -> None:
        try:
            recovery = self._composed.runtime.recover()
        except AgentRuntimeError as exc:
            self._fail(exc.incident_code, exc.detail)
            self._append_event(
                {
                    "event": "failure",
                    "code": exc.incident_code,
                    "detail": exc.detail,
                    "observation_timestamp_s": None,
                }
            )
            return
        status = self._composed.runtime.status()
        self._last_delivered_observation_ts = (
            status.last_observation_timestamp_s
        )
        self._append_event(
            {
                "event": "resumed" if resumed else "launched",
                "text": (
                    "Service resumed from persisted evidence and source "
                    "cursor."
                    if resumed
                    else "Service launched fresh from a READY enablement "
                    "report."
                ),
                "observation_timestamp_s": (
                    status.last_observation_timestamp_s
                ),
                "cursor": self._composed.cursor().to_dict(),
                "recovery": {
                    "journal_record_count": recovery.journal_record_count,
                    "ledger_record_count": recovery.ledger_record_count,
                    "last_published_sequence": (
                        recovery.last_published_sequence
                    ),
                    "last_evaluated_sequence": (
                        recovery.last_evaluated_sequence
                    ),
                    "pending_decision_count": (
                        recovery.pending_decision_count
                    ),
                },
            }
        )

    def _fail(self, code: str, detail: str) -> None:
        self._state = ServiceState.FAILED
        self._failure = (code, detail)

    def _append_event(self, event: Mapping[str, Any]) -> None:
        if not self._storage.append_event(event):
            self._event_append_failures += 1

    def _scenario_now_s(self) -> float | None:
        status = self._composed.runtime.status()
        candidates = [
            value
            for value in (
                status.last_observation_timestamp_s,
                self._last_delivered_observation_ts,
            )
            if value is not None
        ]
        return max(candidates) if candidates else None

    def _scenario_now(self) -> datetime | None:
        now_s = self._scenario_now_s()
        if now_s is None:
            return None
        return self._simulation_midnight + timedelta(seconds=now_s)

    def _ledger_reader(self) -> JsonlEventLedger:
        return JsonlEventLedger(
            self._storage.workflow_evidence_root / "ledger.jsonl"
        )

    def _latest_envelope(self) -> dict[str, Any] | None:
        path = self._storage.workflow_evidence_root / "snapshots.jsonl"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        latest: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("envelope_id"):
                latest = payload
        return latest

    def _ledger_index(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, float],
    ]:
        """Index issuance traces, recommendations, and responses by ID."""
        traces: dict[str, dict[str, Any]] = {}
        recommendations: dict[str, dict[str, Any]] = {}
        responses: dict[str, dict[str, Any]] = {}
        response_scenario_seconds: dict[str, float] = {}
        try:
            records = self._ledger_reader().records()
        except (OSError, ValueError, RuntimeError):
            return traces, recommendations, responses, response_scenario_seconds
        for record in records:
            payload = record.payload
            if record.event_type == "recommendation_issued":
                recommendation = payload.get("recommendation")
                trace = payload.get("decision_trace")
                if isinstance(recommendation, Mapping):
                    key = str(recommendation.get("recommendation_id"))
                    recommendations[key] = _plain(recommendation)
                    if isinstance(trace, Mapping):
                        traces[key] = _plain(trace)
            elif record.event_type == "recommendation_response":
                response = payload.get("response")
                if isinstance(response, Mapping):
                    key = str(response.get("recommendation_id"))
                    responses[key] = _plain(response)
                    try:
                        occurred = datetime.fromisoformat(
                            str(record.occurred_at)
                        )
                        response_scenario_seconds[key] = (
                            occurred - self._simulation_midnight
                        ).total_seconds()
                    except ValueError:
                        pass
        return traces, recommendations, responses, response_scenario_seconds

    # -- read-only projections -------------------------------------------

    @property
    def plan(self) -> RangeOpsLaunchPlan:
        return self._plan

    @property
    def storage(self) -> ServiceStorage:
        return self._storage

    def health_snapshot(self) -> dict[str, Any]:
        """Noncanonical service diagnostics; never raises."""
        with self._lock:
            status = self._composed.runtime.status()
            try:
                cursor: dict[str, Any] | None = (
                    self._composed.cursor().to_dict()
                )
            except Exception:  # noqa: BLE001 - diagnostics must not raise
                cursor = None
            declared = len(self._seam.cycle_catalog)
            failure_code, failure_detail = (
                self._failure if self._failure else (None, None)
            )
            return {
                "service_state": self._state.value,
                "mode_label": SERVICE_MODE_LABEL,
                "fixture_mode": True,
                "source_type": "fixture",
                "degraded": bool(
                    status.degraded
                    or self._event_append_failures
                    or self._state is ServiceState.FAILED
                ),
                "site_id": self._plan.site_id,
                "deployment_id": self._plan.deployment_id,
                "workflow_id": self._plan.workflow_id,
                "workflow_readiness": (
                    ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE.value
                ),
                "report_id": self._report_payload.get("report_id"),
                "run_directory": self._storage.run_root.name,
                "runtime": status.to_dict(),
                "source": {
                    "cursor": cursor,
                    "declared_cycles": declared,
                    "exhausted": status.source_exhausted,
                    "max_cycles": self._plan.max_cycles,
                },
                "pending_recommendation_count": (
                    status.pending_decision_count
                ),
                "last_failure_code": (
                    failure_code or status.last_failure_code
                ),
                "last_failure_detail": (
                    failure_detail or status.last_failure_detail
                ),
                "event_append_failures": self._event_append_failures,
            }

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            envelope = self._latest_envelope()
            if envelope is None:
                return no_state_projection(
                    "no snapshot envelope has been published yet"
                )
            return state_projection(
                envelope, scenario_now_s=self._scenario_now_s()
            )

    def evaluations_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            journal_records = self._journal_records()
            traces, _, _, _ = self._ledger_index()
            return [
                evaluation_projection(
                    record,
                    ledger_trace=traces.get(record.recommendation_id or ""),
                )
                for record in journal_records
            ]

    def _journal_records(self):
        journal = EvaluationJournal(
            self._storage.workflow_evidence_root / "evaluations.jsonl"
        )
        try:
            return journal.read()
        except (OSError, ValueError, RuntimeError) as exc:
            raise SiteAgentError(
                "journal_unreadable",
                f"cannot read the evaluation journal: {exc}",
            ) from exc

    def recommendations_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                entries = self._composed.runtime.queue.entries()
            except (ValueError, RuntimeError) as exc:
                raise SiteAgentError(
                    "queue_unreadable",
                    f"cannot read the manager decision queue: {exc}",
                ) from exc
            traces, recommendations, responses, _ = self._ledger_index()
            return [
                recommendation_projection(
                    entry,
                    trace=traces.get(entry.recommendation_id),
                    recommendation=recommendations.get(
                        entry.recommendation_id
                    ),
                    response=responses.get(entry.recommendation_id),
                )
                for entry in entries
            ]

    def briefing_snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self.state_snapshot()
            health = self.health_snapshot()
            try:
                evaluations = self.evaluations_snapshot()
            except SiteAgentError:
                evaluations = []
            try:
                recommendations = self.recommendations_snapshot()
            except SiteAgentError:
                recommendations = []
            _, _, _, response_seconds = self._ledger_index()
            return briefing_projection(
                identity={
                    "site_id": self._plan.site_id,
                    "deployment_id": self._plan.deployment_id,
                    "workflow_id": self._plan.workflow_id,
                    "mode_label": SERVICE_MODE_LABEL,
                    "run_directory": self._storage.run_root.name,
                },
                state=state,
                health=health,
                evaluations=evaluations,
                recommendations=recommendations,
                service_events=self._storage.read_events(),
                response_scenario_seconds=response_seconds,
                disclaimer=DISCLAIMER,
            )

    def fixture_snapshot(self) -> dict[str, Any]:
        """Fixture-only metadata for the console's fixture controls."""
        with self._lock:
            cursor = self._composed.cursor()
            catalog = [dict(item) for item in self._seam.cycle_catalog]
            next_cycle = (
                catalog[cursor.consumed_cycles]
                if cursor.consumed_cycles < len(catalog)
                else None
            )
            return {
                "fixture_mode": True,
                "disclaimer": DISCLAIMER,
                "cycle_catalog": catalog,
                "cursor": cursor.to_dict(),
                "next_cycle": next_cycle,
                "controls": {
                    "advance": self._can_advance()[0],
                    "restart": self._state is not ServiceState.STOPPED,
                    "reset": self._state is not ServiceState.STOPPED,
                },
            }

    # -- fixture lifecycle operations ------------------------------------

    def _can_advance(self) -> tuple[bool, str | None]:
        if self._state is ServiceState.FAILED:
            return False, "the service is in a failed state"
        if self._state is ServiceState.STOPPED:
            return False, "the service is stopped"
        status = self._composed.runtime.status()
        if status.source_exhausted:
            return False, "the fixture source is exhausted"
        if status.cycles_completed >= self._plan.max_cycles:
            return False, (
                "the declared bounded run is complete "
                f"(max_cycles={self._plan.max_cycles})"
            )
        return True, None

    def advance(self) -> dict[str, Any]:
        """Run exactly one fixture cycle through the existing runtime."""
        with self._lock:
            allowed, reason = self._can_advance()
            if not allowed:
                raise SiteAgentError("advance_refused", reason or "refused")
            cursor_before = self._composed.cursor()
            catalog = self._seam.cycle_catalog
            cycle_label = None
            if cursor_before.consumed_cycles < len(catalog):
                cycle_label = catalog[cursor_before.consumed_cycles].get(
                    "label"
                )
            self._sink.reset()
            try:
                outcome = self._composed.runtime.run_once()
            except AgentRuntimeError as exc:
                self._fail(exc.incident_code, exc.detail)
                self._append_event(
                    {
                        "event": "failure",
                        "code": exc.incident_code,
                        "detail": exc.detail,
                        "observation_timestamp_s": (
                            self._last_delivered_observation_ts
                        ),
                    }
                )
                raise SiteAgentError(exc.incident_code, exc.detail) from exc
            payload = self._record_cycle(outcome, cycle_label)
            cursor_after = self._composed.cursor()
            self._storage.write_cursor(cursor_after)
            return payload

    def _record_cycle(
        self, outcome: CycleOutcome, cycle_label: str | None
    ) -> dict[str, Any]:
        rejection = self._sink.rejections[-1] if self._sink.rejections else None
        observation_ts: float | None = None
        if outcome.record is not None:
            observation_ts = outcome.record.observation_timestamp_s
        elif rejection is not None:
            observation_ts = rejection.get("observation_timestamp_s")
        if observation_ts is not None:
            previous = self._last_delivered_observation_ts
            self._last_delivered_observation_ts = (
                observation_ts
                if previous is None
                else max(previous, observation_ts)
            )
        adapter_summary = None
        reports = dict(self._composed.adapter_reports())
        if reports:
            latest_cycle = max(reports)
            report = reports[latest_cycle]
            adapter_summary = {
                "cycle_index": latest_cycle,
                "rejected": list(report.get("rejected", [])),
                "unmapped_count": len(report.get("unmapped", [])),
            }
        event = {
            "event": "cycle",
            "outcome": outcome.kind.value,
            "sequence_number": outcome.sequence_number,
            "envelope_id": outcome.envelope_id,
            "evaluation_id": outcome.evaluation_id,
            "acknowledged": outcome.acknowledged,
            "verdict": (
                None
                if outcome.record is None
                else outcome.record.verdict.value
            ),
            "recommendation_action": (
                None
                if outcome.record is None
                or outcome.record.recommendation_action is None
                else outcome.record.recommendation_action.value
            ),
            "failure_code": (
                None
                if outcome.failure is None
                else getattr(outcome.failure.code, "value", None)
            ),
            "failure_detail": (
                None if outcome.failure is None else outcome.failure.detail
            ),
            "observation_timestamp_s": observation_ts,
            "cycle_label": cycle_label,
            "adapter": adapter_summary,
        }
        self._append_event(event)
        return {key: value for key, value in event.items() if key != "event"}

    def restart_runtime(self) -> dict[str, Any]:
        """Recompose the runtime from persisted evidence and cursor."""
        with self._lock:
            if self._state is ServiceState.STOPPED:
                raise SiteAgentError(
                    "restart_refused", "the service is stopped"
                )
            try:
                self._composed.runtime.request_stop()
            except Exception:  # noqa: BLE001 - old runtime teardown only
                pass
            cursor = self._storage.read_cursor()
            sink = _ServiceRuntimeSink()
            composed = self._seam.composer(
                self._plan,
                self._storage.workflow_evidence_root,
                cursor,
                sink,
            )
            self._composed = composed
            self._sink = sink
            self._state = ServiceState.SERVING
            self._failure = None
            self._append_event(
                {
                    "event": "restarted",
                    "text": (
                        "Fixture control: runtime recomposed from persisted "
                        "evidence and source cursor."
                    ),
                    "observation_timestamp_s": (
                        self._last_delivered_observation_ts
                    ),
                    "cursor": cursor.to_dict(),
                }
            )
            self._recover_or_fail(resumed=True)
            return self.health_snapshot()

    def reset(self) -> dict[str, Any]:
        """Fixture control: fresh launch into the next empty run root."""
        with self._lock:
            if self._state is ServiceState.STOPPED:
                raise SiteAgentError("reset_refused", "the service is stopped")
            numbers = _existing_run_numbers(self._runs_root)
            next_number = (numbers[-1] + 1) if numbers else 1
            replacement = type(self)._launch_fresh(
                runs_root=self._runs_root,
                run_number=next_number,
                site_id=self._storage.site_id,
                deployment_id=self._storage.deployment_id,
                workflow_id=self._storage.workflow_id,
                seam=self._seam,
            )
            self._append_event(
                {
                    "event": "reset",
                    "text": (
                        "Fixture control: service reset into "
                        f"{replacement.storage.run_root.name}."
                    ),
                    "observation_timestamp_s": (
                        self._last_delivered_observation_ts
                    ),
                }
            )
            try:
                self._composed.runtime.request_stop()
            except Exception:  # noqa: BLE001 - old runtime teardown only
                pass
            self._storage = replacement._storage
            self._plan = replacement._plan
            self._report_payload = replacement._report_payload
            self._composed = replacement._composed
            self._sink = replacement._sink
            self._run_number = replacement._run_number
            self._state = replacement._state
            self._failure = replacement._failure
            self._last_delivered_observation_ts = (
                replacement._last_delivered_observation_ts
            )
            self._simulation_midnight = replacement._simulation_midnight
            return self.health_snapshot()

    # -- manager workflow transport --------------------------------------

    def respond(
        self,
        recommendation_id: str,
        *,
        kind: str,
        operator_id: str,
        reason_code: str,
        note: str | None = None,
        replacement_action: str | None = None,
        replacement_robot_id: str | None = None,
        replacement_execute_before: str | None = None,
        responded_at: str | None = None,
    ) -> dict[str, Any]:
        """Record one existing manager workflow response.

        This is transport only: the existing queue operation builds the
        existing response record and the existing ledger enforces
        transition legality.  Acceptance remains workflow evidence — it
        creates, implies, and schedules nothing physical.
        """
        with self._lock:
            if self._state is ServiceState.STOPPED:
                raise SiteAgentError(
                    "service_stopped", "the service is stopped"
                )
            if kind not in ("accept", "reject", "modify"):
                raise SiteAgentError(
                    "invalid_response_kind",
                    "kind must be accept, reject, or modify",
                )
            for name, value in (
                ("operator_id", operator_id),
                ("reason_code", reason_code),
            ):
                if not isinstance(value, str) or not value.strip():
                    raise SiteAgentError(
                        "invalid_request",
                        f"{name} must be a non-blank string",
                    )
            when = self._resolve_responded_at(responded_at)
            queue = self._composed.runtime.queue
            try:
                if kind == "accept":
                    queue.accept(
                        recommendation_id,
                        operator_id=operator_id,
                        responded_at=when,
                        reason_code=reason_code,
                        note=note,
                    )
                elif kind == "reject":
                    queue.reject(
                        recommendation_id,
                        operator_id=operator_id,
                        responded_at=when,
                        reason_code=reason_code,
                        note=note,
                    )
                else:
                    queue.modify(
                        recommendation_id,
                        operator_id=operator_id,
                        responded_at=when,
                        reason_code=reason_code,
                        replacement_action=self._parse_action(
                            replacement_action
                        ),
                        replacement_execute_before=(
                            self._parse_execute_before(
                                replacement_execute_before
                            )
                        ),
                        replacement_robot_id=replacement_robot_id,
                        note=note,
                    )
            except ManagerDecisionQueueError as exc:
                raise SiteAgentError(
                    "unknown_recommendation", str(exc)
                ) from exc
            except LedgerTransitionError as exc:
                raise SiteAgentError(
                    "workflow_transition_rejected", str(exc)
                ) from exc
            except (TypeError, ValueError) as exc:
                raise SiteAgentError("invalid_request", str(exc)) from exc
            for item in self.recommendations_snapshot():
                if item["recommendation_id"] == recommendation_id:
                    return item
            raise SiteAgentError(
                "unknown_recommendation",
                f"recommendation {recommendation_id!r} disappeared after "
                "the response was recorded",
            )

    def _resolve_responded_at(self, value: str | None) -> datetime:
        if value is not None:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise SiteAgentError(
                    "invalid_request",
                    f"responded_at is not a valid ISO datetime: {value!r}",
                ) from exc
            if parsed.tzinfo is None:
                raise SiteAgentError(
                    "invalid_request",
                    "responded_at must be timezone-aware",
                )
            return parsed
        now = self._scenario_now()
        if now is None:
            raise SiteAgentError(
                "no_scenario_time",
                "no observation time exists yet to timestamp a response",
            )
        return now

    @staticmethod
    def _parse_action(value: str | None) -> RecommendationAction:
        if value is None:
            raise SiteAgentError(
                "invalid_request",
                "modify requires replacement_action",
            )
        try:
            return RecommendationAction(value)
        except ValueError as exc:
            raise SiteAgentError(
                "invalid_request",
                f"unknown replacement_action: {value!r}",
            ) from exc

    def _parse_execute_before(self, value: str | None) -> datetime:
        if value is None:
            raise SiteAgentError(
                "invalid_request",
                "modify requires replacement_execute_before",
            )
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise SiteAgentError(
                "invalid_request",
                "replacement_execute_before is not a valid ISO datetime: "
                f"{value!r}",
            ) from exc
        if parsed.tzinfo is None:
            raise SiteAgentError(
                "invalid_request",
                "replacement_execute_before must be timezone-aware",
            )
        return parsed

    # -- lifecycle -------------------------------------------------------

    def stop(self) -> None:
        with self._lock:
            if self._state is ServiceState.STOPPED:
                return
            try:
                self._composed.runtime.request_stop()
            except Exception:  # noqa: BLE001 - stopping must not raise
                pass
            self._state = ServiceState.STOPPED
            self._append_event(
                {
                    "event": "stopped",
                    "text": "Service stopped gracefully.",
                    "observation_timestamp_s": (
                        self._last_delivered_observation_ts
                    ),
                }
            )

    def scenario_now_iso(self) -> str | None:
        with self._lock:
            now = self._scenario_now()
            return None if now is None else to_primitive(now)


__all__ = ["SiteAgentService"]
