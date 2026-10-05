"""Independent 18-hole synthetic map and inspection checkpoints.

This composition root builds existing commissioning and Course World Model
contracts; it creates no physical readiness, camera calibration, or live pose
channel. Every metre and source is synthetic. Carts/cameras in a rehearsal are
scenario entities, not invented assets in the closed commissioning vocabulary.
"""

from __future__ import annotations

import hashlib

from nxt_commissioning import (
    CommissionedSite, CoordinateReferenceSystem, CoordinateSystemKind,
    GeometryReference, GeometryType, LocationMetadata, Provenance,
    ProvenanceSource, SpatialPoint, SpatialReference, ZoneDefinition,
)
from nxt_course_world_model import (
    CartPath, CourseCoordinateFrame, CourseWorldModel, ElevationGrid,
    HoleDefinition, PolygonRing, Polyline, ScanSourceReference, ScanSourceType,
    SurfaceFeature, SurfaceType, build_course_world_model,
    validate_model_against_site,
)

SITE_ID = "synthetic-course-18"
DEPLOYMENT_ID = "synthetic-course-18-monitoring-v0"
COURSE_MODEL_ID = "synthetic-course-18.map"
MODEL_VERSION = "v1"
FRAME_ID = "synthetic-course-18.enu.v1"
CRS_ID = "LOCAL:synthetic-course-18-enu"
EFFECTIVE_FROM = "2026-09-16T00:00:00Z"
DISCLAIMER = "SIMULATION ONLY — synthetic geometry, observations and outcomes; no live course data"


