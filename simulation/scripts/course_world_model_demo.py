"""Course World Model V0 demo: Pilot Course A — Synthetic Hole 7.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A bounded, deterministic composition-root demo of the versioned
Course World Model and its read-only Map Query Service:

    invalid synthetic model -> rejected before it exists
    valid synthetic model   -> validated against the commissioned site
      -> canonical serialization + content digest + tamper refusal
      -> deterministic map queries (elevation, surface, slope, hole
         context, nearby hazards, restricted area, trajectory/terrain)
      -> workflow enablement WITHOUT Course Model evidence
      -> workflow enablement WITH declared Course Model evidence
      -> Range Operations readiness byte-identical either way
      -> Grounds and Player Caddy gain only their map prerequisites
         and remain NOT_READY

Nothing here connects to a physical device, network, or facility.
There is no raw LAS/LAZ or point-cloud ingestion, no drone or camera,
no live GPS, no cart, no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2,
Nav2, vendor SDK, socket, or cloud path, and no robot, actuator,
motion, navigation, charging, or emergency-stop surface.  Every value
is synthetic, the demo reads no wall clock, and identical invocations
produce byte-identical stdout and artifacts.

Usage (from simulation/):
    uv run --no-sync python -B scripts/course_world_model_demo.py \
        --out reports/course-world-model
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_course_world_model import (  # noqa: E402
    CourseWorldModelError,
    MapQueryService,
    TrajectorySample,
    dumps_model,
    validate_model_against_site,
    verify_model_payload,
)
from nxt_commissioning import canonical_projection_json  # noqa: E402
from nxt_workflow_enablement import (  # noqa: E402
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    canonical_report_json,
)

from scripts.pilot_course_a_course_model_fixture import (  # noqa: E402
    FRAME_ID,
    build_invalid_pilot_model,
    course_model_evidence,
    pilot_course_world_model,
)
from scripts.pilot_course_a_enablement_fixture import (  # noqa: E402
    DEPLOYMENT_ID,
    DISCLAIMER,
    SITE_ID,
    enablement_manifest_payload,
    enablement_site,
    evaluate_enablement,
)

MODEL_ARTIFACT = "course_model.json"
QUERY_ARTIFACT = "query_results.json"
BEFORE_ARTIFACT = "workflow_enablement_before.json"
AFTER_ARTIFACT = "workflow_enablement_after.json"
EVIDENCE_ARTIFACT = "course_world_model_demo.json"


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def _refuse_unsafe_root(root: Path) -> None:
    """Fail closed on a non-empty, file-valued, or unprovable root."""
    try:
        if not root.exists():
            return
        if not root.is_dir():
            raise SystemExit(
                f"refusing to write: the evidence root is not a directory: "
                f"{root}"
            )
        if any(root.iterdir()):
            raise SystemExit(
                "refusing to write into a non-empty evidence directory: "
                f"{root} (evidence artifacts are regenerated whole; use a "
                "fresh --out directory)"
            )
    except OSError as exc:
        raise SystemExit(
            f"refusing to write: cannot prove the evidence root is safe: "
            f"{root} ({exc})"
        )


def _readiness_lines(report_payload: dict) -> list[str]:
    lines = []
    for workflow_id, section in sorted(report_payload["workflows"].items()):
        lines.append(f"  {workflow_id}: {section['verdict']}")
        for label in ("satisfied", "missing", "unsupported_in_v0", "deferred"):
            if section[label]:
                lines.append(f"    {label}: " + ", ".join(section[label]))
    return lines


def run_demo(out_dir: Path) -> str:
    """Run the bounded demo, write evidence, and return the printed report."""
    root = out_dir / SITE_ID / DEPLOYMENT_ID
    _refuse_unsafe_root(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"refusing to write: cannot create the evidence root: {root} "
            f"({exc})"
        )

    lines = [DISCLAIMER, ""]
    lines.append(
        "Pilot Course A — Synthetic Hole 7 Spatial Baseline  "
        f"({SITE_ID}/{DEPLOYMENT_ID})"
    )
    lines.append("")

    # --- Step 1: an invalid model cannot even be constructed.
    try:
        build_invalid_pilot_model()
    except CourseWorldModelError as exc:
        invalid_refusal = str(exc)
    else:  # pragma: no cover - the invalid fixture must fail closed
        raise SystemExit("an invalid course model must never construct")
    lines.append("step 1 — invalid synthetic model (self-intersecting")
    lines.append("  fairway outline): rejected before any model exists")
    lines.append(f"  refusal: {invalid_refusal}")
    lines.append("")

    # --- Step 2: the valid model, bound to the commissioned site.
    site = enablement_site(enablement_manifest_payload())
    model = pilot_course_world_model()
    validate_model_against_site(model, site)
    model_bytes = dumps_model(model)
    verify_model_payload(json.loads(model_bytes))
    (root / MODEL_ARTIFACT).write_text(model_bytes, encoding="utf-8")
    lines.append("step 2 — valid model constructed and site-bound:")
    lines.append(
        f"  {model.course_model_id} {model.model_version} "
        f"effective_from={model.effective_from}"
    )
    lines.append(f"  frame: {model.frame.frame_id} "
                 f"({model.frame.crs_identifier}, local ENU metres)")
    lines.append(
        f"  bounds: x [{model.bounds.min_x}, {model.bounds.max_x}] m, "
        f"y [{model.bounds.min_y}, {model.bounds.max_y}] m, "
        f"resolution {model.elevation.resolution_m} m"
    )
    lines.append(f"  content digest: {model.content_digest}")

    tampered = json.loads(model_bytes)
    tampered["elevation"]["heights"][0] = 99.0
    try:
        verify_model_payload(tampered)
    except CourseWorldModelError as exc:
        tamper_refusal = str(exc)
    else:  # pragma: no cover - tampering must fail verification
        raise SystemExit("a tampered payload must fail digest verification")
    lines.append(
        "  tampered payload (one height changed): digest verification "
        "refused"
    )
    lines.append("")

    # --- Step 3: deterministic map queries.
    service = MapQueryService(model)
    trajectory = (
        TrajectorySample(t_s=0.0, x=100.0, y=100.0, z=40.0),
        TrajectorySample(t_s=1.0, x=120.0, y=100.0, z=25.0),
        TrajectorySample(t_s=2.0, x=140.0, y=100.0, z=10.0),
        TrajectorySample(t_s=3.0, x=160.0, y=100.0, z=0.5),
        TrajectorySample(t_s=4.0, x=180.0, y=100.0, z=-5.0),
    )
    queries = {
        "elevation_fairway": service.get_elevation(150.0, 100.0).to_dict(),
        "elevation_out_of_bounds": service.get_elevation(
            -50.0, 100.0
        ).to_dict(),
        "surface_fairway": service.get_surface(150.0, 100.0).to_dict(),
        "surface_cart_path_overlay": service.get_surface(
            100.0, 75.0
        ).to_dict(),
        "slope_fairway": service.get_slope(150.0, 100.0).to_dict(),
        "hole_context_fairway": service.get_hole_context(
            150.0, 100.0
        ).to_dict(),
        "hole_context_outside_holes": service.get_hole_context(
            7.0, 190.0
        ).to_dict(),
        "nearby_hazards_greenside": service.get_nearby_hazards(
            233.0, 84.0, 120.0
        ).to_dict(),
        "restricted_maintenance_yard": service.is_restricted(
            270.0, 30.0
        ).to_dict(),
        "restricted_commissioned_zone": service.is_restricted(
            20.0, 20.0
        ).to_dict(),
        "trajectory_terrain_intersection": (
            service.intersect_trajectory_with_terrain(
                trajectory, frame_id=FRAME_ID
            ).to_dict()
        ),
    }
    (root / QUERY_ARTIFACT).write_text(
        _canonical_json(queries) + "\n", encoding="utf-8"
    )
    lines.append("step 3 — deterministic map queries:")
    elevation = queries["elevation_fairway"]
    lines.append(
        f"  elevation(150, 100) = {elevation['elevation_m']} m "
        f"[{elevation['status']}]"
    )
    surface = queries["surface_fairway"]
    lines.append(
        f"  surface(150, 100) = {surface['surface_type']} "
        f"({surface['feature_id']}, hole {surface['hole_id']})"
    )
    slope = queries["slope_fairway"]
    lines.append(
        f"  slope(150, 100): grade {slope['grade_percent']:.4f}% "
        f"aspect {slope['aspect_deg']:.2f} deg"
    )
    hazards = queries["nearby_hazards_greenside"]
    lines.append(
        "  hazards within 120 m of (233, 84): "
        + ", ".join(
            f"{hit['feature_id']} ({hit['hazard_type']}, "
            f"{hit['distance_m']:.3f} m)"
            for hit in hazards["hazards"]
        )
    )
    restricted = queries["restricted_maintenance_yard"]
    lines.append(
        f"  restricted(270, 30) = {restricted['restricted']} "
        f"({', '.join(m['feature_id'] for m in restricted['matches'])})"
    )
    intersection = queries["trajectory_terrain_intersection"]
    lines.append(
        "  trajectory intersects terrain in segment "
        f"{intersection['segment_index']} at "
        f"({intersection['x']:.3f}, {intersection['y']:.3f}, "
        f"{intersection['z']:.3f}) on {intersection['surface_type']}"
    )
    lines.append(
        "  out-of-bounds elevation status: "
        f"{queries['elevation_out_of_bounds']['status']} (no value "
        "fabricated)"
    )
    lines.append("")

    # --- Step 4: workflow enablement without Course Model evidence.
    payload = enablement_manifest_payload()
    _, before_report = evaluate_enablement(payload)
    before_payload = before_report.to_payload()
    (root / BEFORE_ARTIFACT).write_text(
        canonical_report_json(before_report), encoding="utf-8"
    )
    lines.append("step 4 — workflow enablement without Course Model "
                 "evidence:")
    lines.extend(_readiness_lines(before_payload))
    lines.append("")

    # --- Step 5: workflow enablement with declared Course Model evidence.
    evidence = course_model_evidence(model, site)
    _, after_report = evaluate_enablement(
        payload, course_model_evidence=evidence
    )
    after_payload = after_report.to_payload()
    (root / AFTER_ARTIFACT).write_text(
        canonical_report_json(after_report), encoding="utf-8"
    )
    lines.append("step 5 — workflow enablement with Course Model evidence:")
    lines.extend(_readiness_lines(after_payload))

    range_ops_before = canonical_projection_json(
        before_payload["workflows"][RANGE_OPS_WORKFLOW_ID]
    )
    range_ops_after = canonical_projection_json(
        after_payload["workflows"][RANGE_OPS_WORKFLOW_ID]
    )
    if range_ops_before != range_ops_after:  # pragma: no cover
        raise SystemExit(
            "Course Model evidence changed the Range Operations readiness "
            "section; spatial evidence must never touch Range Operations"
        )
    for workflow_id in (GROUNDS_WORKFLOW_ID, PLAYER_CADDY_WORKFLOW_ID):
        if after_payload["workflows"][workflow_id]["verdict"] != "NOT_READY":
            raise SystemExit(  # pragma: no cover
                f"{workflow_id} must remain NOT_READY; a map alone does "
                "not make a course workflow ready"
            )
    lines.append(
        "  range operations section byte-identical with and without "
        "Course Model evidence"
    )
    lines.append(
        "  grounds gained: "
        + ", ".join(
            after_payload["workflows"][GROUNDS_WORKFLOW_ID]["satisfied"]
        )
        + " — still NOT_READY"
    )
    lines.append(
        "  player caddy gained: "
        + ", ".join(
            after_payload["workflows"][PLAYER_CADDY_WORKFLOW_ID]["satisfied"]
        )
        + " — still NOT_READY"
    )
    lines.append("")

    artifacts = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )
    evidence_payload = {
        "disclaimer": DISCLAIMER,
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "course_model": {
            "course_model_id": model.course_model_id,
            "model_version": model.model_version,
            "content_digest": model.content_digest,
            "frame_id": model.frame.frame_id,
            "crs_identifier": model.frame.crs_identifier,
            "resolution_m": model.elevation.resolution_m,
            "invalid_model_refusal": invalid_refusal,
            "tampered_payload_refusal": tamper_refusal,
        },
        "physical_boundary": {
            "raw_scan_ingestion": False,
            "live_transport": False,
            "physical_device_connection": False,
            "cart_or_robot_positioning": False,
            "route_planning_or_navigation": False,
            "robot_command_surface": False,
            "actuator_or_estop_control": False,
            "network_call": False,
            "note": (
                "synthetic processed-scan fixture only; the model and its "
                "queries are spatial information and can neither reach a "
                "device nor command a cart or robot"
            ),
        },
        "reports": {
            "before": {
                "file": BEFORE_ARTIFACT,
                "report_id": before_payload["report_id"],
                "workflow_verdicts": {
                    workflow_id: section["verdict"]
                    for workflow_id, section in before_payload[
                        "workflows"
                    ].items()
                },
            },
            "after": {
                "file": AFTER_ARTIFACT,
                "report_id": after_payload["report_id"],
                "workflow_verdicts": {
                    workflow_id: section["verdict"]
                    for workflow_id, section in after_payload[
                        "workflows"
                    ].items()
                },
                "grounds_satisfied": after_payload["workflows"][
                    GROUNDS_WORKFLOW_ID
                ]["satisfied"],
                "player_caddy_satisfied": after_payload["workflows"][
                    PLAYER_CADDY_WORKFLOW_ID
                ]["satisfied"],
            },
            "range_ops_section_byte_identical": True,
        },
        "artifacts": sorted([*artifacts, EVIDENCE_ARTIFACT]),
    }
    (root / EVIDENCE_ARTIFACT).write_text(
        _canonical_json(evidence_payload) + "\n", encoding="utf-8"
    )

    lines.append("evidence artifacts:")
    lines.extend(
        f"  {name}" for name in sorted([*artifacts, EVIDENCE_ARTIFACT])
    )
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic Pilot Course A Course World Model demo "
            "(synthetic fixtures only)"
        )
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output directory for deterministic JSON evidence",
    )
    args = parser.parse_args(argv)
    print(run_demo(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
