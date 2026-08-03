"""Run one scenario end-to-end: load -> validate -> adapt -> control -> record."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from nxt_sim.adapters.base import create_adapter
from nxt_sim.config.loader import ConfigError, apply_overrides, build_bundle, load_raw_bundle
from nxt_sim.config.models import collect_placeholders
from nxt_sim.config.validation import Severity, cross_validate
from nxt_sim.controllers.handoff_state_machine import HandoffController
from nxt_sim.interfaces.telemetry import TelemetryProvider
from nxt_sim.metrics.collector import MetricsCollector


def run_scenario_raw(
    raw: Mapping[str, Any],
    overrides: Optional[Mapping[str, Any]] = None,
    seed: Optional[int] = None,
    source: str = "<in-memory>",
) -> dict[str, Any]:
    """Execute one handoff cycle from a raw bundle dict and return the record.

    Raises ConfigError on validation ERRORs; validation WARNINGs are attached
    to the record (runs exploring incompatible configurations are legitimate).
    """
    effective = apply_overrides(dict(raw), dict(overrides or {}))
    if seed is not None:
        scenario = effective.setdefault("scenario", {})
        mock = scenario.setdefault("mock", {}) if isinstance(scenario, dict) else None
        if isinstance(mock, dict):
            mock["random_seed"] = seed

    bundle = build_bundle(effective, source=source)
    issues = cross_validate(bundle)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        raise ConfigError(f"scenario '{bundle.scenario.name}' failed validation",
                          [str(i) for i in errors])

    adapter = create_adapter(bundle)
    metrics = MetricsCollector(
        scenario_name=bundle.scenario.name,
        seed=bundle.scenario.mock.random_seed,
        overrides=overrides or {},
    )
    controller = HandoffController(adapter, bundle, metrics)
    outcome = controller.run_cycle()

    telemetry: Mapping[str, Any] = (
        adapter.get_telemetry() if isinstance(adapter, TelemetryProvider) else {}
    )
    record = metrics.build_record(outcome, telemetry)
    record["validation_warnings"] = [
        str(i) for i in issues if i.severity is Severity.WARNING
    ]
    record["placeholder_parameter_count"] = len(collect_placeholders(bundle))
    return record


def run_scenario_file(
    scenario_path: str,
    overrides: Optional[Mapping[str, Any]] = None,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    raw = load_raw_bundle(scenario_path)
    return run_scenario_raw(raw, overrides=overrides, seed=seed, source=str(scenario_path))