def _ring(x0: float, y0: float, x1: float, y1: float) -> PolygonRing:
    return PolygonRing(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def _origin(hole_number: int) -> tuple[float, float]:
    return 10.0 + ((hole_number - 1) % 6) * 300.0, 10.0 + ((hole_number - 1) // 6) * 180.0


def _provenance() -> Provenance:
    return Provenance(
        source_type=ProvenanceSource.IMPORTED_RECORD,
        source_id="synthetic-course-18-fixture-v1",
        captured_at=EFFECTIVE_FROM,
        captured_by="synthetic-fixture-author",
        evidence_uri="synthetic:course-18-layout-v1",
        notes=DISCLAIMER,
    )


def _height(x: float, y: float) -> float:
    return round(0.002 * x + 0.001 * y, 6)


def _holes() -> tuple[HoleDefinition, ...]:
    holes = []
    for number in range(1, 19):
        x, y = _origin(number)
        holes.append(HoleDefinition(f"hole-{number:02d}", number, _ring(x, y, x + 280, y + 160)))
    return tuple(holes)


def _manifest(holes: tuple[HoleDefinition, ...]) -> CommissionedSite:
    provenance = _provenance()
    geometries = tuple(
        GeometryReference(
            geometry_id=f"geometry-{hole.hole_id}",
            geometry_type=GeometryType.POLYGON,
            points=tuple(SpatialPoint(x, y, _height(x, y), provenance) for x, y in hole.boundary.vertices),
            source_uri=f"synthetic:course-18/{hole.hole_id}",
            provenance=provenance,
        )
        for hole in holes
    )
    zones = tuple(
        ZoneDefinition(
            zone_id=f"zone-{hole.hole_id}", name=f"Synthetic hole {hole.hole_number}",
            zone_type="course_hole", geometry_reference=f"geometry-{hole.hole_id}",
            safety_classification="synthetic-layout-only", provenance=provenance,
        )
        for hole in holes
    )
    return CommissionedSite(
        site_id=SITE_ID, deployment_id=DEPLOYMENT_ID,
        facility_name="Synthetic 18-hole Course", timezone="UTC",
        location_metadata=LocationMetadata(
            address_lines=(), locality=None, region=None, postal_code=None,
            country_code="US", site_reference="synthetic-no-physical-location",
            provenance=provenance,
        ),
        operating_constraints=(),
        spatial_reference=SpatialReference(
            coordinate_system=CoordinateReferenceSystem(
                kind=CoordinateSystemKind.LOCAL_CARTESIAN, identifier=CRS_ID,
                horizontal_unit="m", vertical_unit="m", axes=("east", "north", "up"),
                provenance=provenance,
            ),
            facility_origin=SpatialPoint(0.0, 0.0, 0.0, provenance),
            geometry_references=geometries, zone_definitions=zones,
        ),
        equipment_assets=(), robot_assets=(), sensor_bindings=(), provenance=provenance,
    )


def checkpoint_catalog() -> list[dict[str, object]]:
    """54 declared point checks, not complete surface inspection coverage.

    The cart remains on a cart-path point; the target lies inside the surface.
    The composition root must supply separate uncertainty for both estimates.
    """
    catalog = []
    for number in range(1, 19):
        x, y = _origin(number)
        for kind, tx, ty, cx, cy in (
            ("fairway", 120.0, 80.0, 120.0, 25.0),
            ("bunker", 227.0, 49.0, 227.0, 25.0),
            ("green", 240.0, 80.0, 270.0, 80.0),
        ):
            catalog.append({
                "id": f"cp-h{number:02d}-{kind}", "hole_number": number, "hole": number,
                "feature_id": f"hole-{number:02d}-{kind}", "surface_type": kind,
                "x_m": x + tx, "y_m": y + ty,
                "cart_x_m": x + cx, "cart_y_m": y + cy,
            })
    return catalog


def build_fixture() -> tuple[CommissionedSite, CourseWorldModel]:
    """Return an independent validated manifest and its immutable spatial map."""
    holes = _holes()
    site = _manifest(holes)
    surfaces = []
    paths = []
    for hole in holes:
        x, y = _origin(hole.hole_number)
        for kind, surface_type, bounds in (
            ("fairway", SurfaceType.FAIRWAY, (40.0, 60.0, 200.0, 100.0)),
            ("green", SurfaceType.GREEN, (225.0, 65.0, 255.0, 95.0)),
            ("bunker", SurfaceType.BUNKER, (215.0, 40.0, 240.0, 58.0)),
        ):
            x0, y0, x1, y1 = bounds
            surfaces.append(SurfaceFeature(
                feature_id=f"{hole.hole_id}-{kind}", surface_type=surface_type,
                polygon=_ring(x + x0, y + y0, x + x1, y + y1), hole_id=hole.hole_id,
            ))
        paths.append(CartPath(
            feature_id=f"{hole.hole_id}-cart-path", width_m=3.0, hole_id=hole.hole_id,
            centerline=Polyline(((x + 20, y + 25), (x + 270, y + 25), (x + 270, y + 110))),
        ))
    source = ScanSourceReference(
        source_id="synthetic-course-18-source-v1", source_type=ScanSourceType.SYNTHETIC_FIXTURE,
        capture_id="synthetic-course-18-capture-v1", processing_pipeline_id="synthetic-layout-v1",
        source_uri="synthetic:course-18-layout-v1",
        source_digest="sha256:" + hashlib.sha256(b"synthetic-course-18-layout-v1-3x6-300x180").hexdigest(),
        provenance=_provenance(),
    )
    model = build_course_world_model(
        course_model_id=COURSE_MODEL_ID, model_version=MODEL_VERSION, supersedes_version=None,
        effective_from=EFFECTIVE_FROM, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID,
        display_name="Synthetic 18-hole course — 54 point checks",
        frame=CourseCoordinateFrame(
            frame_id=FRAME_ID, crs_kind="local_cartesian", crs_identifier=CRS_ID,
            crs_horizontal_unit="m", crs_vertical_unit="m", crs_axes=("east", "north", "up"),
            origin_crs_x=0.0, origin_crs_y=0.0, origin_crs_z=0.0,
            vertical_basis="synthetic metres above the fixture origin",
        ),
        elevation=ElevationGrid(
            origin_x=0.0, origin_y=0.0, cell_size_m=20.0, n_rows=28, n_cols=91,
            heights=tuple(_height(col * 20.0, row * 20.0) for row in range(28) for col in range(91)),
        ),
        course_boundary=_ring(0.0, 0.0, 1800.0, 540.0),
        holes=holes, surfaces=tuple(surfaces), cart_paths=tuple(paths),
        restricted_zones=(), scan_sources=(source,),
    )
    validate_model_against_site(model, site)
    return site, model
