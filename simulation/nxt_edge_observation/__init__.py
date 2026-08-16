"""NXTektal Edge Observation Adapter Kit V0 -- raw device data to Observations.

This package converts *already-read* equipment and vendor-shaped samples
into the existing canonical ``nxt_telemetry.observations.Observation``
boundary.  It owns two fact classes and no others: the conversion from a raw
device payload into a trustworthy canonical channel value plus the
diagnostics explaining what could not be converted, and the
at-least-once delivery cursor over raw batches -- peek, acknowledge,
reject-with-sequence-reuse, and explicit exhaustion -- which is the
source side of the contract the Site Runtime's ``ObservationSource`` port
documents.  Assigning a delivery position is the source's job; validating
it, gating on quality, and checkpointing it are the Site Runtime's.

It owns none of:

* observation semantics or state assembly (``nxt_telemetry``);
* physical static facts, channels, units, or calibration identity
  (``nxt_commissioning``);
* publication sequencing validation, quality admission, checkpointing, or
  publication (the Site Runtime);
* evaluation lifecycle (the Agent Runtime);
* policy, trace, or workflow (``nxt_pilot_ops``);
* any facility state, advice, or execution surface.

V0 is transport-neutral and **fixture-backed only**.  There is no
Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2, Nav2, vendor SDK, camera,
cloud, or socket path here, no physical facility is connected, and no
robot, actuator, motion, charging, or emergency-stop surface exists or
may be added.  An observed ``estop_latched`` value is telemetry evidence
only and can neither set, clear, nor reset an emergency stop.

See ``simulation/docs/edge_observation_v0.md`` for the raw-to-canonical
coverage matrix, the unsupported channels, and the exact next seam for a
real transport.
"""

from .adapters import (
    DEVICE_STATUS_FAULT,
    DEVICE_STATUS_MISSING,
    DEVICE_STATUS_OK,
    ConversionResult,
    DigitalIOAdapter,
    EdgeObservationAdapterKit,
    LoadCellAdapter,
    RobotStatusAdapter,
)
from .bindings import (
    NOT_REQUIRED_CALIBRATION_ID,
    TELEMETRY_ADAPTER_CONFIG_SCHEMA,
    AdapterBinding,
    AdapterBindingSet,
)
from .channels import ChannelKind, channel_kind, robot_channel, robot_channel_parts
from .contracts import (
    ADAPTER_REPORT_SCHEMA,
    BATTERY_UNITS,
    CONFIDENCE_BY_STATUS,
    EDGE_ADAPTER_VERSION,
    MASS_UNIT_TO_KG,
    AcceptedField,
    DeviceProfile,
    DigitalDeviceProfile,
    DigitalInputProfile,
    DigitalInputSample,
    DigitalIOSnapshot,
    EdgeAdapterError,
    EdgeAdapterReport,
    LoadCellProfile,
    LoadCellSample,
    RawSampleBatch,
    RawSampleTiming,
    RejectedField,
    RejectionCode,
    RobotStatusProfile,
    RobotStatusSample,
    UnmappedField,
)
from .feed import (
    FeedExhausted,
    FeedProtocolError,
    FixtureRawSampleFeed,
    SequencedRawBatch,
)

__all__ = [
    "ADAPTER_REPORT_SCHEMA",
    "BATTERY_UNITS",
    "CONFIDENCE_BY_STATUS",
    "DEVICE_STATUS_FAULT",
    "DEVICE_STATUS_MISSING",
    "DEVICE_STATUS_OK",
    "EDGE_ADAPTER_VERSION",
    "MASS_UNIT_TO_KG",
    "NOT_REQUIRED_CALIBRATION_ID",
    "TELEMETRY_ADAPTER_CONFIG_SCHEMA",
    "AcceptedField",
    "AdapterBinding",
    "AdapterBindingSet",
    "ChannelKind",
    "ConversionResult",
    "DeviceProfile",
    "DigitalDeviceProfile",
    "DigitalIOAdapter",
    "DigitalIOSnapshot",
    "DigitalInputProfile",
    "DigitalInputSample",
    "EdgeAdapterError",
    "EdgeAdapterReport",
    "EdgeObservationAdapterKit",
    "FeedExhausted",
    "FeedProtocolError",
    "FixtureRawSampleFeed",
    "LoadCellAdapter",
    "LoadCellProfile",
    "LoadCellSample",
    "RawSampleBatch",
    "RawSampleTiming",
    "RejectedField",
    "RejectionCode",
    "RobotStatusAdapter",
    "RobotStatusProfile",
    "RobotStatusSample",
    "SequencedRawBatch",
    "UnmappedField",
    "channel_kind",
    "robot_channel",
    "robot_channel_parts",
]
