#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const [sourceArgument, destinationArgument] = process.argv.slice(2);
if (!sourceArgument || !destinationArgument) {
  throw new Error(
    "usage: node scripts/build-edge-gateway-replay-excerpt.mjs <episode.json> <destination.json>",
  );
}

const sourcePath = resolve(sourceArgument);
const destinationPath = resolve(destinationArgument);
const sourceBytes = await readFile(sourcePath);
const episode = JSON.parse(sourceBytes.toString("utf8"));

if (
  episode.schema !== "nxt-range-viewer/episode/v1" ||
  episode.meta?.scenario !== "normal_weekday" ||
  episode.meta?.policy !== "inventory_threshold" ||
  episode.meta?.seed !== 101 ||
  !Array.isArray(episode.frames) ||
  episode.frames.length !== episode.meta.n_steps
) {
  throw new Error("source is not the expected deterministic RangeOps replay");
}

const requestedFrames = [
  ["accepted collection directive", 1, "assign_collection(R1,Z3)"],
  ["accepted handoff directive", 282, "send_to_handoff(R1)"],
  ["recorded terminal replay state", 960, "wait"],
];

const frames = requestedFrames.map(([label, step, directive]) => {
  const frame = episode.frames.find((candidate) => candidate.step === step);
  if (!frame || frame.action?.name !== directive) {
    throw new Error(`expected ${directive} at replay step ${step}`);
  }
  const robot = frame.robots.find((candidate) => candidate.robot_id === "R1");
  if (!robot) throw new Error(`R1 missing at replay step ${step}`);
  return {
    label,
    step,
    tSeconds: frame.t_s,
    directive,
    safetyShieldAllowed: frame.action.shield_allowed,
    robotId: robot.robot_id,
    robotActivity: robot.activity,
    robotLocation: robot.location,
    robotDestination: robot.destination,
    robotBatteryFraction: robot.battery_frac,
    robotPayloadBalls: robot.payload_balls,
    dispenserBalls: frame.inventory.dispenser_balls,
    ballsProcessed: frame.kpi.balls_processed,
  };
});

const excerpt = {
  schema: "nxt-edge-gateway-demo/replay-excerpt/v1",
  source: {
    schema: episode.schema,
    scenario: episode.meta.scenario,
    policy: episode.meta.policy,
    policyVersion: episode.meta.policy_version,
    seed: episode.meta.seed,
    simulatorVersion: episode.meta.simulator_version,
    gitCommit: episode.meta.git_commit,
    controlIntervalSeconds: episode.meta.control_interval_s,
    episodeSteps: episode.meta.n_steps,
    episodeSha256: createHash("sha256").update(sourceBytes).digest("hex"),
    disclaimer: episode.meta.disclaimer,
  },
  frames,
};

await writeFile(destinationPath, `${JSON.stringify(excerpt, null, 2)}\n`, "utf8");
