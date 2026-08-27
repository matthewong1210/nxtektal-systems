"""Noncanonical service-state files for the Pilot Site Agent service.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

These files are service-owned operational metadata, deliberately
separate from the canonical evidence streams (ledger, journal,
snapshots, checkpoints) that keep their existing owners:

- ``launch.json`` — written once per fresh launch: the verified plan
  payload and report identity this evidence root was launched with.
- ``source_cursor.json`` — rewritten atomically after every cycle: the
  fixture source resume position (the analogue of a real transport
  reader's cursor).  Losing the latest write is safe — the cursor is
  then behind by at most one resolved cycle, and redelivery is
  idempotent — but a cursor that cannot be written at all makes a
  future restart unsafe, so the service fails closed on write errors.
- ``service_events.jsonl`` — append-only noncanonical diagnostics
  (cycle outcomes including rejections, restarts, resets).  Never
  decision truth, never read back as live input.

None of these files is facility state, policy evidence, or workflow
truth, and none of them feeds the live loop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import (
    DISCLAIMER,
    SERVICE_EVENTS_SCHEMA,
    SERVICE_STATE_SCHEMA,
    SiteAgentError,
    SourceCursor,
)


def _stable_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` via a sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _load_json_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SiteAgentError(code, f"cannot read {path.name}: {exc}") from exc
    except ValueError as exc:
        raise SiteAgentError(
            code, f"{path.name} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SiteAgentError(code, f"{path.name} must be a JSON object")
    return payload


class ServiceStorage:
    """Filesystem layout of one launched run.

    ``run_root/<site_id>/<deployment_id>/`` holds the readiness report
    and the ``service/`` metadata directory; the canonical workflow
    evidence root is the ``<workflow_id>/`` subdirectory whose relative
    layout the launch plan declares.
    """

    READY_REPORT_NAME = "workflow_enablement_report.ready.json"
    NOT_READY_REPORT_NAME = "workflow_enablement_report.not_ready.json"

    def __init__(
        self,
        run_root: Path,
        *,
        site_id: str,
        deployment_id: str,
        workflow_id: str,
    ) -> None:
        for name, value in (
            ("site_id", site_id),
            ("deployment_id", deployment_id),
            ("workflow_id", workflow_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SiteAgentError(
                    "invalid_storage", f"{name} must be a non-blank string"
                )
        self.run_root = Path(run_root)
        self.site_id = site_id
        self.deployment_id = deployment_id
        self.workflow_id = workflow_id

    @property
    def identity_root(self) -> Path:
        return self.run_root / self.site_id / self.deployment_id

    @property
    def workflow_evidence_root(self) -> Path:
        return self.identity_root / self.workflow_id

    @property
    def service_dir(self) -> Path:
        return self.identity_root / "service"

    @property
    def report_path(self) -> Path:
        return self.identity_root / self.READY_REPORT_NAME

    @property
    def launch_record_path(self) -> Path:
        return self.service_dir / "launch.json"

    @property
    def cursor_path(self) -> Path:
        return self.service_dir / "source_cursor.json"

    @property
    def events_path(self) -> Path:
        return self.service_dir / "service_events.jsonl"

    # -- fresh/resume classification ------------------------------------

    def workflow_root_is_empty(self) -> bool:
        """Observe (never mutate) whether the canonical root is empty.

        A root that exists but is not a directory, and a filesystem
        error that prevents proving emptiness, are both treated as a
        collision so launch fails closed.
        """
        root = self.workflow_evidence_root
        try:
            if not root.exists():
                return True
            if not root.is_dir():
                return False
            return not any(root.iterdir())
        except OSError:
            return False

    def has_service_records(self) -> bool:
        try:
            return (
                self.launch_record_path.is_file()
                and self.cursor_path.is_file()
            )
        except OSError:
            return False

    # -- launch record ---------------------------------------------------

    def write_launch_record(
        self, *, report_id: str, plan_payload: Mapping[str, Any]
    ) -> None:
        record = {
            "schema": SERVICE_STATE_SCHEMA,
            "kind": "launch",
            "disclaimer": DISCLAIMER,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "workflow_id": self.workflow_id,
            "report_id": report_id,
            "report_file": self.READY_REPORT_NAME,
            "plan": dict(plan_payload),
        }
        try:
            _atomic_write_text(
                self.launch_record_path, _stable_json(record) + "\n"
            )
        except OSError as exc:
            raise SiteAgentError(
                "service_state_unavailable",
                f"cannot write launch record: {exc}",
            ) from exc

    def read_launch_record(self) -> dict[str, Any]:
        record = _load_json_object(
            self.launch_record_path, code="service_state_invalid"
        )
        if record.get("schema") != SERVICE_STATE_SCHEMA:
            raise SiteAgentError(
                "service_state_invalid",
                "launch record carries a foreign schema",
            )
        if record.get("kind") != "launch":
            raise SiteAgentError(
                "service_state_invalid", "launch record kind mismatch"
            )
        for name in ("site_id", "deployment_id", "workflow_id"):
            if record.get(name) != getattr(self, name):
                raise SiteAgentError(
                    "service_state_identity_mismatch",
                    f"launch record {name} does not match this service",
                )
        if not isinstance(record.get("plan"), dict):
            raise SiteAgentError(
                "service_state_invalid", "launch record has no plan payload"
            )
        return record

    # -- source cursor ---------------------------------------------------

    def write_cursor(self, cursor: SourceCursor) -> None:
        record = {
            "schema": SERVICE_STATE_SCHEMA,
            "kind": "source_cursor",
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "workflow_id": self.workflow_id,
            **cursor.to_dict(),
        }
        try:
            _atomic_write_text(self.cursor_path, _stable_json(record) + "\n")
        except OSError as exc:
            raise SiteAgentError(
                "cursor_write_failed",
                f"cannot persist the source cursor: {exc}",
            ) from exc

    def read_cursor(self) -> SourceCursor:
        record = _load_json_object(
            self.cursor_path, code="service_state_invalid"
        )
        if record.get("schema") != SERVICE_STATE_SCHEMA:
            raise SiteAgentError(
                "service_state_invalid",
                "source cursor carries a foreign schema",
            )
        if record.get("kind") != "source_cursor":
            raise SiteAgentError(
                "service_state_invalid", "source cursor kind mismatch"
            )
        for name in ("site_id", "deployment_id", "workflow_id"):
            if record.get(name) != getattr(self, name):
                raise SiteAgentError(
                    "service_state_identity_mismatch",
                    f"source cursor {name} does not match this service",
                )
        try:
            return SourceCursor(
                consumed_cycles=record["consumed_cycles"],
                next_sequence_number=record["next_sequence_number"],
            )
        except KeyError as exc:
            raise SiteAgentError(
                "service_state_invalid",
                f"source cursor is missing {exc.args[0]!r}",
            ) from exc

    # -- report ----------------------------------------------------------

    def write_ready_report(self, canonical_json: str) -> None:
        try:
            self.identity_root.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(canonical_json, encoding="utf-8")
        except OSError as exc:
            raise SiteAgentError(
                "service_state_unavailable",
                f"cannot write the readiness report: {exc}",
            ) from exc

    def read_ready_report_text(self) -> str:
        try:
            return self.report_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SiteAgentError(
                "service_state_invalid",
                f"cannot read the stored readiness report: {exc}",
            ) from exc

    # -- service events --------------------------------------------------

    def append_event(self, event: Mapping[str, Any]) -> bool:
        """Append one noncanonical diagnostics event.

        Best-effort by design: a failed append degrades visibility but
        must never block the canonical loop, so failures are reported
        through the return value instead of an exception.
        """
        payload = {"schema": SERVICE_EVENTS_SCHEMA, **event}
        try:
            self.service_dir.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        payload,
                        sort_keys=True,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    + "\n"
                )
        except (OSError, ValueError):
            return False
        return True

    def read_events(self) -> tuple[dict[str, Any], ...]:
        """Read the diagnostics stream, tolerating a torn final line."""
        try:
            text = self.events_path.read_text(encoding="utf-8")
        except OSError:
            return ()
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(payload, dict)
                and payload.get("schema") == SERVICE_EVENTS_SCHEMA
            ):
                events.append(payload)
        return tuple(events)


__all__ = ["ServiceStorage"]
