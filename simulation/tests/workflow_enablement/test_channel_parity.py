"""Drift detection: the declared requirement set matches the real pipeline.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The requirement templates in ``nxt_workflow_enablement.requirements``
declare which canonical channels Range Operations v1 needs.  These
tests pin that declaration to the real fixture and the real assembler
in BOTH directions, so a hand-written list cannot silently drift:

* the pilot fixture's observation frame must carry exactly the declared
  set (no undeclared channel is needed, none is superfluous);
* the real assembler must report a clean assembly for the declared set
  and must flag every single declared channel as missing when it is
  removed.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_telemetry.assemble import assemble_from_observations

from nxt_workflow_enablement import required_range_ops_channels

from scripts.pilot_course_a_enablement_fixture import (
    enablement_manifest_payload,
    enablement_site,
    enablement_site_config,
)
from scripts.pilot_course_a_edge_fixture import pilot_observation_source


@pytest.fixture(scope="module")
def site():
    return enablement_site(enablement_manifest_payload())


@pytest.fixture(scope="module")
def observed(site):
    return pilot_observation_source(site).observe()


@pytest.fixture(scope="module")
def required(site):
    return required_range_ops_channels(
        zone_ids=tuple(
            sorted(
                zone.zone_id
                for zone in site.spatial_reference.zone_definitions
            )
        ),
        station_ids=("ST1",),
        robot_ids=tuple(
            sorted(robot.robot_id for robot in site.robot_assets)
        ),
    )


def test_the_fixture_frame_carries_exactly_the_required_channels(
    observed, required
):
    frame_channels = frozenset(
        observation.channel for observation in observed.frame.observations
    )
    assert frame_channels == frozenset(required)


def test_the_assembler_accepts_the_required_set_cleanly(
    site, observed, required
):
    state, report = assemble_from_observations(
        observed.frame, enablement_site_config(site), observed.upstream
    )
    assert report.missing_channels == ()
    assert report.stale_channels == ()
    assert report.consistency_issues == ()
    assert report.provenance_grade == "high"
    assert state.ball_flow.conserved is True


def test_every_required_channel_is_genuinely_required(
    site, observed, required
):
    config = enablement_site_config(site)
    for channel in required:
        reduced = dataclasses.replace(
            observed.frame,
            observations=tuple(
                observation
                for observation in observed.frame.observations
                if observation.channel != channel
            ),
        )
        _, report = assemble_from_observations(
            reduced, config, observed.upstream
        )
        assert channel in report.missing_channels, (
            f"dropping {channel!r} was not reported missing; the "
            "requirement template set has drifted from the assembler"
        )
