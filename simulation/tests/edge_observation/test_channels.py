"""The value-kind table must stay in step with the commissioning vocabulary.

``nxt_commissioning.validation`` owns *which* channels exist.  This module
owns only *what shape* their values take.  If these tests fail,
commissioning is right and ``nxt_edge_observation.channels`` is wrong.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest

import nxt_commissioning.validation
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


# ---------------------------------------------------------------------------
# Real drift detection against the commissioning vocabulary.
#
# The hand-written list above proves agreement on channels this repository
# already knows.  It cannot notice a channel commissioning *adds*, which is
# exactly the drift that matters: an unknown channel makes
# AdapterBinding.__post_init__ reject an entire commissioned site.  These
# tests derive the vocabulary from validation.py itself.
# ---------------------------------------------------------------------------

_CHANNEL_LITERAL = re.compile(r'"((?:[a-z][a-z0-9_]*)(?:\.[a-z0-9_]+)+)"')
_CHANNEL_REGEX = re.compile(r'r"([a-z][a-z0-9_\\.]*\\\.\(\.\+\)[^"]*)"')


def _commissioning_check_sensor_source() -> str:
    source = (
        Path(nxt_commissioning.validation.__file__)
        .read_text(encoding="utf-8")
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_sensor":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("nxt_commissioning.validation._check_sensor not found")


def _commissioning_exact_channels() -> set[str]:
    """Channel string literals compared against `sensor.channel`."""
    body = _commissioning_check_sensor_source()
    return {
        literal
        for literal in _CHANNEL_LITERAL.findall(body)
        # Only dotted names that look like telemetry channels, not messages.
        if literal.count(".") >= 1 and " " not in literal
    }


def test_the_commissioning_vocabulary_is_actually_reachable():
    """Guard the extraction itself, so a silent zero-match cannot pass."""
    exact = _commissioning_exact_channels()
    assert "inventory.dispenser.count" in exact
    assert "wash.washer.wip" in exact
    assert len(exact) >= 6


def test_every_exact_commissioning_channel_has_a_value_kind():
    """Fails if commissioning gains an exact channel this table lacks."""
    missing = sorted(
        channel
        for channel in _commissioning_exact_channels()
        if _resolves(channel) is None
    )
    assert missing == [], (
        "nxt_commissioning.validation accepts these channels but "
        f"nxt_edge_observation.channels cannot shape them: {missing}"
    )


def _robot_field_tokens(body: str) -> list[str]:
    """Parse the robot-channel field alternation out of commissioning source.

    The alternation spans a Python adjacent-string-literal seam
    (``...location|"`` newline ``r"destination...``); the seam is joined
    before splitting so no token arrives mangled.  Every token must come
    back as a bare identifier -- the caller asserts that, so a
    commissioning pattern-format change fails the test instead of
    silently shrinking its scope.
    """
    marker = r"robot\.(.+)\."
    assert marker in body, "robot channel pattern not found in commissioning"
    tail = body.split(marker, 1)[1]
    alternation = tail.split(")", 1)[0].lstrip("(")
    # Join adjacent string-literal seams: closing quote, whitespace,
    # optional r-prefix, opening quote.
    joined = re.sub(r'"\s*r?"', "", alternation)
    return [token.strip() for token in joined.split("|")]


def test_every_patterned_commissioning_channel_has_a_value_kind():
    """Fails if commissioning gains a patterned channel this table lacks."""
    body = _commissioning_check_sensor_source()
    # Each probe is a concrete instance of one pattern commissioning matches.
    probes = (
        "inventory.station.ST1.buffer_balls",
        "scan.zone.Z1.balls",
        "zone.Z1.is_open",
        "station.ST1.is_open",
        "station.ST1.docked",
        "station.ST1.queue_length",
    )
    for channel in probes:
        assert _resolves(channel) is not None, channel

    tokens = _robot_field_tokens(body)
    # Fail closed on the parse itself: a malformed token means the
    # commissioning pattern changed shape, never that a field vanished.
    malformed = [token for token in tokens if not token.isidentifier()]
    assert malformed == [], (
        "the commissioning robot-field alternation no longer parses into "
        f"bare field names; malformed tokens: {malformed!r}"
    )
    declared = set(tokens)
    assert len(tokens) == len(declared), "alternation repeats a field"
    assert {"battery_frac", "estop_latched", "activity"} <= declared, declared
    for field in sorted(declared):
        assert _resolves(f"robot.R1.{field}") is not None, field


def test_the_alternation_parser_fails_on_a_malformed_pattern():
    """Negative control: unexpected surrounding text must surface, not vanish."""
    synthetic = 'x = re.fullmatch(r"robot\\.(.+)\\.(activity|hea lth|\\w+)", c)'
    tokens = _robot_field_tokens(synthetic)
    malformed = [token for token in tokens if not token.isidentifier()]
    assert malformed != [], tokens


def _resolves(channel: str):
    try:
        return channel_kind(channel)
    except EdgeAdapterError:
        return None
