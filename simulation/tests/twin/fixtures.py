"""Hand-built fixtures matching the real contracts key-for-key."""

LAYOUT = {
    "schema": "nxt-range-viewer/layout/v1",
    "disclaimer": "placeholder disclaimer",
    "coordinate_frame": {"units": "meters", "origin": "dispenser"},
    "dispenser": {"x_m": 0.0, "y_m": 0.0},
    "charger": {"position": {"x_m": 5.0, "y_m": -30.0}, "slots": 2},
    "zones": [
        {"zone_id": "Z1", "position": {"x_m": 40.0, "y_m": -25.0}, "landing_weight": 3,
         "closure_windows": []},
        {"zone_id": "Z2", "position": {"x_m": 70.0, "y_m": -10.0}, "landing_weight": 5,
         "closure_windows": []},
    ],
    "stations": [
        {"station_id": "H1", "position": {"x_m": 10.0, "y_m": -20.0}, "dock_slots": 2,
         "buffer_capacity_balls": 2500, "outage_windows": []},
    ],
    "robots": [
        {"robot_id": "R1", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
        {"robot_id": "R2", "payload_capacity_balls": 600, "initial_battery_frac": 1.0},
    ],
}

STATE = {
    "meta": {"t_s": 60.0, "minute_of_day": 361.0, "facility_open": True,
             "scenario_name": "fixture", "seed": 7},
    "ball_flow": {"total_balls": 100, "clean_available": 60, "clean_sensed": 58.5,
                  "in_wash": 10, "dirty_buffered": {"H1": 10}, "on_field": {"Z1": 12, "Z2": 3},
                  "in_transit": {"R1": 5, "R2": 0}, "conserved": True},
    "washer": {"throughput_balls_per_minute": 40.0, "batch_size_balls": 200, "wip": 10},
    "demand": {"forecast_balls_per_minute": [1.0, 2.0], "forecast_bucket_minutes": 60,
               "minutes_to_close": 900.0, "demand_balls_total": 40, "demand_balls_served": 38,
               "stockout_minutes": 0.0, "service_availability": 1.0},
    "fleet": {"total": 2, "operable": 2, "inoperative": 0, "charging": 0, "awaiting_human": 0},
    "charging": {"slots": 2, "in_use": 0, "queue_length": 0},
    "staff": {"capacity": 1, "busy": 0, "queued_requests": 0},
    "environment": {"wet_ground_speed_multiplier": 1.0, "zones_open": 2, "zones_total": 2,
                    "stations_open": 1, "stations_total": 1},
    "robots": [
        {"robot_id": "R1", "activity": "traveling", "health": "ok", "battery_frac": 0.9,
         "payload_balls": 5, "payload_capacity_balls": 600, "location": "zone:Z1",
         "destination": "station:H1", "assigned_zone": "Z1", "estop_latched": False,
         "awaiting_human": False},
        {"robot_id": "R2", "activity": "idle", "health": "ok", "battery_frac": 1.0,
         "payload_balls": 0, "payload_capacity_balls": 600, "location": "dispenser",
         "destination": None, "assigned_zone": None, "estop_latched": False,
         "awaiting_human": False},
    ],
    "zones": [
        {"zone_id": "Z1", "balls": 12, "is_open": True, "robots_present": 1},
        {"zone_id": "Z2", "balls": 3, "is_open": True, "robots_present": 0},
    ],
    "stations": [
        {"station_id": "H1", "is_open": True, "docked": 0, "queue_length": 0,
         "buffer_balls": 10, "buffer_capacity_balls": 2500},
    ],
}
