"""How a canonical channel's value must be shaped, and nothing else.

**This is not a channel registry.**  The closed canonical channel
vocabulary -- which channels exist, their canonical unit, which sensor
types may serve them, and which asset each one binds to -- is owned by
``nxt_commissioning.validation`` and is already enforced when a
``CommissionedSite`` is constructed.  This module answers only the one
question the commissioning contract does not: *what Python value kind
must the canonical value be* so that
``nxt_telemetry.assemble.assemble_from_observations`` reads it correctly.

The mapping is derived from the assembler's own accessors:
``_ChannelView.count`` (integers), ``_ChannelView.frac`` (fractions),
``bool(...)`` coercions (booleans), and the string constructors for
robot activity/health/location.  ``tests/edge_observation/test_channels.py``
proves this table stays in step with the commissioning vocabulary; if
that parity test fails, commissioning is right and this table is wrong.

Stdlib plus the canonical Observation contract only.
"""

from __future__ import annotations

import re
from enum import Enum

from .contracts import EdgeAdapterError


class ChannelKind(str, Enum):
    """The value shape the assembler expects for a canonical channel."""

    COUNT = "count"          # non-negative integer
    QUANTITY = "quantity"    # non-negative real (sensed, not accounted)
    FRACTION = "fraction"    # real in [0, 1]
    BOOLEAN = "boolean"      # real bool, never a truthy coercion
    LABEL = "label"          # bare string, possibly empty


_EXACT_KINDS: dict[str, ChannelKind] = {
    "inventory.dispenser.count": ChannelKind.COUNT,
    "inventory.dispenser.sensed": ChannelKind.QUANTITY,
    "wash.washer.wip": ChannelKind.COUNT,
    "charger.site.queue_length": ChannelKind.COUNT,
    "staff.site.busy": ChannelKind.COUNT,
    "staff.site.queued": ChannelKind.COUNT,
}

_PATTERN_KINDS: tuple[tuple[re.Pattern[str], ChannelKind], ...] = (
    (re.compile(r"inventory\.station\.(.+)\.buffer_balls"), ChannelKind.COUNT),
    (re.compile(r"scan\.zone\.(.+)\.balls"), ChannelKind.COUNT),
    (re.compile(r"zone\.(.+)\.is_open"), ChannelKind.BOOLEAN),
    (re.compile(r"station\.(.+)\.is_open"), ChannelKind.BOOLEAN),
    (re.compile(r"station\.(.+)\.docked"), ChannelKind.COUNT),
    (re.compile(r"station\.(.+)\.queue_length"), ChannelKind.COUNT),
    (re.compile(r"robot\.(.+)\.battery_frac"), ChannelKind.FRACTION),
    (re.compile(r"robot\.(.+)\.payload_balls"), ChannelKind.COUNT),
    (re.compile(r"robot\.(.+)\.estop_latched"), ChannelKind.BOOLEAN),
    (re.compile(r"robot\.(.+)\.awaiting_human"), ChannelKind.BOOLEAN),
    (re.compile(r"robot\.(.+)\.activity"), ChannelKind.LABEL),
    (re.compile(r"robot\.(.+)\.health"), ChannelKind.LABEL),
    (re.compile(r"robot\.(.+)\.location"), ChannelKind.LABEL),
    (re.compile(r"robot\.(.+)\.destination"), ChannelKind.LABEL),
    (re.compile(r"robot\.(.+)\.assigned_zone"), ChannelKind.LABEL),
)

_ROBOT_CHANNEL = re.compile(
    r"robot\.(?P<robot_id>.+)\.(?P<field>activity|health|battery_frac|"
    r"payload_balls|location|destination|assigned_zone|estop_latched|"
    r"awaiting_human)"
)


def channel_kind(channel: str) -> ChannelKind:
    """Return the value kind for ``channel``.

    Fails closed on anything outside the canonical vocabulary rather than
    guessing a shape for an unrecognised channel string.
    """
    kind = _EXACT_KINDS.get(channel)
    if kind is not None:
        return kind
    for pattern, pattern_kind in _PATTERN_KINDS:
        if pattern.fullmatch(channel):
            return pattern_kind
    raise EdgeAdapterError(
        f"channel {channel!r} has no canonical value kind in this repository"
    )


def robot_channel_parts(channel: str) -> tuple[str, str] | None:
    """Split a robot channel into ``(robot_id, field)``, or ``None``."""
    match = _ROBOT_CHANNEL.fullmatch(channel)
    if match is None:
        return None
    return match.group("robot_id"), match.group("field")


def robot_channel(robot_id: str, field: str) -> str:
    return f"robot.{robot_id}.{field}"


__all__ = [
    "ChannelKind",
    "channel_kind",
    "robot_channel",
    "robot_channel_parts",
]
