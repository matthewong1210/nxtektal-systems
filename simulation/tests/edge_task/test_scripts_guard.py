"""Guards on the composition scripts: transport isolation, storage boundaries, vocabulary."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = SIMULATION_ROOT / "scripts"
GATEWAY = SCRIPTS / "edge_task_gateway_v0.py"
ROBOT = SCRIPTS / "mock_robot_task_device.py"
CLI = SCRIPTS / "edge_task_cli.py"
TRANSPORT = SCRIPTS / "edge_task_transport.py"
FIXTURE = SCRIPTS / "pilot_course_a_task_fixture.py"
ALL_SCRIPTS = (GATEWAY, ROBOT, CLI, TRANSPORT, FIXTURE)
CONFIG = SIMULATION_ROOT / "configs" / "edge_task" / "pilot-course-a.sim.example.json"
BROKER_CONF = SIMULATION_ROOT / "deploy" / "edge-task-v0" / "mosquitto.loopback.conf"

ALLOWED_FIRST_PARTY = {
    GATEWAY: {"nxt_edge_task", "scripts"},
    ROBOT: {"nxt_edge_task", "scripts"},
    CLI: {"nxt_edge_task", "scripts"},
    TRANSPORT: set(),
    FIXTURE: {"nxt_commissioning", "nxt_edge_task", "scripts"},
}

BANNED_IMPORT_ROOTS = {
    "rclpy", "rospy", "nav2", "serial", "pyserial", "pymodbus", "minimalmodbus", "can", "socketcan", "asyncua", "opcua",
    "kafka", "confluent_kafka", "pika", "bleak", "usb", "sqlite3", "boto3", "botocore", "azure", "google", "requests",
    "openai", "anthropic", "langchain", "nxt_sim", "nxt_range_ops", "nxt_facility", "nxt_telemetry", "nxt_pilot_ops",
    "nxt_site_runtime", "nxt_agent_runtime", "nxt_edge_observation", "nxt_memory", "nxt_range_twin",
}

EXECUTION_TOKENS = (
    "RobotTaskInterface", "HandoffController", "SafetyShield", "apply_directive", "send_robot_command",
    "actuator_command", "motion_plan", "emergency_stop(", "reset_estop", "clear_estop", "return_to_charge(",
    "write_register", "write_coil", "set_output", "llm", "openai", "anthropic",
)

# The Edge -> robot vocabulary is exactly the task request; no control verbs exist.
CONTROL_VOCABULARY = ("task/pause", "task/resume", "task/cancel", "task/reset", "estop/", "/command", "/control")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_scripts_import_only_approved_first_party_roots_and_no_banned_stacks() -> None:
    for path, allowed in ALLOWED_FIRST_PARTY.items():
        roots = _imports(path)
        first_party = {r for r in roots if r.startswith("nxt_") or r == "scripts"}
        assert first_party <= allowed, f"{path.name} imports {first_party - allowed}"
        assert not (roots & BANNED_IMPORT_ROOTS), f"{path.name} imports banned {roots & BANNED_IMPORT_ROOTS}"


def test_paho_is_confined_to_the_transport_module() -> None:
    for path in ALL_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        if path is TRANSPORT:
            assert "import paho.mqtt.client" in text
        else:
            assert "paho" not in text, f"{path.name} must not touch paho directly"
    tree = ast.parse(TRANSPORT.read_text(encoding="utf-8"))
    top_level = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)} | {
        n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "paho" not in top_level, "paho must be imported lazily so the double works without it"


def test_processes_never_read_another_role_directory() -> None:
    for path in (GATEWAY, CLI):
        text = path.read_text(encoding="utf-8")
        assert "robots/" not in text and "robot_task_journal" not in text and "notification-receiver" not in text, path.name
    text = ROBOT.read_text(encoding="utf-8")
    assert "edge_task_journal" not in text and "notification-receiver" not in text
    for path in ALL_SCRIPTS:
        assert "interventions" not in path.read_text(encoding="utf-8"), f"{path.name} references PR B components"


def test_no_execution_tokens_or_control_vocabulary() -> None:
    for path in ALL_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in EXECUTION_TOKENS:
            assert token.lower() not in lowered, f"{path.name} mentions {token!r}"
        for verb in CONTROL_VOCABULARY:
            assert verb not in text, f"{path.name} defines control vocabulary {verb!r}"


def test_no_live_hardware_switch_anywhere() -> None:
    for path in ALL_SCRIPTS + (CONFIG,):
        text = path.read_text(encoding="utf-8")
        for literal in ('"LIVE"', '"PRODUCTION"', "REAL_HARDWARE", "--live", "--real-robot", "--hardware"):
            assert literal not in text, f"{path.name} contains {literal!r}"
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert "environment" not in config and "kind" not in json.dumps(config)


def test_example_config_matches_the_commissioned_fixture() -> None:
    import sys

    if str(SIMULATION_ROOT) not in sys.path:
        sys.path.insert(0, str(SIMULATION_ROOT))
    from nxt_edge_task.contracts import EdgeTaskConfig
    from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site

    config = EdgeTaskConfig.from_json(CONFIG.read_bytes())
    facts = admission_facts(commissioned_site())
    assert (config.site_id, config.deployment_id) == (facts.site_id, facts.deployment_id)
    assert set(config.robot_ids) == facts.robot_ids == {"picker-01", "carrier-01"}
    assert config.robot("carrier-01").task_types == ()
    assert config.robot("picker-01").task_types == ("COLLECT_BALLS_ZONE",)
    assert config.broker_host == "127.0.0.1"


def test_broker_conf_is_loopback_nonpersistent_and_task_specific() -> None:
    text = BROKER_CONF.read_text(encoding="utf-8")
    assert re.search(r"^listener 18830 127\.0\.0\.1$", text, re.M)
    assert "0.0.0.0" not in text
    assert re.search(r"^persistence false$", text, re.M)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["broker"]["port"] == 18830 and config["broker"]["port"] != 1883


def test_task_request_is_the_only_edge_to_robot_message() -> None:
    text = GATEWAY.read_text(encoding="utf-8")
    publishes = re.findall(r"self\.client\.publish\((\w+)", text)
    assert publishes == ["topic"]
    assert "request_topic(" in text and "status_topic(" in text and "event_topic(" in text
    assert text.count("request_topic(") == 1
