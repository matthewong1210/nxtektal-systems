"""Pilot Course A -- Edge Observation Intake fixture (composition root).

SIMULATED PILOT SCENARIO -- NOT LIVE CUSTOMER DATA.

Every value in this module is synthetic.  No physical facility, load
cell, washer, handoff cabinet, or robot is connected; no Modbus, serial,
MQTT, Kafka, OPC-UA, ROS 2, Nav2, vendor SDK, camera, socket, or cloud
call happens anywhere in this path; and nothing here can command a robot,
an actuator, or an emergency stop.

This is a **composition root**, deliberately outside
``nxt_edge_observation``.  The adapter package is pure conversion and must
not import ``nxt_site_runtime``; wrapping its output into the existing
``ObservationSource``/``SequencedObservationFrame`` contracts is this
module's job.

Coverage honesty: the V0 adapter kit produces the dispenser, washer,
station-buffer, handoff/zone digital, and robot-status channels.  Five
canonical channels have no V0 edge adapter family --
``scan.zone.<z>.balls``, ``station.<s>.queue_length``,
``charger.site.queue_length``, ``staff.site.busy`` and
``staff.site.queued`` -- so this fixture supplies them separately as
explicitly ``SIMULATION``-typed facility-system inputs.  They are listed
as unsupported in ``simulation/docs/edge_observation_v0.md`` and are
never presented as adapter output.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_commissioning import (  # noqa: E402
    CommissionedSite,
    LegacySiteConfigContext,
    Provenance,
    project_legacy_site_config,
    project_telemetry_adapter_config,
)
from nxt_edge_observation import (  # noqa: E402
    NOT_REQUIRED_CALIBRATION_ID,
    AdapterBindingSet,
    DigitalDeviceProfile,
    DigitalInputProfile,
    DigitalInputSample,
    DigitalIOSnapshot,
    EdgeAdapterReport,
    EdgeObservationAdapterKit,
    FeedExhausted,
    FixtureRawSampleFeed,
    LoadCellProfile,
    LoadCellSample,
    RawSampleBatch,
    RawSampleTiming,
    RobotStatusProfile,
    RobotStatusSample,
)
from nxt_telemetry.observations import (  # noqa: E402
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    SourceType,
    UpstreamInputs,
)

from nxt_agent_runtime import SourceExhausted  # noqa: E402
from nxt_site_runtime import SourceReference  # noqa: E402
from nxt_site_runtime.ports import SequencedObservationFrame  # noqa: E402

DISCLAIMER = "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA"

SITE_ID = "pilot-course-a"
DEPLOYMENT_ID = "pilot-a-edge-v0"
SCENARIO_NAME = "pilot-course-a-edge-observation"
COORDINATE_FRAME = "EPSG:32651"

ZONE_ID = "Z1"
STATION_ID = "ST1"
DISPENSER_ID = "DISP1"
WASHER_ID = "WASH1"
CHARGER_ID = "CHRG1"
ROBOT_IDS = ("R1", "R2")

TOTAL_BALLS = 8000
DISPENSER_CAPACITY_BALLS = 9000
WASHER_BATCH_BALLS = 400
STATION_BUFFER_BALLS = 600
ROBOT_PAYLOAD_BALLS = 600

# ---------------------------------------------------------------------------
# SYNTHETIC FIXTURE CONSTANTS -- not measured product facts.
#
# No commissioned contract in this repository carries load-cell conversion
# coefficients: ``CalibrationInfo`` records calibration identity and
# validity only.  These numbers exist purely so the fixture is arithmetically
# checkable, and they carry that statement as adapter-profile provenance.
# ---------------------------------------------------------------------------
SYNTHETIC_MASS_PER_BALL_KG = 0.046
SYNTHETIC_DISPENSER_TARE_KG = 12.5
SYNTHETIC_WASHER_TARE_KG = 5.0
SYNTHETIC_STATION_TARE_KG = 3.0
PROFILE_PROVENANCE = (
    "SYNTHETIC FIXTURE CONSTANT — declared for Pilot Course A only; "
    "not a measured product fact and not a commissioned value"
)

CALIBRATION_ID_LOAD_CELL = "CAL-LC-PILOTA-2026"
ACQUISITION_LAG_S = 5.0
LOAD_CELL_STALE_AFTER_S = 60.0
DIGITAL_STALE_AFTER_S = 60.0
ROBOT_STALE_AFTER_S = 30.0

DIGITAL_DEVICE_ID = "io-handoff-01"
SENSOR_DISPENSER_COUNT = "sensor-lc-dispenser-count"
SENSOR_DISPENSER_SENSED = "sensor-lc-dispenser-sensed"
SENSOR_WASHER_WIP = "sensor-lc-washer-wip"
SENSOR_STATION_BUFFER = "sensor-lc-station-buffer"
SENSOR_STATION_OPEN = "sensor-di-station-ready"
SENSOR_STATION_DOCKED = "sensor-di-handoff-docked"
SENSOR_ZONE_OPEN = "sensor-di-zone-gate"

# Digital points with no canonical destination anywhere in this repository.
RAW_ONLY_DIGITAL_INPUTS = (
    "washer_running",
    "washer_fault",
    "basket_present",
    "lift_upper_limit",
    "lift_lower_limit",
    "cabinet_door_closed",
)

# Channels no V0 edge adapter family can produce.  The fixture supplies
# them as clearly simulated facility-system inputs.
FACILITY_SYSTEM_CHANNELS = (
    f"scan.zone.{ZONE_ID}.balls",
    f"station.{STATION_ID}.queue_length",
    "charger.site.queue_length",
    "staff.site.busy",
    "staff.site.queued",
)
FACILITY_SYSTEM_CALIBRATION_ID = "cal:synthetic:pilot-a"

OPEN_MINUTE = 420    # 07:00
CLOSE_MINUTE = 1260  # 21:00
FORECAST_BUCKET_MINUTES = 30

CALM_T_S = 63000.0   # 17:30
SPIKE_T_S = 66600.0  # 18:30
LATE_T_S = 70200.0   # 19:30
CALM_FORECAST = (24.0, 26.0, 28.0, 30.0, 30.0, 30.0)
SPIKE_FORECAST = (34.0, 36.0, 36.0, 34.0, 32.0, 30.0)


# ---------------------------------------------------------------------------
# Commissioned manifest
# ---------------------------------------------------------------------------


def _provenance(
    source_type: str,
    source_id: str,
    *,
    captured_at: str = "2026-07-15T02:30:00Z",
    captured_by: str = "commissioning@nxt.example",
) -> dict:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "captured_at": captured_at,
        "captured_by": captured_by,
        "evidence_uri": f"urn:nxt:evidence:{source_id}",
        "notes": f"Synthetic Pilot Course A evidence for {source_id}",
    }


def _measured(name: str, value: float, unit: str, source: dict) -> dict:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "provenance": copy.deepcopy(source),
    }


def _point(x: float, y: float, z: float, source: dict) -> dict:
    return {"x": x, "y": y, "z": z, "provenance": copy.deepcopy(source)}


def commissioned_site_payload() -> dict:
    """A minimal, fully provenanced synthetic Pilot Course A manifest."""
    survey = _provenance("survey_data", "survey-pilot-a-2026-07")
    supplier = _provenance(
        "manufacturer_specification",
        "pilot-a-equipment-datasheets",
        captured_at="2026-06-01T00:00:00Z",
        captured_by="supplier-data@nxt.example",
    )
    manual = _provenance(
        "manual_measurement",
        "pilot-a-field-sheet-001",
        captured_at="2026-07-16T03:00:00Z",
    )
    operator = _provenance(
        "operator_input",
        "pilot-a-work-order-001",
        captured_at="2026-08-08T09:00:00+08:00",
        captured_by="site-operator@nxt.example",
    )
    calibration = _provenance(
        "sensor_calibration",
        "pilot-a-calibration-batch-2026-07",
        captured_at="2026-07-20T01:00:00Z",
        captured_by="calibration-lab@nxt.example",
    )

    origin = (346000.0, 3456000.0, 5.0)
    zone_points = [
        _point(origin[0] + 10, origin[1] + 10, origin[2], survey),
        _point(origin[0] + 40, origin[1] + 10, origin[2], survey),
        _point(origin[0] + 40, origin[1] + 35, origin[2], survey),
        _point(origin[0] + 10, origin[1] + 35, origin[2], survey),
    ]

    calibrated_load_cell = {
        "status": "calibrated",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "calibrated_at": "2026-07-20T01:00:00Z",
        "valid_until": "2027-07-20T01:00:00Z",
        "method": "traceable reference load",
        "provenance": copy.deepcopy(calibration),
    }
    uncalibrated = {
        "status": "not_required",
        "calibration_id": None,
        "calibrated_at": None,
        "valid_until": None,
        "method": "commissioned discrete input; no calibration required",
        "provenance": copy.deepcopy(manual),
    }

    def load_cell_binding(sensor_id: str, channel: str, asset_id: str) -> dict:
        return {
            "sensor_id": sensor_id,
            "sensor_type": "load_cell",
            "source": f"edge.{DIGITAL_DEVICE_ID}",
            "channel": channel,
            "associated_asset_id": asset_id,
            "units": "balls",
            "calibration": copy.deepcopy(calibrated_load_cell),
            "provenance": copy.deepcopy(calibration),
        }

    def digital_binding(
        sensor_id: str, channel: str, asset_id: str, units: str
    ) -> dict:
        return {
            "sensor_id": sensor_id,
            "sensor_type": "proximity_switch",
            "source": f"edge.{DIGITAL_DEVICE_ID}",
            "channel": channel,
            "associated_asset_id": asset_id,
            "units": units,
            "calibration": copy.deepcopy(uncalibrated),
            "provenance": copy.deepcopy(manual),
        }

    def robot_binding(robot_id: str, field_name: str, units: str) -> dict:
        return {
            "sensor_id": f"sensor-fleet-{robot_id}-{field_name}",
            "sensor_type": "external_system",
            "source": f"fleet.{robot_id}",
            "channel": f"robot.{robot_id}.{field_name}",
            "associated_asset_id": robot_id,
            "units": units,
            "calibration": copy.deepcopy(uncalibrated),
            "provenance": copy.deepcopy(operator),
        }

    robot_bindings: list[dict] = []
    for robot_id in ROBOT_IDS:
        robot_bindings.append(robot_binding(robot_id, "payload_balls", "balls"))
        for field_name in (
            "activity",
            "health",
            "battery_frac",
            "location",
            "destination",
            "assigned_zone",
            "estop_latched",
            "awaiting_human",
        ):
            robot_bindings.append(robot_binding(robot_id, field_name, "1"))

    return {
        "schema": "nxt-commissioning/manifest/v0",
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "facility_name": "NXT Pilot Course A",
        "timezone": "Asia/Shanghai",
        "location_metadata": {
            "address_lines": ["1 Pilot Fairway"],
            "locality": "Shanghai",
            "region": "Shanghai",
            "postal_code": "201210",
            "country_code": "CN",
            "site_reference": "pilot-course-a",
            "provenance": copy.deepcopy(operator),
        },
        "operating_constraints": [
            {
                "constraint_id": "pilot-a-robot-speed-limit",
                "category": "safety",
                "description": "Maximum autonomous robot speed on site.",
                "applies_to": ["site"],
                "parameters": [_measured("max_speed", 1.5, "m/s", manual)],
                "safety_relevant": True,
                "provenance": copy.deepcopy(operator),
            }
        ],
        "spatial_reference": {
            "coordinate_system": {
                "kind": "epsg",
                "identifier": COORDINATE_FRAME,
                "horizontal_unit": "m",
                "vertical_unit": "m",
                "axes": ["east", "north", "up"],
                "provenance": copy.deepcopy(survey),
            },
            "facility_origin": _point(*origin, survey),
            "geometry_references": [
                {
                    "geometry_id": "geom-zone-z1",
                    "geometry_type": "polygon",
                    "points": zone_points,
                    "source_uri": "urn:nxt:geometry:pilot-a-zone-z1",
                    "provenance": copy.deepcopy(survey),
                },
                *(
                    {
                        "geometry_id": f"geom-{asset_id.lower()}",
                        "geometry_type": "point",
                        "points": [
                            _point(
                                origin[0] + offset,
                                origin[1] + 3,
                                origin[2],
                                survey,
                            )
                        ],
                        "source_uri": f"urn:nxt:geometry:pilot-a-{asset_id.lower()}",
                        "provenance": copy.deepcopy(survey),
                    }
                    for asset_id, offset in (
                        (DISPENSER_ID, 2.0),
                        (WASHER_ID, 5.0),
                        (CHARGER_ID, 8.0),
                        (STATION_ID, 11.0),
                    )
                ),
            ],
            "zone_definitions": [
                {
                    "zone_id": ZONE_ID,
                    "name": "Pilot Course A driving zone",
                    "zone_type": "collection",
                    "geometry_reference": "geom-zone-z1",
                    "safety_classification": "robot-operating-area",
                    "provenance": copy.deepcopy(survey),
                }
            ],
        },
        "equipment_assets": [
            {
                "asset_id": DISPENSER_ID,
                "asset_type": "dispenser",
                "manufacturer": "NXT",
                "model": "Pilot-D1",
                "capabilities": [
                    {
                        "capability": "ball_dispensing",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "inventory_measurement",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                ],
                "capacity": [
                    _measured(
                        "hopper_capacity",
                        DISPENSER_CAPACITY_BALLS,
                        "balls",
                        supplier,
                    )
                ],
                "location_reference": f"geom-{DISPENSER_ID.lower()}",
                "provenance": copy.deepcopy(supplier),
            },
            {
                "asset_id": WASHER_ID,
                "asset_type": "washer",
                "manufacturer": "AquaWash",
                "model": "W400",
                "capabilities": [
                    {
                        "capability": "ball_washing",
                        "parameters": [
                            _measured(
                                "rated_throughput", 40, "balls/min", supplier
                            )
                        ],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "inventory_measurement",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                ],
                "capacity": [
                    _measured(
                        "batch_capacity", WASHER_BATCH_BALLS, "balls", supplier
                    )
                ],
                "location_reference": f"geom-{WASHER_ID.lower()}",
                "provenance": copy.deepcopy(supplier),
            },
            {
                "asset_id": CHARGER_ID,
                "asset_type": "charging_station",
                "manufacturer": None,
                "model": None,
                "capabilities": [
                    {
                        "capability": "robot_charging",
                        "parameters": [
                            _measured("nominal_voltage", 48, "V", manual)
                        ],
                        "provenance": copy.deepcopy(manual),
                    }
                ],
                "capacity": [_measured("slot_count", 2, "count", manual)],
                "location_reference": f"geom-{CHARGER_ID.lower()}",
                "provenance": copy.deepcopy(manual),
            },
            {
                "asset_id": STATION_ID,
                "asset_type": "collection_station",
                "manufacturer": "NXT",
                "model": "Universal-Handoff-1",
                "capabilities": [
                    {
                        "capability": "storage",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "docking",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "inventory_measurement",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                ],
                "capacity": [
                    _measured(
                        "buffer_capacity", STATION_BUFFER_BALLS, "balls", supplier
                    )
                ],
                "location_reference": f"geom-{STATION_ID.lower()}",
                "provenance": copy.deepcopy(supplier),
            },
        ],
        "robot_assets": [
            {
                "robot_id": robot_id,
                "capabilities": [
                    {
                        "capability": "material_transport",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "autonomous_navigation",
                        "parameters": [
                            _measured("max_speed", 1.5, "m/s", supplier)
                        ],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "docking",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                    {
                        "capability": "emergency_stop",
                        "parameters": [],
                        "provenance": copy.deepcopy(supplier),
                    },
                ],
                "payload_limits": [
                    _measured(
                        "max_payload", ROBOT_PAYLOAD_BALLS, "balls", supplier
                    )
                ],
                "operating_zones": [ZONE_ID],
                "charging_requirements": {
                    "charging_station_ids": [CHARGER_ID],
                    "connector_type": "nxt-dock-v1",
                    "electrical_requirements": [
                        _measured("nominal_voltage", 48, "V", supplier),
                        _measured("max_current", 20, "A", supplier),
                    ],
                    "provenance": copy.deepcopy(supplier),
                },
                "safety_constraints": [
                    {
                        "constraint_id": f"{robot_id}-max-slope",
                        "description": "Maximum permitted ground slope.",
                        "limit": _measured("max_slope", 5, "deg", supplier),
                        "provenance": copy.deepcopy(supplier),
                    }
                ],
                "provenance": copy.deepcopy(supplier),
            }
            for robot_id in ROBOT_IDS
        ],
        "sensor_bindings": [
            load_cell_binding(
                SENSOR_DISPENSER_COUNT, "inventory.dispenser.count", DISPENSER_ID
            ),
            load_cell_binding(
                SENSOR_DISPENSER_SENSED, "inventory.dispenser.sensed", DISPENSER_ID
            ),
            load_cell_binding(SENSOR_WASHER_WIP, "wash.washer.wip", WASHER_ID),
            load_cell_binding(
                SENSOR_STATION_BUFFER,
                f"inventory.station.{STATION_ID}.buffer_balls",
                STATION_ID,
            ),
            digital_binding(
                SENSOR_STATION_OPEN,
                f"station.{STATION_ID}.is_open",
                STATION_ID,
                "1",
            ),
            digital_binding(
                SENSOR_STATION_DOCKED,
                f"station.{STATION_ID}.docked",
                STATION_ID,
                "count",
            ),
            digital_binding(
                SENSOR_ZONE_OPEN, f"zone.{ZONE_ID}.is_open", SITE_ID, "1"
            ),
            *robot_bindings,
        ],
        "provenance": _provenance(
            "imported_record",
            "pilot-a-commissioning-manifest-001",
            captured_at="2026-08-08T10:00:00+08:00",
        ),
    }


def commissioned_site() -> CommissionedSite:
    """Build and validate the synthetic Pilot Course A manifest."""
    return CommissionedSite.from_dict(commissioned_site_payload())


def site_config(site: CommissionedSite) -> SiteConfig:
    """Project the commissioned manifest into the existing SiteConfig."""
    context = LegacySiteConfigContext(
        scenario_name=SCENARIO_NAME,
        seed=0,
        total_balls=TOTAL_BALLS,
        staff_capacity=3,
        wet_ground_speed_multiplier=1.0,
        open_minute=OPEN_MINUTE,
        close_minute=CLOSE_MINUTE,
        forecast_bucket_minutes=FORECAST_BUCKET_MINUTES,
        provenance=Provenance.from_dict(
            _provenance(
                "operator_input",
                "pilot-a-consumer-context-001",
                captured_at="2026-08-08T09:30:00+08:00",
                captured_by="site-operator@nxt.example",
            )
        ),
    )
    return SiteConfig(**project_legacy_site_config(site, context))


# ---------------------------------------------------------------------------
# Adapter-local profiles
# ---------------------------------------------------------------------------


def adapter_kit(site: CommissionedSite) -> EdgeObservationAdapterKit:
    """Compose the V0 kit over the commissioned telemetry-adapter projection."""
    bindings = AdapterBindingSet.from_projection(
        project_telemetry_adapter_config(site)
    )
    load_cells = tuple(
        LoadCellProfile(
            sensor_id=sensor_id,
            calibration_id=CALIBRATION_ID_LOAD_CELL,
            stale_after_s=LOAD_CELL_STALE_AFTER_S,
            provenance=PROFILE_PROVENANCE,
            raw_unit="kg",
            tare_raw=tare,
            mass_per_ball_raw=SYNTHETIC_MASS_PER_BALL_KG,
            capacity_balls=capacity,
        )
        for sensor_id, tare, capacity in (
            (
                SENSOR_DISPENSER_COUNT,
                SYNTHETIC_DISPENSER_TARE_KG,
                DISPENSER_CAPACITY_BALLS,
            ),
            (
                SENSOR_DISPENSER_SENSED,
                SYNTHETIC_DISPENSER_TARE_KG,
                DISPENSER_CAPACITY_BALLS,
            ),
            (SENSOR_WASHER_WIP, SYNTHETIC_WASHER_TARE_KG, WASHER_BATCH_BALLS),
            (
                SENSOR_STATION_BUFFER,
                SYNTHETIC_STATION_TARE_KG,
                STATION_BUFFER_BALLS,
            ),
        )
    )
    digital_device = DigitalDeviceProfile(
        device_id=DIGITAL_DEVICE_ID,
        provenance=PROFILE_PROVENANCE,
        raw_only_inputs=RAW_ONLY_DIGITAL_INPUTS,
        mutually_exclusive_inputs=(("lift_upper_limit", "lift_lower_limit"),),
    )
    digital_inputs = tuple(
        DigitalInputProfile(
            sensor_id=sensor_id,
            calibration_id=NOT_REQUIRED_CALIBRATION_ID,
            stale_after_s=DIGITAL_STALE_AFTER_S,
            provenance=PROFILE_PROVENANCE,
            active_low=active_low,
        )
        for sensor_id, active_low in (
            (SENSOR_STATION_OPEN, False),
            # The handoff dock proximity switch is wired normally closed.
            (SENSOR_STATION_DOCKED, True),
            (SENSOR_ZONE_OPEN, False),
        )
    )
    robots = tuple(
        RobotStatusProfile(
            sensor_id=f"fleet-{robot_id}",
            calibration_id=NOT_REQUIRED_CALIBRATION_ID,
            stale_after_s=ROBOT_STALE_AFTER_S,
            provenance=PROFILE_PROVENANCE,
            robot_id=robot_id,
            battery_unit="percent",
            allowed_activities=(
                "idle",
                "traveling",
                "collecting",
                "queued_handoff",
                "unloading",
                "queued_charger",
                "charging",
                "paused",
                "failed",
                "emergency_stopped",
                "awaiting_human",
            ),
            allowed_health=("ok", "degraded", "failed"),
            allowed_locations=(
                "charger",
                "dispenser",
                f"zone:{ZONE_ID}",
                f"station:{STATION_ID}",
            ),
            allowed_zones=(ZONE_ID,),
        )
        for robot_id in ROBOT_IDS
    )
    return EdgeObservationAdapterKit(
        bindings=bindings,
        coordinate_frame=COORDINATE_FRAME,
        load_cell_profiles=load_cells,
        digital_device_profiles=(digital_device,),
        digital_input_profiles=digital_inputs,
        robot_profiles=robots,
    )


# ---------------------------------------------------------------------------
# Raw sample batches
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CycleSpec:
    """One synthetic evening cycle of Pilot Course A."""

    cycle_index: int
    t_s: float
    label: str
    dispenser_balls: int
    washer_balls: int
    station_buffer_balls: int
    zone_balls: int
    robot_payload_balls: tuple[int, ...]
    forecast: tuple[float, ...]
    demand_balls_total: int
    demand_balls_served: int
    variant: str = "nominal"

    def conserves(self) -> bool:
        return (
            self.dispenser_balls
            + self.washer_balls
            + self.station_buffer_balls
            + self.zone_balls
            + sum(self.robot_payload_balls)
        ) == TOTAL_BALLS


def _mass_kg(balls: int, tare_kg: float) -> float:
    return tare_kg + balls * SYNTHETIC_MASS_PER_BALL_KG


def _timing(t_s: float, *, age_s: float = ACQUISITION_LAG_S) -> RawSampleTiming:
    return RawSampleTiming(
        sample_timestamp_s=t_s - age_s, available_timestamp_s=t_s
    )


def raw_batch(spec: CycleSpec) -> RawSampleBatch:
    """Build one already-read raw device batch for a cycle specification."""
    if not spec.conserves():
        raise ValueError(
            f"cycle {spec.cycle_index} does not conserve {TOTAL_BALLS} balls"
        )
    uncalibrated_dispenser = spec.variant == "uncalibrated_dispenser"
    stale_robot = spec.variant == "stale_robot"

    load_cells = (
        LoadCellSample(
            sensor_id=SENSOR_DISPENSER_COUNT,
            timing=_timing(spec.t_s),
            raw_value=_mass_kg(spec.dispenser_balls, SYNTHETIC_DISPENSER_TARE_KG),
            raw_unit="kg",
            device_status="ok",
            calibration_id=(
                None if uncalibrated_dispenser else CALIBRATION_ID_LOAD_CELL
            ),
            diagnostic_code=None,
        ),
        LoadCellSample(
            sensor_id=SENSOR_DISPENSER_SENSED,
            timing=_timing(spec.t_s),
            raw_value=_mass_kg(spec.dispenser_balls, SYNTHETIC_DISPENSER_TARE_KG),
            raw_unit="kg",
            device_status="ok",
            calibration_id=CALIBRATION_ID_LOAD_CELL,
            diagnostic_code=None,
        ),
        LoadCellSample(
            sensor_id=SENSOR_WASHER_WIP,
            timing=_timing(spec.t_s),
            raw_value=_mass_kg(spec.washer_balls, SYNTHETIC_WASHER_TARE_KG),
            raw_unit="kg",
            device_status="ok",
            calibration_id=CALIBRATION_ID_LOAD_CELL,
            diagnostic_code=None,
        ),
        LoadCellSample(
            sensor_id=SENSOR_STATION_BUFFER,
            timing=_timing(spec.t_s),
            raw_value=_mass_kg(
                spec.station_buffer_balls, SYNTHETIC_STATION_TARE_KG
            ),
            raw_unit="kg",
            device_status="ok",
            calibration_id=CALIBRATION_ID_LOAD_CELL,
            diagnostic_code=None,
        ),
    )

    digital = DigitalIOSnapshot(
        device_id=DIGITAL_DEVICE_ID,
        timing=_timing(spec.t_s),
        device_status="ok",
        inputs=(
            DigitalInputSample(
                sensor_id=SENSOR_STATION_OPEN,
                input_name="equipment_ready",
                raw_state=True,
            ),
            # Wired normally closed: an occupied dock reads False.
            DigitalInputSample(
                sensor_id=SENSOR_STATION_DOCKED,
                input_name="handoff_docked",
                raw_state=True,
            ),
            DigitalInputSample(
                sensor_id=SENSOR_ZONE_OPEN,
                input_name="zone_gate_open",
                raw_state=True,
            ),
            # Declared raw-only points: honest coverage gaps, never dropped.
            DigitalInputSample(
                sensor_id="di-washer-run",
                input_name="washer_running",
                raw_state=True,
            ),
            DigitalInputSample(
                sensor_id="di-washer-fault",
                input_name="washer_fault",
                raw_state=False,
            ),
            DigitalInputSample(
                sensor_id="di-basket",
                input_name="basket_present",
                raw_state=True,
            ),
            DigitalInputSample(
                sensor_id="di-lift-upper",
                input_name="lift_upper_limit",
                raw_state=False,
            ),
            DigitalInputSample(
                sensor_id="di-lift-lower",
                input_name="lift_lower_limit",
                raw_state=True,
            ),
            DigitalInputSample(
                sensor_id="di-cabinet",
                input_name="cabinet_door_closed",
                raw_state=True,
            ),
        ),
    )

    robots = tuple(
        RobotStatusSample(
            robot_id=robot_id,
            timing=_timing(
                spec.t_s,
                age_s=(
                    900.0
                    if stale_robot and robot_id == ROBOT_IDS[1]
                    else ACQUISITION_LAG_S
                ),
            ),
            device_status="ok",
            battery=82.0 if index == 0 else 76.0,
            payload_balls=spec.robot_payload_balls[index],
            activity="idle",
            health="ok",
            location="charger",
            destination="",
            assigned_zone="",
            estop_latched=False,
            awaiting_human=False,
            fault_code=None,
            position_x_m=12.0 + index,
            position_y_m=4.0,
            coordinate_frame=COORDINATE_FRAME,
        )
        for index, robot_id in enumerate(ROBOT_IDS)
    )

    return RawSampleBatch(
        cycle_index=spec.cycle_index,
        frame_t_s=spec.t_s,
        load_cells=load_cells,
        digital_io=(digital,),
        robots=robots,
    )


PILOT_CYCLES: tuple[CycleSpec, ...] = (
    CycleSpec(
        cycle_index=0,
        t_s=CALM_T_S,
        label="17:30 calibrated dispenser and equipment state",
        dispenser_balls=6000,
        washer_balls=200,
        station_buffer_balls=300,
        zone_balls=1300,
        robot_payload_balls=(100, 100),
        forecast=CALM_FORECAST,
        demand_balls_total=3600,
        demand_balls_served=3600,
    ),
    CycleSpec(
        cycle_index=1,
        t_s=SPIKE_T_S,
        label="18:30 evening spike has drained the dispenser",
        dispenser_balls=2400,
        washer_balls=200,
        station_buffer_balls=300,
        zone_balls=4900,
        robot_payload_balls=(100, 100),
        forecast=SPIKE_FORECAST,
        demand_balls_total=7200,
        demand_balls_served=7200,
    ),
    CycleSpec(
        cycle_index=2,
        t_s=LATE_T_S,
        label="19:30 stale robot heartbeat",
        dispenser_balls=2400,
        washer_balls=200,
        station_buffer_balls=300,
        zone_balls=4900,
        robot_payload_balls=(100, 100),
        forecast=SPIKE_FORECAST,
        demand_balls_total=7200,
        demand_balls_served=7200,
        variant="stale_robot",
    ),
    CycleSpec(
        cycle_index=3,
        t_s=LATE_T_S,
        label="19:30 uncalibrated dispenser reading",
        dispenser_balls=2400,
        washer_balls=200,
        station_buffer_balls=300,
        zone_balls=4900,
        robot_payload_balls=(100, 100),
        forecast=SPIKE_FORECAST,
        demand_balls_total=7200,
        demand_balls_served=7200,
        variant="uncalibrated_dispenser",
    ),
    CycleSpec(
        cycle_index=4,
        t_s=LATE_T_S,
        label="19:30 corrected redelivery",
        dispenser_balls=2400,
        washer_balls=200,
        station_buffer_balls=300,
        zone_balls=4900,
        robot_payload_balls=(100, 100),
        forecast=SPIKE_FORECAST,
        demand_balls_total=7200,
        demand_balls_served=7200,
    ),
)


# ---------------------------------------------------------------------------
# Facility-system inputs with no V0 adapter family
# ---------------------------------------------------------------------------


def facility_system_observations(spec: CycleSpec) -> tuple[Observation, ...]:
    """Emit the five canonical channels the V0 adapter kit cannot produce.

    These are explicitly ``SIMULATION``-sourced synthetic stand-ins for a
    scanning rig and facility/POS systems.  They never claim to be sensor
    readings and never pass through the adapter kit.
    """
    values: dict[str, tuple[object, str]] = {
        f"scan.zone.{ZONE_ID}.balls": (spec.zone_balls, "synthetic.scanrig"),
        f"station.{STATION_ID}.queue_length": (0, "synthetic.facility_system"),
        "charger.site.queue_length": (0, "synthetic.facility_system"),
        "staff.site.busy": (1, "synthetic.facility_system"),
        "staff.site.queued": (0, "synthetic.facility_system"),
    }
    return tuple(
        Observation(
            channel=channel,
            value=value,
            sample_timestamp_s=spec.t_s - ACQUISITION_LAG_S,
            available_timestamp_s=spec.t_s,
            status=ObservationStatus.OK,
            source_type=SourceType.SIMULATION,
            source_id=source_id,
            calibration_id=FACILITY_SYSTEM_CALIBRATION_ID,
            confidence=1.0,
            seq=spec.cycle_index,
        )
        for channel, (value, source_id) in sorted(values.items())
    )


def upstream_inputs(spec: CycleSpec) -> UpstreamInputs:
    return UpstreamInputs(
        forecast_balls_per_minute=spec.forecast,
        demand_balls_total=spec.demand_balls_total,
        demand_balls_served=spec.demand_balls_served,
        stockout_minutes=0.0,
        service_availability=1.0,
    )


def upstream_reference(spec: CycleSpec) -> SourceReference:
    return SourceReference(
        source_type="simulation",
        source_id="synthetic.upstream",
        channel="upstream.site",
        reference_id=f"upstream.site:o{spec.cycle_index:06d}",
        sample_timestamp_s=spec.t_s - ACQUISITION_LAG_S,
        available_timestamp_s=spec.t_s,
        confidence=1.0,
        status="ok",
        calibration_id=FACILITY_SYSTEM_CALIBRATION_ID,
    )


# ---------------------------------------------------------------------------
# ObservationSource composition
# ---------------------------------------------------------------------------


@dataclass
class EdgeObservationSource:
    """Adapt the fixture raw feed into the existing ``ObservationSource``.

    ``observe`` peeks: the identical frame is redelivered until the Site
    Runtime acknowledges or terminally rejects it.  The Agent Runtime and
    the pipeline both call ``observe`` within one cycle, so the method is
    deliberately free of side effects beyond recording diagnostics.
    """

    feed: FixtureRawSampleFeed
    kit: EdgeObservationAdapterKit
    specs: tuple[CycleSpec, ...]
    reports: dict[int, EdgeAdapterReport] = field(default_factory=dict)
    # Ordered record of what each delivery actually carried.  The demo
    # pairs outcomes with this rather than with a positional index,
    # because a retained frame is re-observed and one cycle index can
    # therefore span several runtime outcomes.
    delivery_log: list[tuple[int, int]] = field(default_factory=list)

    def _spec_for(self, cycle_index: int) -> CycleSpec:
        for spec in self.specs:
            if spec.cycle_index == cycle_index:
                return spec
        raise KeyError(f"no cycle specification for index {cycle_index}")

    def observe(self) -> SequencedObservationFrame:
        try:
            sequenced = self.feed.peek()
        except FeedExhausted as exc:
            raise SourceExhausted(str(exc)) from exc
        batch = sequenced.batch
        spec = self._spec_for(batch.cycle_index)
        result = self.kit.convert(batch)
        self.reports[batch.cycle_index] = result.report
        delivery = (sequenced.sequence_number, batch.cycle_index)
        # observe() is a peek and the runtime calls it twice per cycle, so
        # only a genuinely new delivery is logged.
        if not self.delivery_log or self.delivery_log[-1] != delivery:
            self.delivery_log.append(delivery)
        frame = ObservationFrame(
            t_s=batch.frame_t_s,
            observations=result.observations + facility_system_observations(spec),
        )
        return SequencedObservationFrame(
            sequence_number=sequenced.sequence_number,
            frame=frame,
            upstream=upstream_inputs(spec),
            upstream_source_references=(upstream_reference(spec),),
        )

    def acknowledge(self, sequence_number: int) -> None:
        self.feed.acknowledge(sequence_number)

    def reject(self, sequence_number: int, reason: str) -> None:
        self.feed.reject(sequence_number, reason)


def pilot_observation_source(
    site: CommissionedSite,
    *,
    specs: tuple[CycleSpec, ...] = PILOT_CYCLES,
    consumed_cycles: int = 0,
    first_sequence_number: int = 0,
) -> EdgeObservationSource:
    """Compose the whole synthetic edge path for Pilot Course A.

    The two resume parameters exist because this feed is in-memory and a
    restarted runtime must not replay from the beginning: Site Runtime
    treats ``invalid_sequence`` as a retained, non-discardable incident
    (``nxt_site_runtime.pipeline._DISCARDABLE_INPUT_FAILURES``), so a
    source that restarts at sequence 0 after publishing sequence 1 can
    never make progress again.

    They are deliberately explicit rather than derived from the
    checkpoint's ``last_successful_sequence``, because a rejected frame
    consumes a cycle without advancing the sequence -- the two counts are
    genuinely independent and guessing one from the other would be wrong
    exactly when recovery matters:

    * ``consumed_cycles`` -- how many declared cycles the previous run
      already delivered, whether they were acknowledged or rejected;
    * ``first_sequence_number`` -- the next publication position, i.e. the
      checkpoint's ``last_successful_sequence + 1`` (or ``0`` when nothing
      was published).

    A real transport must offer an equivalent resume hook. An
    at-least-once reader that cannot resume is not restart-safe, however
    correct its peek/acknowledge/reject semantics are within one process.
    """
    for name, value in (
        ("consumed_cycles", consumed_cycles),
        ("first_sequence_number", first_sequence_number),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if consumed_cycles > len(specs):
        raise ValueError(
            f"cannot resume after {consumed_cycles} cycles: the fixture "
            f"declares only {len(specs)}"
        )
    return EdgeObservationSource(
        feed=FixtureRawSampleFeed(
            tuple(raw_batch(spec) for spec in specs[consumed_cycles:]),
            first_sequence_number=first_sequence_number,
        ),
        kit=adapter_kit(site),
        specs=specs,
    )


__all__ = [
    "COORDINATE_FRAME",
    "DEPLOYMENT_ID",
    "DISCLAIMER",
    "FACILITY_SYSTEM_CHANNELS",
    "PILOT_CYCLES",
    "RAW_ONLY_DIGITAL_INPUTS",
    "ROBOT_IDS",
    "SITE_ID",
    "STATION_ID",
    "TOTAL_BALLS",
    "ZONE_ID",
    "CycleSpec",
    "EdgeObservationSource",
    "adapter_kit",
    "commissioned_site",
    "commissioned_site_payload",
    "facility_system_observations",
    "pilot_observation_source",
    "raw_batch",
    "site_config",
    "upstream_inputs",
    "upstream_reference",
]
