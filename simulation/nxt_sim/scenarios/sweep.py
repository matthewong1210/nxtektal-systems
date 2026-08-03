"""Parameter sweeps over docking scenarios.

A sweep is a Cartesian grid over dotted-path axes (initial lateral /
longitudinal / yaw error in the shipped config; any bundle path works —
inlet height/width, guide geometry, payload, CoM, slope, lift height, dump
angle/speed, sensor placement, e-stop distance). Each grid point runs
``repeats_per_point`` times with per-run seeds derived from ``base_seed``,
so results are exactly reproducible.
"""

from __future__ import annotations

import itertools
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from nxt_sim.config.loader import load_raw_bundle, load_sweep_config
from nxt_sim.config.models import SweepAxis, SweepConfig, collect_placeholders
from nxt_sim.config.loader import build_bundle, apply_overrides
from nxt_sim.reporting.report import write_json_report, write_runs_csv
from nxt_sim.scenarios.runner import run_scenario_raw


def expand_grid(axes: list[SweepAxis]) -> list[dict[str, float]]:
    """Cartesian product of axes -> list of {path: value} override dicts."""
    combos = itertools.product(*[[(axis.path, v) for v in axis.values] for axis in axes])
    return [dict(combo) for combo in combos]


def _max_tolerated(
    records: list[dict[str, Any]],
    path_suffix: str,
    axes: Optional[list[SweepAxis]] = None,
) -> Optional[float]:
    """Marginal tolerance along one axis.

    Definition: largest |axis value| such that every run at that magnitude AND
    at every smaller magnitude succeeded (contiguous from zero, both signs
    pooled), while every OTHER swept axis is held at its most benign grid
    point (smallest |value|). Without the filter, failures caused by extreme
    values of other axes would mask the tolerance being measured.
    """
    target_paths = {a.path for a in (axes or []) if a.path.endswith(path_suffix)}
    other_min_abs: dict[str, float] = {
        a.path: min(abs(v) for v in a.values)
        for a in (axes or [])
        if a.path not in target_paths
    }
    by_magnitude: dict[float, list[bool]] = {}
    for record in records:
        overrides = record.get("overrides", {})
        if any(
            path in overrides and abs(float(overrides[path])) != min_abs
            for path, min_abs in other_min_abs.items()
        ):
            continue
        for path, value in overrides.items():
            if path.endswith(path_suffix):
                # The tolerance being measured is a DOCKING tolerance:
                # post-docking failures (unload, tipping, ...) must not
                # corrupt the docking error envelope.
                by_magnitude.setdefault(abs(float(value)), []).append(
                    bool(record["docking_success"])
                )
    if not by_magnitude:
        return None
    tolerated: Optional[float] = None
    for magnitude in sorted(by_magnitude):
        if all(by_magnitude[magnitude]):
            tolerated = magnitude
        else:
            break
    return tolerated


