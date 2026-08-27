#!/usr/bin/env python3
"""Publish one deterministic Pilot Course A load-cell message over MQTT.

This is a deployment composition tool, not a device emulator or a production
sensor integration.  It publishes one versioned raw message to a local broker
and exits after the configured QoS acknowledgement.  It has no command,
actuator, robot, cloud, persistence, OTA, or safety-control surface.

Paho is imported only when an MQTT publish is requested, so importing the
deterministic payload/topic helpers does not make transport a core-package
dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path


SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from scripts.edge_gateway_live_input_v0 import (  # noqa: E402
    WIRE_SCHEMA,
    GatewayConfig,
    expected_topic,
    load_gateway_config,
)
from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    CALIBRATION_ID_LOAD_CELL,
    SYNTHETIC_DISPENSER_TARE_KG,
    SYNTHETIC_MASS_PER_BALL_KG,
    commissioned_site,
)


DEFAULT_CONFIG = (
    SIM_ROOT / "configs" / "edge_gateway" / "pilot-course-a.example.yaml"
)
DEFAULT_BOOT_ID = "boot-mock-001"
DEFAULT_SAMPLED_AT_UTC = "2026-08-08T09:29:55.000Z"
DEFAULT_PUBLISHED_AT_UTC = "2026-08-08T09:30:00.000Z"
# Pilot fixture cycle 0 has 6,000 balls at the dispenser.  Matching that
# synthetic fixture keeps the default hybrid rehearsal ball-conserving.
DEFAULT_RAW_VALUE = SYNTHETIC_DISPENSER_TARE_KG + (
    6000 * SYNTHETIC_MASS_PER_BALL_KG
)


def _configured_route_for_sensor(config: GatewayConfig, sensor_id: str):
    for route in config.devices:
        if sensor_id in route.sensor_ids:
            return route
    raise ValueError(f"sensor {sensor_id!r} is not configured for this gateway")


def publisher_topic(
    config: GatewayConfig, device_id: str | None = None
) -> str:
    """Return the one exact configured V1 load-cell topic.

    The helper never invents a route.  An explicit device must already be in
    the gateway config; otherwise publication fails before touching a broker.
    """

    if not isinstance(config, GatewayConfig):
        raise TypeError("config must be a GatewayConfig")
    selected = config.devices[0].device_id if device_id is None else device_id
    if config.route_for_device(selected) is None:
        raise ValueError(f"device {selected!r} is not configured for this gateway")
    return expected_topic(config, selected)


def build_payload(
    config: GatewayConfig,
    *,
    raw_value: float | int | None = DEFAULT_RAW_VALUE,
    sampled_at_utc: str = DEFAULT_SAMPLED_AT_UTC,
    published_at_utc: str = DEFAULT_PUBLISHED_AT_UTC,
    device_sequence: int = 0,
    boot_id: str = DEFAULT_BOOT_ID,
    sensor_id: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a deterministic wire payload from declared gateway identity.

    ``overrides`` intentionally applies last.  It exists for explicit negative
    smoke cases (for example a wrong unit or fault status), so this helper does
    not run the gateway decoder and silently repair malformed test input.
    """

    if not isinstance(config, GatewayConfig):
        raise TypeError("config must be a GatewayConfig")
    selected_sensor = (
        config.devices[0].sensor_ids[0] if sensor_id is None else sensor_id
    )
    route = _configured_route_for_sensor(config, selected_sensor)
    payload: dict[str, object] = {
        "schema": WIRE_SCHEMA,
        "site_id": config.site_id,
        "deployment_id": config.deployment_id,
        "gateway_id": config.gateway_id,
        "device_id": route.device_id,
        "sensor_id": selected_sensor,
        "boot_id": boot_id,
        "device_sequence": device_sequence,
        "sampled_at_utc": sampled_at_utc,
        "published_at_utc": published_at_utc,
        "raw_value": raw_value,
        "raw_unit": "kg",
        "device_status": "ok",
        "calibration_id": CALIBRATION_ID_LOAD_CELL,
        "diagnostic_code": None,
    }
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise TypeError("overrides must be a mapping")
        payload.update(dict(overrides))
    return payload


