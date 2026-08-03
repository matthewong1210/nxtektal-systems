"""YAML loading, reference resolution, override application, and clear errors.

File layout convention (see configs/):

* robot file:      ``robot_geometry:`` + ``handoff_mechanism:`` sections
* equipment file:  ``equipment_profile:`` section
* safety file:     ``safety:`` section
* scenario file:   ``scenario:`` section referencing the three files above
* sweep file:      ``sweep:`` section referencing a base scenario file
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from pydantic import ValidationError

from nxt_sim.config.models import ScenarioBundle, SweepConfig


class ConfigError(Exception):
    """A configuration problem, with human-readable per-issue lines."""

    def __init__(self, message: str, issues: Optional[list[str]] = None):
        self.issues = issues or []
        if self.issues:
            message = message + "\n" + "\n".join(f"  - {i}" for i in self.issues)
        super().__init__(message)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"config file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"top level of {path} must be a mapping, got {type(data).__name__}")
    return data


def _require_section(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    if key not in data:
        raise ConfigError(
            f"{path} is missing required top-level section '{key}:' "
            f"(found: {', '.join(sorted(data)) or 'nothing'})"
        )
    section = data[key]
    if not isinstance(section, dict):
        raise ConfigError(f"section '{key}:' in {path} must be a mapping")
    return section


def _resolve_ref(ref: str, relative_to: Path) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else (relative_to / p).resolve()


def _require_only(data: dict[str, Any], allowed: set[str], path: Path) -> None:
    """Reject unexpected top-level sections (a mis-nested section would
    otherwise be silently dropped, defeating the typo-catching guarantee)."""
    extras = sorted(set(data) - allowed)
    if extras:
        raise ConfigError(
            f"{path} contains unexpected top-level section(s): {', '.join(extras)} "
            f"(allowed: {', '.join(sorted(allowed))}). Did you mis-nest a section?"
        )


# File references are resolved and inlined BEFORE overrides are applied, so an
# override targeting them would silently rewrite only the path string.
_UNOVERRIDABLE_PATHS = frozenset(
    {"scenario.robot_config", "scenario.equipment_config", "scenario.safety_config"}
)


def load_raw_bundle(scenario_path: str | Path) -> dict[str, Any]:
    """Load a scenario file and everything it references, as plain dicts.

    Raw dicts (rather than models) are returned so parameter-sweep overrides
    can be applied before validation — swept values then get exactly the same
    validation as hand-written configs.
    """
    spath = Path(scenario_path).resolve()
    scenario_data = _read_yaml_mapping(spath)
    _require_only(scenario_data, {"scenario"}, spath)
    scenario = _require_section(scenario_data, "scenario", spath)
    base = spath.parent

    for key in ("robot_config", "equipment_config", "safety_config"):
        if not scenario.get(key):
            raise ConfigError(f"{spath}: scenario is missing required file reference '{key}'")

    robot_path = _resolve_ref(str(scenario["robot_config"]), base)
    robot_data = _read_yaml_mapping(robot_path)
    _require_only(robot_data, {"robot_geometry", "handoff_mechanism"}, robot_path)
    equipment_path = _resolve_ref(str(scenario["equipment_config"]), base)
    equipment_data = _read_yaml_mapping(equipment_path)
    _require_only(equipment_data, {"equipment_profile"}, equipment_path)
    safety_path = _resolve_ref(str(scenario["safety_config"]), base)
    safety_data = _read_yaml_mapping(safety_path)
    _require_only(safety_data, {"safety"}, safety_path)

    return {
        "scenario": scenario,
        "robot": _require_section(robot_data, "robot_geometry", robot_path),
        "mechanism": _require_section(robot_data, "handoff_mechanism", robot_path),
        "equipment": _require_section(equipment_data, "equipment_profile", equipment_path),
        "safety": _require_section(safety_data, "safety", safety_path),
        "_sources": {
            "scenario": str(spath),
            "robot": str(robot_path),
            "equipment": str(equipment_path),
            "safety": str(safety_path),
        },
    }


def _format_validation_error(exc: ValidationError, source: str) -> list[str]:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"{source}: {loc}: {err['msg']}")
    return lines


def build_bundle(raw: Mapping[str, Any], source: str = "<in-memory>") -> ScenarioBundle:
    """Validate a raw bundle dict into typed models, with readable errors."""
    payload = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    try:
        return ScenarioBundle.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid scenario bundle ({source})", _format_validation_error(exc, source)
        ) from exc


def apply_overrides(raw: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``raw`` with dotted-path overrides applied.

    Paths address the raw bundle dict, e.g. ``scenario.initial_error.lateral_m``
    or ``equipment.inlet_height_m.value``. List elements use numeric segments
    (``robot.sensors.0.mount.x_m``). Intermediate mappings are created when
    absent (a typo then fails model validation, because unknown keys are
    forbidden). A scalar assigned to a PhysicalParam mapping replaces its
    ``value`` and re-tags it as an implicit placeholder via model coercion.
    """
    result = copy.deepcopy(raw)
    for dotted, value in overrides.items():
        if str(dotted) in _UNOVERRIDABLE_PATHS:
            raise ConfigError(
                f"override path '{dotted}' cannot be overridden: file references are "
                f"resolved before overrides are applied, so this would only rewrite the "
                f"path string without changing the loaded data. Reference a different "
                f"scenario file instead."
            )
        segments = str(dotted).split(".")
        if not all(segments):
            raise ConfigError(f"override path '{dotted}' has an empty segment")
        node: Any = result
        for i, seg in enumerate(segments[:-1]):
            if isinstance(node, list):
                if not seg.isdigit() or int(seg) >= len(node):
                    raise ConfigError(
                        f"override path '{dotted}': '{seg}' is not a valid list index"
                    )
                node = node[int(seg)]
            elif isinstance(node, dict):
                if seg not in node or node[seg] is None:
                    node[seg] = {}
                elif (
                    isinstance(node[seg], (int, float))
                    and not isinstance(node[seg], bool)
                    and segments[i + 1 :] == ["value"]
                ):
                    # A bare-scalar PhysicalParam addressed as '<param>.value':
                    # promote it to mapping form so the path resolves.
                    node[seg] = {"value": float(node[seg])}
                node = node[seg]
            else:
                done = ".".join(segments[:i])
                raise ConfigError(
                    f"override path '{dotted}': '{done}' is a scalar and cannot be descended into"
                )
        leaf = segments[-1]
        if isinstance(node, list):
            if not leaf.isdigit() or int(leaf) >= len(node):
                raise ConfigError(f"override path '{dotted}': '{leaf}' is not a valid list index")
            node[int(leaf)] = value
        elif isinstance(node, dict):
            existing = node.get(leaf)
            if isinstance(existing, dict) and "value" in existing and isinstance(value, (int, float)):
                existing["value"] = value
                existing["source"] = "placeholder"
                existing["note"] = f"overridden by sweep/CLI (was {existing.get('note', '')!r})"
            else:
                node[leaf] = value
        else:
            raise ConfigError(
                f"override path '{dotted}': parent is a scalar and cannot hold key '{leaf}'"
            )
    return result


def load_scenario_bundle(
    scenario_path: str | Path, overrides: Optional[Mapping[str, Any]] = None
) -> ScenarioBundle:
    """Convenience: load + apply overrides + validate."""
    raw = load_raw_bundle(scenario_path)
    if overrides:
        raw = apply_overrides(raw, overrides)
    return build_bundle(raw, source=str(scenario_path))


def load_sweep_config(sweep_path: str | Path) -> tuple[SweepConfig, Path]:
    """Load a sweep file; returns the config and the resolved base scenario path."""
    spath = Path(sweep_path).resolve()
    sweep_data = _read_yaml_mapping(spath)
    _require_only(sweep_data, {"sweep"}, spath)
    section = _require_section(sweep_data, "sweep", spath)
    try:
        cfg = SweepConfig.model_validate(section)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid sweep config ({spath})", _format_validation_error(exc, str(spath))
        ) from exc
    return cfg, _resolve_ref(cfg.base_scenario, spath.parent)
