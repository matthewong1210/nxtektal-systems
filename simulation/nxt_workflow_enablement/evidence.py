"""Typed, plain-data evidence consumed by readiness evaluation.

Evidence is *declared* by a composition root from canonical owners'
public surfaces; nothing here reads a device, a clock, a file, or a
network.  Adapter facts are the outcome of composing the real edge
adapter kit; declared fixture channels are the composition root's
explicit claim of which canonical channels its synthetic facility
systems supply.  The evaluation fails closed on any claim the
commissioned site does not support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identity import WorkflowEnablementError, _require_non_blank


class TransportMode(StrEnum):
    """The only transport V0 supports: already-read synthetic fixtures."""

    FIXTURE_ONLY = "FIXTURE_ONLY"


class RuntimeMode(StrEnum):
    """The only runtime posture V0 supports: advisory Shadow Mode."""

    SHADOW = "SHADOW"


@dataclass(frozen=True, slots=True)
class SharedSiteExpectation:
    """The identity the operator expects the shared manifest to carry."""

    site_id: str
    deployment_id: str

    def __post_init__(self) -> None:
        _require_non_blank("site_id", self.site_id)
        _require_non_blank("deployment_id", self.deployment_id)


def validate_relative_evidence_paths(paths: object) -> tuple[str, ...]:
    """Validate a deterministic, collision-safe relative evidence layout."""
    if not isinstance(paths, tuple) or not paths:
        raise WorkflowEnablementError(
            "evidence paths must be a non-empty tuple"
        )
    seen: set[str] = set()
    for path in paths:
        _require_non_blank("evidence path", path)
        if path.startswith(("/", "\\")) or "\\" in path:
            raise WorkflowEnablementError(
                f"evidence path {path!r} must be a relative POSIX path"
            )
        segments = path.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise WorkflowEnablementError(
                f"evidence path {path!r} must not contain empty, '.', "
                "or '..' segments"
            )
        if path in seen:
            raise WorkflowEnablementError(
                f"evidence path {path!r} is duplicated"
            )
        seen.add(path)
    return paths


def simulation_midnight_issue(value: str) -> str | None:
    """Return a failure detail, or None when the epoch is valid."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        return f"simulation midnight is not ISO-8601: {exc}"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "simulation midnight must be timezone-aware"
    if (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) != (
        0,
        0,
        0,
        0,
    ):
        return "simulation midnight must be an exact calendar midnight"
    return None


@dataclass(frozen=True, slots=True)
class OutputLocationPlan:
    """Deterministic, collision-safe evidence locations (relative only).

    ``root_is_empty`` is declared evidence: the package never touches a
    filesystem, so the composition root is responsible for proving the
    declaration against the real evidence root before making it.
    """

    relative_paths: tuple[str, ...]
    root_is_empty: bool

    def __post_init__(self) -> None:
        validate_relative_evidence_paths(self.relative_paths)
        if type(self.root_is_empty) is not bool:
            raise WorkflowEnablementError("root_is_empty must be a boolean")


@dataclass(frozen=True, slots=True)
class EnablementContext:
    """Shared, declared enablement configuration for one evaluation."""

    scenario_name: str
    scenario_t_s: float
    transport_mode: str
    physical_execution_reachable: bool
    output_locations: OutputLocationPlan

    def __post_init__(self) -> None:
        _require_non_blank("scenario_name", self.scenario_name)
        if (
            isinstance(self.scenario_t_s, bool)
            or not isinstance(self.scenario_t_s, (int, float))
            or not math.isfinite(self.scenario_t_s)
            or self.scenario_t_s < 0
        ):
            # Fail at contract construction: a non-finite value must never
            # reach report hashing or canonical JSON serialization.
            raise WorkflowEnablementError(
                "scenario_t_s must be a finite non-negative number"
            )
        _require_non_blank("transport_mode", self.transport_mode)
        if type(self.physical_execution_reachable) is not bool:
            raise WorkflowEnablementError(
                "physical_execution_reachable must be a boolean"
            )
        if not isinstance(self.output_locations, OutputLocationPlan):
            raise WorkflowEnablementError(
                "output_locations must be an OutputLocationPlan"
            )


