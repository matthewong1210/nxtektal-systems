"""Pilot Course A — Synthetic Hole 7 Spatial Baseline (composition root).

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

This module is the deterministic spatial fixture for the shared Pilot
Course A site: one synthetic hole with tee, fairway, rough, green,
bunker, water, a cart path, restricted zones, and a non-flat elevation
surface, bound to the commissioned EPSG:32651 spatial reference and
the enablement deployment identity.  Every coordinate, height, and
provenance value is synthetic; no real course was scanned and no
survey accuracy is claimed.

As a composition root it may import both the Course World Model
package and the workflow-enablement package; the two packages never
import each other.  ``course_model_evidence`` is the one honest
derivation of plain-data Course Model evidence from a validated model.
"""

from __future__ import annotations

import sys
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_commissioning import (  # noqa: E402
    CommissionedSite,
    Provenance,
    ProvenanceSource,
)
from nxt_course_world_model import (  # noqa: E402
    CartPath,
    CourseCoordinateFrame,
    CourseWorldModel,
    ElevationGrid,
    HoleDefinition,
    PolygonRing,
    Polyline,
    RestrictedZone,
    RestrictionCategory,
    SUPPORTED_QUERY_KINDS,
    ScanSourceReference,
    ScanSourceType,
    SurfaceFeature,
    SurfaceType,
    build_course_world_model,
    validate_model_against_site,
)
from nxt_workflow_enablement import CourseModelEvidence  # noqa: E402

from scripts.pilot_course_a_enablement_fixture import (  # noqa: E402
    DEPLOYMENT_ID,
    DISCLAIMER,
    SITE_ID,
)

COURSE_MODEL_ID = "pilot-course-a.course-map"
MODEL_VERSION = "v1"
FRAME_ID = "pilot-course-a.course-frame.v1"
EFFECTIVE_FROM = "2026-07-20T00:00:00+08:00"
DISPLAY_NAME = "Pilot Course A — Synthetic Hole 7 Spatial Baseline"

# The commissioned Pilot Course A spatial-reference identity (see
# scripts/pilot_course_a_edge_fixture.py): EPSG:32651, metres, ENU
# axes, facility origin at easting 346000 / northing 3456000 / z 5.
PILOT_CRS_KIND = "epsg"
PILOT_CRS_IDENTIFIER = "EPSG:32651"
PILOT_ORIGIN_CRS = (346000.0, 3456000.0, 5.0)

_GRID_N_COLS = 31
_GRID_N_ROWS = 21
_GRID_CELL_M = 10.0


def pilot_fixture_provenance(
    source_id: str = "synthetic-scan-fixture",
) -> Provenance:
    return Provenance(
        source_type=ProvenanceSource.IMPORTED_RECORD,
        source_id=source_id,
        captured_at="2026-07-15T08:30:00+08:00",
        captured_by="pilot-course-a-fixture-author",
        evidence_uri=None,
        notes=DISCLAIMER,
    )


def pilot_frame() -> CourseCoordinateFrame:
    return CourseCoordinateFrame(
        frame_id=FRAME_ID,
        crs_kind=PILOT_CRS_KIND,
        crs_identifier=PILOT_CRS_IDENTIFIER,
        crs_horizontal_unit="m",
        crs_vertical_unit="m",
        crs_axes=("east", "north", "up"),
        origin_crs_x=PILOT_ORIGIN_CRS[0],
        origin_crs_y=PILOT_ORIGIN_CRS[1],
        origin_crs_z=PILOT_ORIGIN_CRS[2],
        vertical_basis=(
            "metres above the commissioned facility origin elevation"
        ),
    )


def pilot_height(x: float, y: float) -> float:
    """The deterministic, non-flat synthetic surface height in metres."""
    return round(1.5 + 0.01 * x + 0.005 * y, 6)


