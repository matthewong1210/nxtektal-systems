"""NXTektal facility commissioning source-of-truth contracts.

The package owns static facility identity, spatial declarations, equipment,
robots, sensor bindings, and their provenance.  It intentionally owns no
runtime observations or operational state.
"""

from .contracts import (
    COMMISSIONING_SCHEMA,
    CommissionedSite,
    LocationMetadata,
    OperatingConstraint,
)
from .equipment import (
    EQUIPMENT_CAPABILITY_COMPATIBILITY,
    ROBOT_CAPABILITY_COMPATIBILITY,
    AssetType,
    CapabilityDeclaration,
    CapabilityKind,
    ChargingRequirements,
    EquipmentAsset,
    RobotAsset,
    SafetyConstraint,
)
from .projections import (
    DIGITAL_TWIN_LAYOUT_SCHEMA,
    LegacySiteConfigContext,
    SITE_CONFIG_PROJECTION_SCHEMA,
    TELEMETRY_ADAPTER_CONFIG_SCHEMA,
    ProjectionError,
    canonical_projection_json,
    project_digital_twin_layout,
    project_legacy_site_config,
    project_site_config,
    project_telemetry_adapter_config,
)
from .provenance import MeasuredValue, Provenance, ProvenanceSource
from .sensors import (
    CalibrationInfo,
    CalibrationStatus,
    SensorBinding,
    SensorType,
)
from .spatial import (
    CoordinateReferenceSystem,
    CoordinateSystemKind,
    GeometryReference,
    GeometryType,
    SpatialPoint,
    SpatialReference,
    ZoneDefinition,
)
from .store import (
    CommissioningStore,
    DuplicateJSONKeyError,
    ManifestExistsError,
    dumps_manifest,
    loads_manifest,
)
from .validation import (
    CommissioningValidationError,
    ValidationIssue,
    collect_validation_issues,
    validate_commissioned_site,
)

__all__ = [
    "COMMISSIONING_SCHEMA",
    "DIGITAL_TWIN_LAYOUT_SCHEMA",
    "EQUIPMENT_CAPABILITY_COMPATIBILITY",
    "ROBOT_CAPABILITY_COMPATIBILITY",
    "SITE_CONFIG_PROJECTION_SCHEMA",
    "TELEMETRY_ADAPTER_CONFIG_SCHEMA",
    "AssetType",
    "CalibrationInfo",
    "CalibrationStatus",
    "CapabilityDeclaration",
    "CapabilityKind",
    "ChargingRequirements",
    "CommissionedSite",
    "CommissioningStore",
    "CommissioningValidationError",
    "CoordinateReferenceSystem",
    "CoordinateSystemKind",
    "DuplicateJSONKeyError",
    "EquipmentAsset",
    "GeometryReference",
    "GeometryType",
    "LocationMetadata",
    "LegacySiteConfigContext",
    "ManifestExistsError",
    "MeasuredValue",
    "OperatingConstraint",
    "ProjectionError",
    "Provenance",
    "ProvenanceSource",
    "RobotAsset",
    "SafetyConstraint",
    "SensorBinding",
    "SensorType",
    "SpatialPoint",
    "SpatialReference",
    "ValidationIssue",
    "ZoneDefinition",
    "canonical_projection_json",
    "collect_validation_issues",
    "dumps_manifest",
    "loads_manifest",
    "project_digital_twin_layout",
    "project_legacy_site_config",
    "project_site_config",
    "project_telemetry_adapter_config",
    "validate_commissioned_site",
]
