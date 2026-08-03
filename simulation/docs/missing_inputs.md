# Missing real-world inputs — supplier & site request list

Everything below is currently a **placeholder** in `configs/`. Each item lists
who to ask and which config field(s) it unblocks.

## From AgileX (chassis)

| # | Item | Unblocks |
|---|---|---|
| 1 | Chassis CAD (STEP) and/or URDF + meshes | `assets/robot/`, Isaac import |
| 2 | Wheelbase and track width | `robot_geometry.wheelbase_m`, `track_width_m` |
| 3 | Steering/drive model (differential? ackermann?) + kinematic limits | `robot_geometry.drive_type`, mock/Isaac motion model |
| 4 | Chassis mass | `robot_geometry.chassis_mass_kg` |
| 5 | Maximum payload | `robot_geometry.max_payload_kg` |
| 6 | Approximate center of mass (unloaded) | `robot_geometry.com_height_unloaded_m`, stability model |
| 7 | Max speed, acceleration, braking deceleration | `max_speed_mps`, `max_decel_mps2`, e-stop math |
| 8 | ROS 2 driver / SDK documentation (topics, actions, e-stop interface) | Phase 2 `Ros2Adapter` |
| 9 | Battery/charging dock interface spec | `return_to_charge` behavior, charge dock asset |

## From our mechanism design (internal, once designed)

| # | Item | Unblocks |
|---|---|---|
| 10 | Basket dimensions (L/W/D) | `handoff_mechanism.basket_*` |
| 11 | Full-basket weight (ball count x ball mass + basket) | `basket_full_mass_kg`, `scenario.payload_kg` |
| 12 | Lift travel | `lift_travel_m` |
| 13 | Lift speed (loaded) | `lift_speed_mps` |
| 14 | Maximum dump angle + dump speed | `max_dump_angle_deg`, `dump_speed_deg_per_s` |
| 15 | CoM at full lift, loaded | `com_height_lifted_m`, tipping indicator |
| 16 | Actuator specs (force/torque, duty cycle) | Isaac articulation limits, hardware drivers |
| 17 | Docking guide geometry (funnel width/depth/angle) | `docking_guide.*` |

## From equipment manufacturers (per washer/hopper model)

| # | Item | Unblocks |
|---|---|---|
| 18 | Inlet dimensions (height, width, depth) + drawing | `equipment_profile.inlet_*` |
| 19 | Acceptable approach directions / service clearances | `preferred_approach_direction`, `safety_zone` |
| 20 | Required dump height/angle for reliable intake | `required_dump_angle_deg` |
| 21 | Intake throughput (balls/min) and dwell requirements | `unloading_cycle.*` |
| 22 | Mounting options for a marker bracket + guide | `docking_marker_pose`, guide design |

## From the site (per driving range)

| # | Item | Unblocks |
|---|---|---|
| 23 | Available approach clearance around the equipment | scenario poses, `safety_zone` |
| 24 | Docking-area ground slope + surface type | `facility.ground_slope_deg`, traction assumptions |
| 25 | Expected localization accuracy of the nav stack on site | `initial_error` sweep ranges |
| 26 | Proposed sensor list (camera, ultrasonic, contact switches, lidar) | `robot_geometry.sensors`, Phase 1/2 perception |
| 27 | Operating hours / lighting conditions for marker detection | physical-test plan |

## How to apply incoming data

1. Replace the placeholder value in the relevant YAML; set
   `source: supplier` (datasheet) or `source: measured` (field-verified) and
   cite the document in `note`.
2. Run `uv run python scripts/validate_configs.py` — cross-checks will flag
   any new inconsistencies (basket vs inlet, lift vs inlet height, ...).
3. Re-run the sweeps; compare tolerance envelopes against the placeholder
   baseline before trusting any change.