def pilot_elevation_grid() -> ElevationGrid:
    return ElevationGrid(
        origin_x=0.0,
        origin_y=0.0,
        cell_size_m=_GRID_CELL_M,
        n_rows=_GRID_N_ROWS,
        n_cols=_GRID_N_COLS,
        heights=tuple(
            pilot_height(col * _GRID_CELL_M, row * _GRID_CELL_M)
            for row in range(_GRID_N_ROWS)
            for col in range(_GRID_N_COLS)
        ),
    )


def rectangle_ring(
    min_x: float, min_y: float, max_x: float, max_y: float
) -> PolygonRing:
    return PolygonRing(
        vertices=(
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        )
    )


def pilot_course_boundary() -> PolygonRing:
    return rectangle_ring(5.0, 5.0, 295.0, 195.0)


def pilot_holes() -> tuple[HoleDefinition, ...]:
    return (
        HoleDefinition(
            hole_id="hole-7",
            hole_number=7,
            boundary=rectangle_ring(10.0, 40.0, 290.0, 160.0),
        ),
    )


def pilot_surfaces() -> tuple[SurfaceFeature, ...]:
    return (
        SurfaceFeature(
            feature_id="hole-7-tee-a",
            surface_type=SurfaceType.TEE,
            polygon=rectangle_ring(20.0, 90.0, 40.0, 110.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-fairway",
            surface_type=SurfaceType.FAIRWAY,
            polygon=rectangle_ring(45.0, 80.0, 230.0, 120.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-rough-south",
            surface_type=SurfaceType.ROUGH,
            polygon=rectangle_ring(45.0, 60.0, 230.0, 78.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-rough-north",
            surface_type=SurfaceType.ROUGH,
            polygon=rectangle_ring(45.0, 122.0, 230.0, 140.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-green",
            surface_type=SurfaceType.GREEN,
            polygon=rectangle_ring(235.0, 85.0, 265.0, 115.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-bunker-greenside",
            surface_type=SurfaceType.BUNKER,
            polygon=rectangle_ring(235.0, 70.0, 250.0, 82.0),
            hole_id="hole-7",
        ),
        SurfaceFeature(
            feature_id="hole-7-water-pond",
            surface_type=SurfaceType.WATER,
            polygon=rectangle_ring(100.0, 142.0, 160.0, 158.0),
            hole_id="hole-7",
        ),
    )


def pilot_cart_paths() -> tuple[CartPath, ...]:
    return (
        CartPath(
            feature_id="hole-7-cart-path",
            centerline=Polyline(
                vertices=((15.0, 75.0), (240.0, 75.0), (270.0, 95.0))
            ),
            width_m=3.0,
            hole_id="hole-7",
        ),
    )


def pilot_restricted_zones() -> tuple[RestrictedZone, ...]:
    return (
        RestrictedZone(
            feature_id="restricted-maintenance-yard",
            category=RestrictionCategory.NO_GO,
            polygon=rectangle_ring(260.0, 20.0, 290.0, 50.0),
            commissioned_zone_id=None,
        ),
        RestrictedZone(
            feature_id="restricted-z1-collection",
            category=RestrictionCategory.MAINTENANCE_ONLY,
            polygon=rectangle_ring(10.0, 10.0, 40.0, 35.0),
            commissioned_zone_id="Z1",
        ),
    )


def pilot_scan_sources() -> tuple[ScanSourceReference, ...]:
    return (
        ScanSourceReference(
            source_id="scan-2026-07-pilot-a",
            source_type=ScanSourceType.SYNTHETIC_FIXTURE,
            capture_id="capture-0007",
            processing_pipeline_id="pipeline-fixture-v0",
            source_uri="urn:nxtektal:fixture:pilot-course-a:hole7-scan-v1",
            source_digest=(
                "sha256:"
                "9d2f6d0f9b1e4c7a8d3b5e6f7a8b9c0d"
                "1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b"
            ),
            provenance=pilot_fixture_provenance(),
        ),
    )


def pilot_model_parts() -> dict[str, object]:
    """Every constructor argument for the pilot model, by keyword."""
    return {
        "course_model_id": COURSE_MODEL_ID,
        "model_version": MODEL_VERSION,
        "supersedes_version": None,
        "effective_from": EFFECTIVE_FROM,
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "display_name": DISPLAY_NAME,
        "frame": pilot_frame(),
        "elevation": pilot_elevation_grid(),
        "course_boundary": pilot_course_boundary(),
        "holes": pilot_holes(),
        "surfaces": pilot_surfaces(),
        "cart_paths": pilot_cart_paths(),
        "restricted_zones": pilot_restricted_zones(),
        "scan_sources": pilot_scan_sources(),
    }


def pilot_course_world_model() -> CourseWorldModel:
    """The valid Pilot Course A Synthetic Hole 7 spatial baseline."""
    return build_course_world_model(**pilot_model_parts())


def build_invalid_pilot_model() -> CourseWorldModel:
    """Attempt a deliberately broken model; always raises.

    The broken variant replaces the fairway with a self-intersecting
    bow-tie outline, so validation fails before any model exists.  The
    demo uses the raised error to show that an invalid model cannot be
    constructed, cannot be serialized, and can never satisfy a
    workflow prerequisite.
    """
    parts = pilot_model_parts()
    surfaces = tuple(
        surface
        for surface in parts["surfaces"]  # type: ignore[union-attr]
        if surface.feature_id != "hole-7-fairway"
    )
    parts["surfaces"] = surfaces + (
        SurfaceFeature(
            feature_id="hole-7-fairway",
            surface_type=SurfaceType.FAIRWAY,
            polygon=PolygonRing(
                vertices=(
                    (45.0, 80.0),
                    (230.0, 120.0),
                    (230.0, 80.0),
                    (45.0, 120.0),
                )
            ),
            hole_id="hole-7",
        ),
    )
    return build_course_world_model(**parts)


def course_model_evidence(
    model: CourseWorldModel,
    site: CommissionedSite | None = None,
) -> CourseModelEvidence:
    """Derive plain-data Course Model evidence from a validated model.

    When a validated commissioned site is supplied the model is first
    cross-checked against it, so the composition root cannot declare
    evidence for a model that does not bind to the site it is about.
    The declared fields are copied from the model's public surface; the
    supported query kinds are the real query-service declaration, not
    a hand-written claim.
    """
    if not isinstance(model, CourseWorldModel):
        raise TypeError("model must be a CourseWorldModel")
    if site is not None:
        validate_model_against_site(model, site)
    return CourseModelEvidence(
        course_model_id=model.course_model_id,
        model_version=model.model_version,
        content_digest=model.content_digest,
        site_id=model.site_id,
        deployment_id=model.deployment_id,
        frame_id=model.frame.frame_id,
        crs_kind=model.frame.crs_kind,
        crs_identifier=model.frame.crs_identifier,
        crs_horizontal_unit=model.frame.crs_horizontal_unit,
        crs_vertical_unit=model.frame.crs_vertical_unit,
        crs_axes=model.frame.crs_axes,
        origin_crs_x=model.frame.origin_crs_x,
        origin_crs_y=model.frame.origin_crs_y,
        origin_crs_z=model.frame.origin_crs_z,
        supported_queries=SUPPORTED_QUERY_KINDS,
        resolution_m=model.elevation.resolution_m,
    )


__all__ = [
    "COURSE_MODEL_ID",
    "DISPLAY_NAME",
    "EFFECTIVE_FROM",
    "FRAME_ID",
    "MODEL_VERSION",
    "PILOT_CRS_IDENTIFIER",
    "PILOT_CRS_KIND",
    "PILOT_ORIGIN_CRS",
    "build_invalid_pilot_model",
    "course_model_evidence",
    "pilot_cart_paths",
    "pilot_course_boundary",
    "pilot_course_world_model",
    "pilot_elevation_grid",
    "pilot_fixture_provenance",
    "pilot_frame",
    "pilot_height",
    "pilot_holes",
    "pilot_model_parts",
    "pilot_restricted_zones",
    "pilot_scan_sources",
    "pilot_surfaces",
    "rectangle_ring",
]
