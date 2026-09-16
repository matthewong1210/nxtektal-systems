#!/usr/bin/env python3
"""Validate every config under configs/ and print a clear report.

Exit code 0 when everything is valid (warnings allowed), 1 on any error.

Usage (from simulation/):
    uv run python scripts/validate_configs.py [--strict]   # --strict: warnings fail too
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from pydantic import ValidationError  # noqa: E402

from nxt_sim.config.loader import (  # noqa: E402
    ConfigError,
    _read_yaml_mapping,
    _require_section,
    load_raw_bundle,
    load_sweep_config,
    apply_overrides,
    build_bundle,
)
from nxt_sim.config.models import (  # noqa: E402
    EquipmentProfile,
    HandoffMechanismConfig,
    RobotGeometryConfig,
    SafetyConfig,
)
from nxt_sim.config.validation import Severity, cross_validate  # noqa: E402
from nxt_sim.scenarios.sweep import expand_grid  # noqa: E402
from scripts.edge_gateway_live_input_v0 import (  # noqa: E402
    GatewayError,
    load_gateway_config,
)
from scripts.pilot_course_a_edge_fixture import commissioned_site  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument(
        "--configs", default=str(SIM_ROOT / "configs"), help="configs directory"
    )
    args = parser.parse_args()
    configs = Path(args.configs)
    errors = 0
    warnings = 0

    def report(path: Path, ok: bool, messages: list[str]) -> None:
        nonlocal errors
        rel = path.relative_to(configs) if path.is_relative_to(configs) else path
        print(f"{'OK   ' if ok else 'ERROR'} {rel}")
        for msg in messages:
            print(f"      {msg}")
        if not ok:
            errors += 1

    # Standalone section files
    section_specs = [
        ("robot", "robot_geometry", RobotGeometryConfig),
        ("robot", "handoff_mechanism", HandoffMechanismConfig),
        ("equipment", "equipment_profile", EquipmentProfile),
        ("safety", "safety", SafetyConfig),
    ]
    for subdir in ("robot", "equipment", "safety"):
        for path in sorted((configs / subdir).glob("*.yaml")):
            messages: list[str] = []
            ok = True
            try:
                data = _read_yaml_mapping(path)
                for spec_dir, key, model in section_specs:
                    if spec_dir != subdir:
                        continue
                    section = _require_section(data, key, path)
                    model.model_validate(section)
            except (ConfigError, ValidationError) as exc:
                ok = False
                messages.append(str(exc))
            report(path, ok, messages)

    # Edge Gateway V0 is a deployment composition root, not a nxt_sim config
    # model. Validate its strict versioned document against the authoritative
    # commissioned Pilot Course A identity and load-cell bindings.
    for edge_gateway_path in sorted((configs / "edge_gateway").glob("*.yaml")):
        messages = []
        ok = True
        try:
            load_gateway_config(edge_gateway_path, site=commissioned_site())
        except GatewayError as exc:
            ok = False
            messages.append(str(exc))
        report(edge_gateway_path, ok, messages)

    # Scenario + sweep files (full bundle + cross validation)
    for path in sorted((configs / "scenarios").glob("*.yaml")):
        messages = []
        ok = True
        try:
            data = _read_yaml_mapping(path)
            if "scenario" in data:
                raw = load_raw_bundle(path)
                bundle = build_bundle(raw, source=str(path))
                for issue in cross_validate(bundle):
                    if issue.severity is Severity.ERROR:
                        ok = False
                        messages.append(str(issue))
                    elif issue.severity is Severity.WARNING:
                        warnings += 1
                        messages.append(str(issue))
            elif "sweep" in data:
                cfg, base = load_sweep_config(path)
                raw = load_raw_bundle(base)
                grid = expand_grid(cfg.axes)
                # Prove every axis path + static override applies and validates.
                probe = apply_overrides(raw, {**cfg.overrides, **grid[0]})
                build_bundle(probe, source=f"{path} (first grid point)")
                messages.append(f"{len(grid)} grid points x {cfg.repeats_per_point} repeats")
            else:
                ok = False
                messages.append("no 'scenario:' or 'sweep:' section found")
        except (ConfigError, ValidationError) as exc:
            ok = False
            messages.append(str(exc))
        report(path, ok, messages)

    print(f"\n{errors} error(s), {warnings} warning(s)")
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