@dataclass(frozen=True, slots=True)
class AdapterCompositionEvidence:
    """The outcome of composing the real edge adapter kit.

    ``adapter_channels`` are the canonical channels the composed kit can
    produce; ``profile_calibration_ids`` are the declared adapter-local
    profile calibration identities per sensor.  When composition failed,
    ``error`` carries the canonical owner's own fail-closed message.
    """

    composed: bool
    error: str | None
    adapter_channels: tuple[str, ...]
    profile_calibration_ids: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.composed) is not bool:
            raise WorkflowEnablementError("composed must be a boolean")
        if self.composed and self.error is not None:
            raise WorkflowEnablementError(
                "a composed adapter kit must not carry an error"
            )
        if not self.composed:
            if type(self.error) is not str or not self.error.strip():
                raise WorkflowEnablementError(
                    "a failed adapter composition must carry the owner's "
                    "error detail as a string"
                )
            if self.adapter_channels:
                raise WorkflowEnablementError(
                    "a failed adapter composition cannot claim channels"
                )
        if not isinstance(self.adapter_channels, tuple):
            raise WorkflowEnablementError(
                "adapter_channels must be a tuple"
            )
        if len(set(self.adapter_channels)) != len(self.adapter_channels):
            raise WorkflowEnablementError(
                "adapter_channels must not contain duplicates"
            )
        for channel in self.adapter_channels:
            _require_non_blank("adapter_channels entry", channel)
        if not isinstance(self.profile_calibration_ids, tuple):
            raise WorkflowEnablementError(
                "profile_calibration_ids must be a tuple"
            )
        seen_sensors: set[str] = set()
        for pair in self.profile_calibration_ids:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not str
            ):
                raise WorkflowEnablementError(
                    "profile_calibration_ids entries must be "
                    "(sensor_id, calibration_id) string pairs"
                )
            sensor_id, calibration_id = pair
            _require_non_blank("profile sensor_id", sensor_id)
            _require_non_blank("profile calibration_id", calibration_id)
            if sensor_id in seen_sensors:
                raise WorkflowEnablementError(
                    f"profile sensor {sensor_id!r} is declared twice"
                )
            seen_sensors.add(sensor_id)


@dataclass(frozen=True, slots=True)
class RangeOpsRuntimeDeclaration:
    """Declared Range Operations runtime configuration (data only)."""

    runtime_mode: str
    simulation_midnight_iso: str
    clean_sensed_valid: bool
    policy_id: str
    policy_version: str
    max_cycles: int

    def __post_init__(self) -> None:
        _require_non_blank("runtime_mode", self.runtime_mode)
        _require_non_blank(
            "simulation_midnight_iso", self.simulation_midnight_iso
        )
        if type(self.clean_sensed_valid) is not bool:
            raise WorkflowEnablementError(
                "clean_sensed_valid must be a boolean"
            )
        _require_non_blank("policy_id", self.policy_id)
        _require_non_blank("policy_version", self.policy_version)
        if (
            isinstance(self.max_cycles, bool)
            or not isinstance(self.max_cycles, int)
            or self.max_cycles < 1
        ):
            raise WorkflowEnablementError(
                "max_cycles must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class RangeOpsEvidence:
    """Everything Range Operations readiness consumes beyond the site."""

    requirements_version: str
    adapter: AdapterCompositionEvidence
    declared_fixture_channels: tuple[str, ...]
    runtime: RangeOpsRuntimeDeclaration

    def __post_init__(self) -> None:
        _require_non_blank(
            "requirements_version", self.requirements_version
        )
        if not isinstance(self.adapter, AdapterCompositionEvidence):
            raise WorkflowEnablementError(
                "adapter must be an AdapterCompositionEvidence"
            )
        if not isinstance(self.declared_fixture_channels, tuple):
            raise WorkflowEnablementError(
                "declared_fixture_channels must be a tuple"
            )
        if len(set(self.declared_fixture_channels)) != len(
            self.declared_fixture_channels
        ):
            raise WorkflowEnablementError(
                "declared_fixture_channels must not contain duplicates"
            )
        for channel in self.declared_fixture_channels:
            _require_non_blank("declared_fixture_channels entry", channel)
        if not isinstance(self.runtime, RangeOpsRuntimeDeclaration):
            raise WorkflowEnablementError(
                "runtime must be a RangeOpsRuntimeDeclaration"
            )
