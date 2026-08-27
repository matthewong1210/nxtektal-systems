"""Two-directional parity between the required and offered map queries.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The enablement layer declares which map-query kinds the Player Caddy
workflow requires; the Course World Model package declares which kinds
its query service supports.  Neither package imports the other, so
this test pins the two declarations against each other and against the
real service surface: drift in either direction fails the suite.
"""

from __future__ import annotations

from nxt_course_world_model import MapQueryService, SUPPORTED_QUERY_KINDS
from nxt_workflow_enablement import REQUIRED_MAP_QUERY_KINDS

QUERY_KIND_TO_METHOD = {
    "elevation": "get_elevation",
    "hole_context": "get_hole_context",
    "nearby_hazards": "get_nearby_hazards",
    "restricted_area": "is_restricted",
    "slope": "get_slope",
    "surface": "get_surface",
    "trajectory_terrain_intersection": "intersect_trajectory_with_terrain",
}


def test_required_and_supported_query_kinds_are_identical():
    assert REQUIRED_MAP_QUERY_KINDS == SUPPORTED_QUERY_KINDS


def test_both_declarations_are_sorted_and_duplicate_free():
    for kinds in (REQUIRED_MAP_QUERY_KINDS, SUPPORTED_QUERY_KINDS):
        assert list(kinds) == sorted(kinds)
        assert len(set(kinds)) == len(kinds)


def test_every_declared_kind_is_a_real_service_method():
    assert set(QUERY_KIND_TO_METHOD) == set(SUPPORTED_QUERY_KINDS)
    for kind, method_name in QUERY_KIND_TO_METHOD.items():
        method = getattr(MapQueryService, method_name, None)
        assert callable(method), (
            f"declared query kind {kind!r} has no service method "
            f"{method_name!r}"
        )
