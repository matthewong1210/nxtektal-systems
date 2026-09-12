"""NXTektal Edge Task Exchange V0 -- simulated site-level task protocol.

SIMULATION ONLY.  This package owns the versioned wire contracts and the
pure, deterministic lifecycle rules of the Edge <-> robot *task exchange
rehearsal*: task requests issued by a SIMULATION-labelled test entry,
robot status heartbeats, task events, the Edge-side task/device journal
derivation (dedup, ordering, terminal absorption, terminal-conflict
gate, session ordering, liveness), and the protocol double's executor
rules (persist-before-publish, restart branches, history replay).

It owns none of:

* facility state, observations, or observation assembly (the facility,
  telemetry, and edge-observation packages keep those);
* commissioned static facts (commissioning keeps them; composition roots
  hand this package plain admission data);
* recommendations, decision traces, human workflow, or the ledger (the
  facility decision rules and Shadow Ops keep those);
* transport, clocks, processes, or files other than its own journal
  (composition roots under ``simulation/scripts`` own MQTT, wall clock,
  and process lifecycle);
* any robot, actuator, navigation, charging, ROS, or emergency-stop
  surface.  A task request is a simulated message to a protocol double;
  nothing here can move, stop, reset, or command a physical device.

The package is stdlib-only, imports no other ``nxt_*`` package, and uses
no wall clock, UUID, or randomness: every timestamp is supplied by the
caller and every identifier is derived from content.
"""

from .contracts import (
    CONFIG_SCHEMA,
    ENVIRONMENT_KIND_SIMULATION,
    EVENT_SCHEMA,
    REQUEST_SCHEMA,
    STATUS_SCHEMA,
    TASK_TYPE_COLLECT_BALLS_ZONE,
    Availability,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    EventKind,
    ReasonClass,
    RobotStatusMessage,
    TaskEvent,
    TaskRequest,
    canonical_json,
    derive_task_id,
    event_topic,
    parse_utc,
    reason_class,
    request_topic,
    stable_digest,
    status_topic,
    utc_text,
)
from .journal import JournalIntegrityError, JournalRecord, JsonlJournal, PreconditionFailed

__all__ = [
    "CONFIG_SCHEMA",
    "ENVIRONMENT_KIND_SIMULATION",
    "EVENT_SCHEMA",
    "REQUEST_SCHEMA",
    "STATUS_SCHEMA",
    "TASK_TYPE_COLLECT_BALLS_ZONE",
    "Availability",
    "EdgeTaskConfig",
    "EdgeTaskError",
    "ErrorCode",
    "EventKind",
    "JournalIntegrityError",
    "JournalRecord",
    "JsonlJournal",
    "PreconditionFailed",
    "ReasonClass",
    "RobotStatusMessage",
    "TaskEvent",
    "TaskRequest",
    "canonical_json",
    "derive_task_id",
    "event_topic",
    "parse_utc",
    "reason_class",
    "request_topic",
    "stable_digest",
    "status_topic",
    "utc_text",
]