def canonical_payload_json(payload: Mapping[str, object]) -> str:
    """Encode one message deterministically and reject non-finite JSON."""

    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_paho():
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError(
            "Paho MQTT is unavailable; install the 'edge-gateway' extra"
        ) from exc
    return mqtt


def publish_payload(
    config: GatewayConfig,
    payload: Mapping[str, object],
    *,
    timeout_s: float = 10.0,
) -> str:
    """Publish once with Callback API V2 and wait for the configured QoS ack."""

    if not isinstance(config, GatewayConfig):
        raise TypeError("config must be a GatewayConfig")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(float(timeout_s))
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be a positive finite number")
    device_id = payload.get("device_id")
    if type(device_id) is not str:
        raise ValueError("payload device_id must be a string")
    topic = publisher_topic(config, device_id=device_id)
    encoded = canonical_payload_json(payload)
    mqtt = _load_paho()
    deadline = time.monotonic() + float(timeout_s)

    def remaining(stage: str) -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError(f"timed out during MQTT {stage}")
        return value

    connected = threading.Event()
    connection_error: list[str] = []

    def on_connect(client, userdata, flags, reason_code, properties):
        del client, userdata, flags, properties
        reason_value = getattr(reason_code, "value", reason_code)
        if getattr(reason_code, "is_failure", False) or reason_value != 0:
            connection_error.append(str(reason_code))
        connected.set()

    # The publisher must not reuse the running gateway's client ID: MQTT
    # brokers disconnect an existing session when the same client reconnects.
    # This suffix is deterministic and does not alter wire gateway identity.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{config.broker.client_id}-mock-publisher",
    )
    client.on_connect = on_connect
    try:
        client.connect_timeout = remaining("connection establishment")
        result = client.connect(
            config.broker.host,
            port=config.broker.port,
            keepalive=config.broker.keepalive_s,
        )
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT connect returned error code {result}")
        while not connected.is_set():
            loop_result = client.loop(
                timeout=min(0.1, remaining("broker connection"))
            )
            if loop_result != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(
                    f"MQTT network loop returned error code {loop_result}"
                )
        if connection_error:
            raise RuntimeError(f"MQTT broker rejected connection: {connection_error[0]}")

        info = client.publish(
            topic,
            payload=encoded,
            qos=config.broker.qos,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish returned error code {info.rc}")
        while not info.is_published():
            loop_result = client.loop(
                timeout=min(0.1, remaining("publish acknowledgement"))
            )
            if loop_result != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(
                    f"MQTT network loop returned error code {loop_result}"
                )
        return topic
    finally:
        client.disconnect()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-value", type=float, default=DEFAULT_RAW_VALUE)
    parser.add_argument("--sampled-at-utc", default=DEFAULT_SAMPLED_AT_UTC)
    parser.add_argument("--published-at-utc", default=DEFAULT_PUBLISHED_AT_UTC)
    parser.add_argument("--device-sequence", type=int, default=0)
    parser.add_argument("--boot-id", default=DEFAULT_BOOT_ID)
    parser.add_argument("--sensor-id")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print deterministic topic/payload JSON without connecting",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_gateway_config(args.config, site=commissioned_site())
        payload = build_payload(
            config,
            raw_value=args.raw_value,
            sampled_at_utc=args.sampled_at_utc,
            published_at_utc=args.published_at_utc,
            device_sequence=args.device_sequence,
            boot_id=args.boot_id,
            sensor_id=args.sensor_id,
        )
        topic = publisher_topic(config, device_id=payload["device_id"])
        if not args.dry_run:
            topic = publish_payload(config, payload, timeout_s=args.timeout_s)
        print(
            json.dumps(
                {
                    "published": not args.dry_run,
                    "qos": config.broker.qos,
                    "topic": topic,
                    "payload": payload,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return 0
    except (OSError, TypeError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"mock publisher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BOOT_ID",
    "DEFAULT_CONFIG",
    "DEFAULT_PUBLISHED_AT_UTC",
    "DEFAULT_RAW_VALUE",
    "DEFAULT_SAMPLED_AT_UTC",
    "build_payload",
    "canonical_payload_json",
    "main",
    "publish_payload",
    "publisher_topic",
]
