"""The value-kind table must stay in step with the commissioning vocabulary.

``nxt_commissioning.validation`` owns *which* channels exist.  This module
owns only *what shape* their values take.  If these tests fail,
commissioning is right and ``nxt_edge_observation.channels`` is wrong.
"""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import CommissionedSite
from nxt_edge_observation import ChannelKind, EdgeAdapterError, channel_kind
from nxt_edge_observation.channels import robot_channel_parts

from scripts.pilot_course_a_edge_fixture import commissioned_site_payload

# Every channel the commissioned Pilot Course A manifest declares, plus the
# canonical channels no V0 adapter family produces.  All of them are part of
# the closed commissioning vocabulary and must have a declared value kind.
CANONICAL_CHANNELS = (
    "inventory.dispenser.count",
    "inventory.dispenser.sensed",
    "wash.washer.wip",
    "charger.site.queue_length",
    "staff.site.busy",
    "staff.site.queued",
    "inventory.station.ST1.buffer_balls",
    "scan.zone.Z1.balls",
    "zone.Z1.is_open",
    "station.ST1.is_open",
    "station.ST1.docked",
    "station.ST1.queue_length",
    "robot.R1.activity",
    "robot.R1.health",
    "robot.R1.battery_frac",
    "robot.R1.payload_balls",
    "robot.R1.location",
    "robot.R1.destination",
    "robot.R1.assigned_zone",
    "robot.R1.estop_latched",
    "robot.R1.awaiting_human",
)


@pytest.mark.parametrize("channel", CANONICAL_CHANNELS)
def test_every_canonical_channel_has_a_value_kind(channel):
    assert isinstance(channel_kind(channel), ChannelKind)


def test_every_commissioned_binding_channel_resolves(site):
    for binding in site.sensor_bindings:
        assert isinstance(channel_kind(binding.channel), ChannelKind)


@pytest.mark.parametrize(
    "channel, expected",
    [
        ("inventory.dispenser.count", ChannelKind.COUNT),
        ("inventory.dispenser.sensed", ChannelKind.QUANTITY),
        ("wash.washer.wip", ChannelKind.COUNT),
        ("station.ST1.docked", ChannelKind.COUNT),
        ("station.ST1.is_open", ChannelKind.BOOLEAN),
        ("zone.Z1.is_open", ChannelKind.BOOLEAN),
        ("robot.R1.battery_frac", ChannelKind.FRACTION),
        ("robot.R1.payload_balls", ChannelKind.COUNT),
        ("robot.R1.estop_latched", ChannelKind.BOOLEAN),
        ("robot.R1.activity", ChannelKind.LABEL),
        ("robot.R1.destination", ChannelKind.LABEL),
    ],
)
def test_value_kinds_match_the_assembler_accessors(channel, expected):
    assert channel_kind(channel) is expected


@pytest.mark.parametrize(
    "channel",
    [
        "robot.R1.temperature",
        "washer.WASH1.running",
        "handoff.ST1.docked",
        "inventory.dispenser",
        "",
        "env.site.rain_mm",
    ],
)
def test_channels_outside_the_vocabulary_fail_closed(channel):
    with pytest.raises(EdgeAdapterError):
        channel_kind(channel)


def _payload_with_channel(channel: str, **binding_changes) -> dict:
    payload = commissioned_site_payload()
    binding = copy.deepcopy(payload["sensor_bindings"][0])
    binding["sensor_id"] = "sensor-probe-01"
    binding["channel"] = channel
    binding.update(binding_changes)
    payload["sensor_bindings"] = [binding]
    return payload


def test_a_channel_commissioning_rejects_has_no_value_kind_either():
    """Negative control across the two layers: both must refuse the same string."""
    invented = "washer.WASH1.running"
    with pytest.raises(ValueError):
        CommissionedSite.from_dict(_payload_with_channel(invented))
    with pytest.raises(EdgeAdapterError):
        channel_kind(invented)


def test_a_channel_commissioning_accepts_also_has_a_value_kind():
    payload = _payload_with_channel("inventory.dispenser.count")
    site = CommissionedSite.from_dict(payload)
    (binding,) = site.sensor_bindings
    assert channel_kind(binding.channel) is ChannelKind.COUNT


def test_robot_channel_parts_round_trips():
    assert robot_channel_parts("robot.R1.battery_frac") == ("R1", "battery_frac")
    assert robot_channel_parts("robot.R1.temperature") is None
    assert robot_channel_parts("zone.Z1.is_open") is None
