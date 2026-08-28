"""Shared fixtures for Course World Model V0 tests.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The canonical geometry lives in
``scripts/pilot_course_a_course_model_fixture.py`` (the composition
root); these helpers only wrap it with per-test overrides so the unit
suite and the pilot fixture can never drift apart.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from nxt_course_world_model import (
    CourseCoordinateFrame,
    CourseWorldModel,
    ElevationGrid,
    PolygonRing,
    build_course_world_model,
)

from scripts.pilot_course_a_course_model_fixture import (
    COURSE_MODEL_ID,
    EFFECTIVE_FROM,
    FRAME_ID,
    MODEL_VERSION,
    PILOT_CRS_IDENTIFIER,
    PILOT_CRS_KIND,
    PILOT_ORIGIN_CRS,
    pilot_cart_paths,
    pilot_elevation_grid,
    pilot_fixture_provenance,
    pilot_frame,
    pilot_height,
    pilot_holes,
    pilot_model_parts,
    pilot_restricted_zones,
    pilot_scan_sources,
    pilot_surfaces,
)
from scripts.pilot_course_a_enablement_fixture import (
    DEPLOYMENT_ID,
    SITE_ID,
)

__all__ = [
    "COURSE_MODEL_ID",
    "DEPLOYMENT_ID",
    "EFFECTIVE_FROM",
    "FRAME_ID",
    "MODEL_VERSION",
    "SITE_ID",
    "build_fixture_model",
    "fixture_cart_paths",
    "fixture_height",
    "fixture_holes",
    "fixture_provenance",
    "fixture_restricted_zones",
    "fixture_scan_sources",
    "fixture_surfaces",
    "make_frame",
    "make_grid",
    "make_ring",
    "rectangle",
]

CRS_KIND = PILOT_CRS_KIND
CRS_IDENTIFIER = PILOT_CRS_IDENTIFIER
ORIGIN_CRS = PILOT_ORIGIN_CRS

fixture_height = pilot_height
fixture_provenance = pilot_fixture_provenance
fixture_holes = pilot_holes
fixture_surfaces = pilot_surfaces
fixture_cart_paths = pilot_cart_paths
fixture_restricted_zones = pilot_restricted_zones
fixture_scan_sources = pilot_scan_sources


def make_frame(**changes) -> CourseCoordinateFrame:
    return dataclasses.replace(pilot_frame(), **changes)


def make_grid(**changes) -> ElevationGrid:
    return dataclasses.replace(pilot_elevation_grid(), **changes)


def rectangle(min_x: float, min_y: float, max_x: float, max_y: float):
    return (
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
    )


def make_ring(vertices=None) -> PolygonRing:
    return PolygonRing(
        vertices=tuple(vertices or rectangle(0.0, 0.0, 10.0, 10.0))
    )


def build_fixture_model(**changes) -> CourseWorldModel:
    values = pilot_model_parts()
    values.update(changes)
    return build_course_world_model(**values)


@pytest.fixture(scope="session")
def session_model() -> CourseWorldModel:
    return build_fixture_model()


@pytest.fixture
def model(session_model) -> CourseWorldModel:
    return session_model


@pytest.fixture
def model_payload(session_model) -> dict:
    return copy.deepcopy(session_model.to_dict())
