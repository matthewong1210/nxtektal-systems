# ROS 2 integration plan (Phase 2 — physical robot)

Status: **not started — blocked on chassis SDK docs + mechanism hardware.**

## Principle

The physical robot runs the **same** `HandoffController` and configs as the
simulator. Only the adapter changes: `Ros2Adapter` implements
`RobotTaskInterface` by delegating to ROS 2 actions/services on the robot
computer. `scenario.adapter: ros2` is the only config difference.

## Environment requirements

* ROS 2 Humble (Ubuntu 22.04) or Jazzy (24.04) on the robot computer.
* AgileX ROS 2 driver for the chosen chassis (e.g. the vendor `*_ros2` stack)
  — the exact package depends on the model; blocked on the SDK docs
  (docs/missing_inputs.md).
* Nav2 for navigation; an AprilTag detection node for final alignment.
* Drivers for the lift/tilt actuators and contact switches (mechanism TBD).

None of this is installed on the current dev machine; it targets the robot's
onboard computer or a Linux dev box.

## Mapping (mirrors the stub in nxt_sim/adapters/ros2/ros2_adapter.py)

| Interface method              | ROS 2 realization                                      |
|-------------------------------|--------------------------------------------------------|
| navigate_to_pose              | Nav2 `NavigateToPose` action                           |
| approach_handoff_station      | custom `ApproachStation` action (AprilTag servoing)    |
| dock / undock                 | custom actions over chassis velocity + contact GPIO    |
| verify_docking                | contact-switch topic + marker-pose residual check      |
| lift / dump / lower           | actuator driver action(s); joint limits from config    |
| verify_unloading              | load-cell delta and/or camera check                    |
| return_to_charge              | Nav2 `NavigateToPose`                                  |
| emergency_stop                | hardware e-stop channel + twist-mux software latch     |
| recover_from_failed_docking   | behavior-tree back-off node                            |

Timeouts map to action deadlines; `TaskResult.duration_s` is wall-clock.
`get_telemetry()` publishes bumper events (collision_count), sonar/lidar
minima (min_clearance_m), and IMU-derived tilt margin.

## Safety notes for hardware bring-up

* The software e-stop latch must mirror — never replace — a safety-rated
  hardware e-stop chain.
* `safety.step_timeouts_s` and `emergency_stop_distance_m` get re-derived from
  measured robot behavior before first autonomous docking.
* Sim-established tolerance envelopes (max lateral/yaw error) are starting
  hypotheses for physical trials, not guarantees.
