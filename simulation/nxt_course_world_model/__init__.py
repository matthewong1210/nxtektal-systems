"""NXTektal Course World Model V0 -- immutable, versioned spatial truth.

One shared operating layer serves multiple workflows at the same
outdoor facility, and they all need the same spatial baseline.  This
package owns exactly that baseline and nothing else: the compact,
versioned, queryable Course World Model derived from *processed*
course-scan products, plus the pure, deterministic, read-only Map
Query Service over it.

The engineering meaning of "map once" is: establish the spatial
baseline once, create immutable controlled revisions when the course
changes, and bind every spatial observation and query to an exact
model identity and version.

What this package owns:

* the course-local coordinate frame (right-handed ENU, metres) bound
  to the commissioned coordinate reference and facility origin;
* the finite elevation surface and its deterministic bilinear and
  slope queries;
* the closed semantic vocabulary for holes, playing surfaces, cart
  paths, and restricted areas, with deterministic geometry rules;
* immutable model identity, controlled revision semantics, canonical
  serialization, and content addressing (a digest proves content
  consistency, never authorship, surveying accuracy, or authenticity);
* the read-only Map Query Service, including the narrow
  trajectory/terrain intersection over an already-computed trajectory.

What this package is not: it is not the commissioned manifest (the
commissioning package remains the sole owner of site identity, the
surveyed coordinate reference, zones, assets, and calibration truth --
this model only references that identity and fails closed on any
mismatch); it is not facility state, telemetry, or a live observation
stream; it is not workflow readiness (readiness stays with the
workflow-enablement layer, which consumes course-model facts as
declared plain data from composition roots); it is not the digital
twin (the twin remains a downstream projection and never a source of
spatial truth); and it is not a raw-scan pipeline -- no LAS/LAZ or
point-cloud parsing, no photogrammetry, no SLAM, and no raw scan bytes
ever enter this package; processed sources are referenced by stable
URI, digest, and provenance only.

The package is deliberately incapable of touching the physical world.
It opens no file, socket, or device; there is no Modbus, serial,
MQTT, Kafka, OPC-UA, ROS 2, Nav2, vendor-SDK, camera, cloud, or
network path here.  It is not a route planner and not a navigation
stack, it commands no cart or robot, and it has no actuator, motion,
charging, or emergency stop surface.  A restricted-area answer is
spatial information only; enforcement, admission, and execution
authority live nowhere in this package and may not be added to it.

Everything shipped in this repository is synthetic:
SIMULATED PILOT SCENARIO -- NOT LIVE CUSTOMER DATA.  No real course
was scanned, no survey accuracy was validated, and no live cart,
camera, launch radar, or customer deployment exists.

See ``simulation/docs/course_world_model_v0.md`` for the complete
contract, boundaries, and next seams.
"""

from .elevation import ElevationGrid
from .errors import CourseModelQueryError, CourseWorldModelError
from .features import (
    CartPath,
    HAZARD_SURFACE_TYPES,
    HoleDefinition,
    RestrictedZone,
    RestrictionCategory,
    ScanSourceReference,
    ScanSourceType,
    SURFACE_TIE_BREAK_ORDER,
    SurfaceFeature,
    SurfaceType,
)
from .frame import (
    CourseCoordinateFrame,
    LOCAL_FRAME_AXES,
    LOCAL_FRAME_HANDEDNESS,
    LOCAL_FRAME_UNIT,
)
from .geometry import PolygonRing, Polyline
from .model import (
    COURSE_WORLD_MODEL_SCHEMA,
    CourseWorldModel,
    ModelBounds,
    build_course_world_model,
    dumps_model,
    require_consistent_content,
    validate_model_against_site,
    validate_revision,
    verify_model_payload,
)
from .query import (
    ElevationResult,
    HazardHit,
    HoleContextResult,
    MapQueryService,
    ModelRef,
    NearbyHazardsResult,
    QueryStatus,
    RestrictedMatch,
    RestrictedResult,
    SUPPORTED_QUERY_KINDS,
    SlopeResult,
    SurfaceResult,
    TrajectoryIntersectionResult,
    TrajectorySample,
)

__all__ = [
    "COURSE_WORLD_MODEL_SCHEMA",
    "CartPath",
    "CourseCoordinateFrame",
    "CourseModelQueryError",
    "CourseWorldModel",
    "CourseWorldModelError",
    "ElevationGrid",
    "ElevationResult",
    "HAZARD_SURFACE_TYPES",
    "HazardHit",
    "HoleContextResult",
    "HoleDefinition",
    "LOCAL_FRAME_AXES",
    "LOCAL_FRAME_HANDEDNESS",
    "LOCAL_FRAME_UNIT",
    "MapQueryService",
    "ModelBounds",
    "ModelRef",
    "NearbyHazardsResult",
    "PolygonRing",
    "Polyline",
    "QueryStatus",
    "RestrictedMatch",
    "RestrictedResult",
    "RestrictedZone",
    "RestrictionCategory",
    "SUPPORTED_QUERY_KINDS",
    "SURFACE_TIE_BREAK_ORDER",
    "ScanSourceReference",
    "ScanSourceType",
    "SlopeResult",
    "SurfaceFeature",
    "SurfaceResult",
    "SurfaceType",
    "TrajectoryIntersectionResult",
    "TrajectorySample",
    "build_course_world_model",
    "dumps_model",
    "require_consistent_content",
    "validate_model_against_site",
    "validate_revision",
    "verify_model_payload",
]