def summarize_sweep(
    records: list[dict[str, Any]], axes: Optional[list[SweepAxis]] = None
) -> dict[str, Any]:
    total = len(records)
    docking_successes = [r for r in records if r["docking_success"]]
    cycle_successes = [r for r in records if r["success"]]
    failures = [r for r in records if not r["success"]]
    recovery_attempts = sum(r["recovery_attempts"] for r in records)
    recovery_successes = sum(r["recovery_successes"] for r in records)

    def _mean(values: list[float]) -> Optional[float]:
        return round(sum(values) / len(values), 3) if values else None

    docking_times = [
        r["docking_completion_time_s"] for r in docking_successes
        if r.get("docking_completion_time_s") is not None
    ]
    unload_times = [
        r["unloading_cycle_time_s"] for r in cycle_successes
        if r.get("unloading_cycle_time_s") is not None
    ]
    clearances = [r["min_clearance_m"] for r in records if r.get("min_clearance_m") is not None]
    margins = [
        r["tipping_risk_indicator"] for r in records
        if r.get("tipping_risk_indicator") is not None
    ]

    return {
        "runs": total,
        "docking_successes": len(docking_successes),
        "cycle_successes": len(cycle_successes),
        "docking_success_rate": (
            round(len(docking_successes) / total, 4) if total else None
        ),
        "cycle_success_rate": round(len(cycle_successes) / total, 4) if total else None,
        "max_tolerated_lateral_error_m": _max_tolerated(
            records, "initial_error.lateral_m", axes
        ),
        "max_tolerated_yaw_error_deg": _max_tolerated(records, "initial_error.yaw_deg", axes),
        "max_tolerated_definition": (
            "largest |axis value| with 100% success at that magnitude and every "
            "smaller magnitude (contiguous from zero, both signs pooled), with "
            "every other swept axis held at its most benign grid point"
        ),
        "collision_count_total": sum(r["collision_count"] for r in records),
        "runs_with_collisions": sum(1 for r in records if r["collision_count"] > 0),
        "min_clearance_m_overall": round(min(clearances), 6) if clearances else None,
        "mean_docking_completion_time_s": _mean(docking_times),
        "mean_unloading_cycle_time_s": _mean(unload_times),
        "recovery_attempts_total": recovery_attempts,
        "recovery_successes_total": recovery_successes,
        "failed_docking_recovery_rate": (
            round(recovery_successes / recovery_attempts, 4) if recovery_attempts else None
        ),
        "emergency_stops": sum(1 for r in records if r["emergency_stop_triggered"]),
        "min_stability_margin_overall": round(min(margins), 6) if margins else None,
        "peak_contact_force_n": None,
        "contact_force_available": any(r.get("contact_force_available") for r in records),
        "failure_reason_histogram": dict(
            Counter(r["failure_reason"] for r in failures).most_common()
        ),
    }


def run_sweep_config(
    cfg: SweepConfig,
    base_scenario_path: Path,
    progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Execute a sweep and return the full report payload (no files written)."""
    base_raw = load_raw_bundle(base_scenario_path)
    grid = expand_grid(cfg.axes)
    records: list[dict[str, Any]] = []
    run_index = 0
    for point in grid:
        for repeat in range(cfg.repeats_per_point):
            seed = cfg.base_seed + run_index
            overrides = {**cfg.overrides, **point}
            record = run_scenario_raw(
                base_raw, overrides=overrides, seed=seed, source=str(base_scenario_path)
            )
            record["sweep_point"] = dict(point)
            record["repeat"] = repeat
            records.append(record)
            run_index += 1
            if progress is not None and run_index % 50 == 0:
                progress(f"{run_index}/{len(grid) * cfg.repeats_per_point} runs complete")

    bundle = build_bundle(
        apply_overrides(base_raw, cfg.overrides), source=str(base_scenario_path)
    )
    placeholders = collect_placeholders(bundle)
    return {
        "sweep": cfg.name,
        "base_scenario": str(base_scenario_path),
        "static_overrides": dict(cfg.overrides),
        "axes": [{"path": a.path, "values": a.values} for a in cfg.axes],
        "repeats_per_point": cfg.repeats_per_point,
        "base_seed": cfg.base_seed,
        "summary": summarize_sweep(records, cfg.axes),
        "validity_note": (
            f"{len(placeholders)} physical parameters are PLACEHOLDERS (no supplier/"
            "measured data). Results validate the pipeline, not the design. Granular "
            "ball flow, bridging, and jamming are physical-test risks not covered by "
            "simulation."
        ),
        "placeholder_parameters": placeholders,
        "runs": records,
    }


def run_sweep(
    sweep_path: str | Path,
    output_dir: Optional[str | Path] = None,
    progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Load a sweep file, execute it, and write JSON + CSV reports.

    Returns the report payload with ``report_json`` / ``report_csv`` paths
    added when reports were written.
    """
    cfg, base_scenario = load_sweep_config(sweep_path)
    payload = run_sweep_config(cfg, base_scenario, progress=progress)
    if output_dir is not None:
        out = Path(output_dir)
        json_path = write_json_report(payload, out / f"{cfg.name}_report.json")
        csv_path = write_runs_csv(payload["runs"], out / f"{cfg.name}_runs.csv")
        payload["report_json"] = str(json_path)
        payload["report_csv"] = str(csv_path)
    return payload
