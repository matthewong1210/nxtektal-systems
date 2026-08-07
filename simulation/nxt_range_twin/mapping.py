"""FacilityState dict -> USD opinions. The checked derivation table.

Every emitted nxt: attribute traces to a contract field; unknown input keys
abort; the Task 9 derivation audit walks built stages against EMITTED_ATTRS.
stdlib only — values are plain Python; pxr types are applied in overlay.py.
"""
from __future__ import annotations

from nxt_range_twin.placement import resolve_location, robot_offset

Opinion = tuple[str, str, str, object]  # (prim_path, attr, sdf_type, value)

SITE = "/World/Site"
OPS = "/World/Ops"

# Top-level state groups the mapping consumes or deliberately ignores.
CONSUMED_GROUPS = frozenset(
    {"meta", "ball_flow", "washer", "demand", "fleet", "charging", "staff",
     "environment", "robots", "zones", "stations"}
)
SNAPSHOT_IGNORED_KEYS: frozenset[str] = frozenset()  # nothing ignored in v1

ROBOT_KEYS = frozenset(
    {"robot_id", "activity", "health", "battery_frac", "payload_balls",
     "payload_capacity_balls", "location", "destination", "assigned_zone",
     "estop_latched", "awaiting_human"}
)
ZONE_KEYS = frozenset({"zone_id", "balls", "is_open", "robots_present"})
STATION_KEYS = frozenset(
    {"station_id", "is_open", "docked", "queue_length", "buffer_balls",
     "buffer_capacity_balls"}
)

HEALTH_COLORS = {
    "ok": (0.20, 0.75, 0.30),
    "degraded": (0.95, 0.75, 0.10),
    "failed": (0.90, 0.15, 0.15),
}
ESTOP_COLOR = (0.85, 0.10, 0.85)
AWAITING_COLOR = (0.15, 0.35, 0.95)

EMITTED_ATTRS = frozenset(
    {
        # /World/Ops (facility scoreboard)
        "nxt:t_s", "nxt:minute_of_day", "nxt:facility_open",
        "nxt:balls_total", "nxt:balls_conserved", "nxt:in_wash",
        "nxt:minutes_to_close", "nxt:demand_balls_total", "nxt:demand_balls_served",
        "nxt:stockout_minutes", "nxt:service_availability",
        "nxt:fleet_total", "nxt:fleet_operable", "nxt:fleet_inoperative",
        "nxt:fleet_charging", "nxt:fleet_awaiting_human",
        "nxt:wet_ground_speed_multiplier", "nxt:zones_open", "nxt:zones_total",
        "nxt:stations_open", "nxt:stations_total",
        # dispenser
        "nxt:clean_available", "nxt:clean_sensed",
        # aspatial washer / staff
        "nxt:wip", "nxt:throughput_balls_per_minute", "nxt:batch_size_balls",
        "nxt:staff_capacity", "nxt:staff_busy", "nxt:staff_queued_requests",
        # charger
        "nxt:slots", "nxt:in_use", "nxt:queue_length",
        # zones
        "nxt:balls", "nxt:is_open", "nxt:robots_present",
        # stations
        "nxt:docked", "nxt:buffer_balls", "nxt:buffer_capacity_balls",
        # robots
        "nxt:activity", "nxt:health", "nxt:battery_frac", "nxt:payload_balls",
        "nxt:payload_capacity_balls", "nxt:location", "nxt:destination",
        "nxt:assigned_zone", "nxt:estop_latched", "nxt:awaiting_human",
        # static (base layer)
        "nxt:landing_weight", "nxt:dock_slots", "nxt:provenance",
    }
)


def _check_keys(record: dict, allowed: frozenset, label: str) -> None:
    unknown = sorted(set(record) - allowed - SNAPSHOT_IGNORED_KEYS)
    if unknown:
        raise ValueError(f"unknown {label} keys (contract drift?): {unknown}")


def _robot_color(robot: dict) -> tuple[float, float, float]:
    if robot["estop_latched"]:
        return ESTOP_COLOR
    if robot["awaiting_human"]:
        return AWAITING_COLOR
    return HEALTH_COLORS.get(robot["health"], HEALTH_COLORS["degraded"])


