"""Pilot Course A -- Edge Task Exchange V0 fixture (composition root).

SIMULATED PILOT SCENARIO -- NOT LIVE CUSTOMER DATA.

A thin wrapper over the existing Pilot Course A commissioning fixture: the
same synthetic site, a new deployment identity for this rehearsal, and the
two demo robots named ``picker-01`` and ``carrier-01``.  Commissioning stays
the owner of robot identity; this module only projects the validated
manifest into the plain admission facts the stdlib-only ``nxt_edge_task``
package consumes.  Nothing here connects to a device.
"""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_commissioning import CommissionedSite, dumps_manifest  # noqa: E402
from nxt_edge_task.contracts import AdmissionFacts  # noqa: E402
from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    SITE_ID,
    ZONE_ID,
    commissioned_site_payload as _base_payload,
)

DEPLOYMENT_ID = "pilot-a-edge-task-sim-v0"
PICKER_ID = "picker-01"
CARRIER_ID = "carrier-01"
ROBOT_IDS = (PICKER_ID, CARRIER_ID)
SIMULATION_ENV_ID = "sim-local-01"
DISCLAIMER = "SIMULATION — protocol rehearsal; no physical robot, no live site"


def commissioned_site_payload() -> dict:
    """The Pilot Course A manifest with this rehearsal's deployment and robot ids."""

    payload = copy.deepcopy(_base_payload())
    payload["deployment_id"] = DEPLOYMENT_ID
    robots = payload["robot_assets"]
    if len(robots) != len(ROBOT_IDS):
        raise RuntimeError("fixture expects exactly two robot assets to rename")
    renames: dict[str, str] = {}
    for robot, robot_id in zip(robots, ROBOT_IDS):
        old_id = robot["robot_id"]
        renames[old_id] = robot_id
        robot["robot_id"] = robot_id
        for constraint in robot["safety_constraints"]:
            if constraint["constraint_id"].startswith(f"{old_id}-"):
                constraint["constraint_id"] = robot_id + constraint["constraint_id"][len(old_id):]
    for binding in payload["sensor_bindings"]:
        old_id = binding["associated_asset_id"]
        robot_id = renames.get(old_id)
        if robot_id is None:
            continue
        binding["associated_asset_id"] = robot_id
        if binding["sensor_id"].startswith(f"sensor-fleet-{old_id}-"):
            binding["sensor_id"] = f"sensor-fleet-{robot_id}-" + binding["sensor_id"][len(f"sensor-fleet-{old_id}-"):]
        if binding["source"] == f"fleet.{old_id}":
            binding["source"] = f"fleet.{robot_id}"
        if binding["channel"].startswith(f"robot.{old_id}."):
            binding["channel"] = f"robot.{robot_id}." + binding["channel"][len(f"robot.{old_id}."):]
    return payload


def commissioned_site() -> CommissionedSite:
    return CommissionedSite.from_dict(commissioned_site_payload())


def manifest_digest(site: CommissionedSite) -> str:
    return hashlib.sha256(dumps_manifest(site).encode("utf-8")).hexdigest()


def admission_facts(site: CommissionedSite | None = None) -> AdmissionFacts:
    site = commissioned_site() if site is None else site
    return AdmissionFacts(
        site_id=site.site_id,
        deployment_id=site.deployment_id,
        robot_ids=frozenset(robot.robot_id for robot in site.robot_assets),
        zone_ids=frozenset(zone.zone_id for zone in site.spatial_reference.zone_definitions),
        manifest_digest=manifest_digest(site),
    )


__all__ = [
    "CARRIER_ID",
    "DEPLOYMENT_ID",
    "DISCLAIMER",
    "PICKER_ID",
    "ROBOT_IDS",
    "SIMULATION_ENV_ID",
    "SITE_ID",
    "ZONE_ID",
    "admission_facts",
    "commissioned_site",
    "commissioned_site_payload",
    "manifest_digest",
]