def frame_opinions(
    state: dict,
    index: dict[str, tuple[float, float]],
    robot_ids: tuple[str, ...],
) -> list[Opinion]:
    _check_keys(state, CONSUMED_GROUPS, "state group")
    ops: list[Opinion] = []

    meta, flow = state["meta"], state["ball_flow"]
    demand, fleet = state["demand"], state["fleet"]
    env, staff = state["environment"], state["staff"]

    ops += [
        (OPS, "nxt:t_s", "double", float(meta["t_s"])),
        (OPS, "nxt:minute_of_day", "double", float(meta["minute_of_day"])),
        (OPS, "nxt:facility_open", "bool", bool(meta["facility_open"])),
        (OPS, "nxt:balls_total", "int", int(flow["total_balls"])),
        (OPS, "nxt:balls_conserved", "bool", bool(flow["conserved"])),
        (OPS, "nxt:in_wash", "int", int(flow["in_wash"])),
        (OPS, "nxt:minutes_to_close", "double", float(demand["minutes_to_close"])),
        (OPS, "nxt:demand_balls_total", "int", int(demand["demand_balls_total"])),
        (OPS, "nxt:demand_balls_served", "int", int(demand["demand_balls_served"])),
        (OPS, "nxt:stockout_minutes", "double", float(demand["stockout_minutes"])),
        (OPS, "nxt:service_availability", "double", float(demand["service_availability"])),
        (OPS, "nxt:fleet_total", "int", int(fleet["total"])),
        (OPS, "nxt:fleet_operable", "int", int(fleet["operable"])),
        (OPS, "nxt:fleet_inoperative", "int", int(fleet["inoperative"])),
        (OPS, "nxt:fleet_charging", "int", int(fleet["charging"])),
        (OPS, "nxt:fleet_awaiting_human", "int", int(fleet["awaiting_human"])),
        (OPS, "nxt:wet_ground_speed_multiplier", "double",
         float(env["wet_ground_speed_multiplier"])),
        (OPS, "nxt:zones_open", "int", int(env["zones_open"])),
        (OPS, "nxt:zones_total", "int", int(env["zones_total"])),
        (OPS, "nxt:stations_open", "int", int(env["stations_open"])),
        (OPS, "nxt:stations_total", "int", int(env["stations_total"])),
        (f"{SITE}/Dispenser", "nxt:clean_available", "int", int(flow["clean_available"])),
        (f"{SITE}/Dispenser", "nxt:clean_sensed", "double", float(flow["clean_sensed"])),
        (f"{SITE}/Aspatial/Washer", "nxt:wip", "int", int(state["washer"]["wip"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_capacity", "int", int(staff["capacity"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_busy", "int", int(staff["busy"])),
        (f"{SITE}/Aspatial/Staff", "nxt:staff_queued_requests", "int",
         int(staff["queued_requests"])),
        (f"{SITE}/Charger", "nxt:in_use", "int", int(state["charging"]["in_use"])),
        (f"{SITE}/Charger", "nxt:queue_length", "int", int(state["charging"]["queue_length"])),
    ]

    for zone in state["zones"]:
        _check_keys(zone, ZONE_KEYS, "zone")
        prim = f"{SITE}/Zones/{zone['zone_id']}"
        ops += [
            (prim, "nxt:balls", "int", int(zone["balls"])),
            (prim, "nxt:is_open", "bool", bool(zone["is_open"])),
            (prim, "nxt:robots_present", "int", int(zone["robots_present"])),
        ]

    for station in state["stations"]:
        _check_keys(station, STATION_KEYS, "station")
        prim = f"{SITE}/Stations/{station['station_id']}"
        ops += [
            (prim, "nxt:is_open", "bool", bool(station["is_open"])),
            (prim, "nxt:docked", "int", int(station["docked"])),
            (prim, "nxt:queue_length", "int", int(station["queue_length"])),
            (prim, "nxt:buffer_balls", "int", int(station["buffer_balls"])),
        ]

    for robot in state["robots"]:
        _check_keys(robot, ROBOT_KEYS, "robot")
        prim = f"{SITE}/Robots/{robot['robot_id']}"
        dx, dy = robot_offset(robot["robot_id"], robot_ids)
        translate_ops: list[Opinion] = []
        # "transit" is a real upstream location_label (the upstream simulator's
        # core sim module sets robot.location_label = "transit" mid-travel; its
        # entities module's location comment enumerates node ids and omits it)
        # but it is not a placement node in the layout index. A robot in transit HOLDS
        # its last authored position: honest, no invented kinematics. So we
        # simply omit the xformOp:translate opinion for this frame; USD's
        # default held-timesample behavior keeps the last authored value until
        # the robot lands somewhere resolvable again. All other robot
        # opinions (including the "transit" location token) are still emitted.
        if robot["location"] != "transit":
            anchor = resolve_location(robot["location"], index)
            translate_ops = [
                (prim, "xformOp:translate", "double3",
                 (anchor[0] + dx, anchor[1] + dy, 0.0)),
            ]
        ops += translate_ops
        ops += [
            (prim, "primvars:displayColor", "color3f[]", [_robot_color(robot)]),
            (prim, "nxt:activity", "token", str(robot["activity"])),
            (prim, "nxt:health", "token", str(robot["health"])),
            (prim, "nxt:battery_frac", "double", float(robot["battery_frac"])),
            (prim, "nxt:payload_balls", "int", int(robot["payload_balls"])),
            (prim, "nxt:location", "token", str(robot["location"])),
            (prim, "nxt:destination", "token", str(robot["destination"] or "")),
            (prim, "nxt:assigned_zone", "token", str(robot["assigned_zone"] or "")),
            (prim, "nxt:estop_latched", "bool", bool(robot["estop_latched"])),
            (prim, "nxt:awaiting_human", "bool", bool(robot["awaiting_human"])),
        ]

    ops.sort(key=lambda o: (o[0], o[1]))
    return ops
