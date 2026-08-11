"use client";

import {
  ChangeEvent,
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type JsonObject = Record<string, unknown>;

type Evidence = {
  source: string;
  line?: number;
  path: string;
  value: string;
  raw: string;
};

type Fact = {
  label: string;
  value: string;
  detail?: string;
  tone?: "neutral" | "warning" | "positive";
};

export type StoryStage = {
  id: "state" | "risk" | "recommend" | "task" | "outcome";
  navLabel: string;
  time: string;
  eyebrow: string;
  title: string;
  body: string;
  claim:
    | "observed"
    | "detected"
    | "recommended"
    | "simulated"
    | "unconfirmed"
    | "recorded";
  focusEntity?: string;
  focusNode?: string;
  entityState?: "offline" | "failed" | "active" | "recovered";
  facts: Fact[];
  evidence: Evidence[];
  evidenceTotal?: number;
};

type MapNode = {
  id: string;
  label: string;
  kind: "zone" | "station" | "charger" | "dispenser";
  x: number;
  y: number;
};

type RawMapNode = MapNode & { rawX: number; rawY: number };

export type StoryData = {
  title: string;
  scenario: string;
  policy: string;
  sourceMode: "reference" | "artifacts";
  sourceLabel: string;
  sourceFiles: string[];
  warnings: string[];
  layoutMode: "reference" | "artifact" | "reference-fallback";
  stages: StoryStage[];
  nodes: MapNode[];
  focusEntity: string;
  focusNode: string;
};

export type SourcedRecord = {
  source: string;
  line: number;
  value: JsonObject;
};

export type ParseIssue = {
  source: string;
  line: number;
  message: string;
};

export type ParseResult = {
  records: SourcedRecord[];
  issues: ParseIssue[];
};

type RecommendationMatch = {
  source: SourcedRecord;
  rec: JsonObject;
  index: number;
  owner: string;
  policyId?: string;
};

export const MAX_ARTIFACT_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_ARTIFACT_TOTAL_BYTES = 30 * 1024 * 1024;
export const MAX_ARTIFACT_FILES = 16;
export const MAX_ARTIFACT_RECORD_BYTES = 256 * 1024;
export const MAX_ARTIFACT_RECORDS = 50_000;
export const MAX_ARTIFACT_TOTAL_RECORDS = 100_000;
export const MAX_ARTIFACT_NESTED_ITEMS = 10_000;
export const MAX_RENDERED_EVIDENCE_RECORDS = 100;

const REFERENCE_NODES: MapNode[] = [
  { id: "DISPENSER", label: "Dispenser", kind: "dispenser", x: 9, y: 23 },
  { id: "CHARGER", label: "Charger", kind: "charger", x: 11, y: 76 },
  { id: "H1", label: "H1", kind: "station", x: 19, y: 62 },
  { id: "Z1", label: "Z1", kind: "zone", x: 31, y: 70 },
  { id: "Z2", label: "Z2", kind: "zone", x: 43, y: 58 },
  { id: "Z3", label: "Z3", kind: "zone", x: 55, y: 48 },
  { id: "Z4", label: "Z4", kind: "zone", x: 67, y: 40 },
  { id: "Z5", label: "Z5", kind: "zone", x: 80, y: 50 },
  { id: "Z6", label: "Z6", kind: "zone", x: 91, y: 61 },
];

const referenceEvidence = (
  source: string,
  line: number | undefined,
  path: string,
  value: string,
  raw: string,
): Evidence => ({ source, line, path, value, raw });

export const REFERENCE_STORY: StoryData = {
  title: "H1 outage response",
  scenario: "handoff_station_outage",
  policy: "inventory_threshold · deterministic baseline",
  sourceMode: "reference",
  sourceLabel: "Recovered simulation-reference transcript",
  sourceFiles: [
    "events.jsonl",
    "facility_states.jsonl",
    "layout.json",
    "recommendations",
    "briefings",
  ],
  warnings: [
    "Original source artifacts are not embedded; values are a recovered simulation-reference transcript.",
  ],
  layoutMode: "reference",
  focusEntity: "R3",
  focusNode: "Z4",
  nodes: REFERENCE_NODES,
  stages: [
    {
      id: "state",
      navLabel: "Facility changes",
      time: "12:00",
      eyebrow: "Observed state change",
      title: "The only handoff station goes offline",
      body:
        "At 12:00, H1 enters the recorded outage window. Dirty payload can still be collected, but the facility temporarily loses its only transfer point into the wash cycle.",
      claim: "observed",
      focusEntity: "H1",
      focusNode: "H1",
      entityState: "offline",
      facts: [
        { label: "Handoff", value: "0 / 1", detail: "stations available", tone: "warning" },
        { label: "Outage", value: "120 min", detail: "12:00–14:00", tone: "warning" },
        { label: "Fleet", value: "3 robots", detail: "collection continues" },
      ],
      evidence: [
        referenceEvidence(
          "events.jsonl",
          undefined,
          "kind",
          "station_outage",
          '{"t_s":21600,"kind":"station_outage","payload":{"station_id":"H1"}}',
        ),
        referenceEvidence(
          "layout.json",
          undefined,
          "stations[0].outage_windows",
          "12:00–14:00",
          '{"station_id":"H1","outage_windows":[{"start_minute":720,"end_minute":840}]}',
        ),
      ],
    },
    {
      id: "risk",
      navLabel: "Risk detected",
      time: "12:46",
      eyebrow: "Rule-based baseline · risk signal",
      title: "A robot failure compounds the constraint",
      body:
        "With H1 still offline, R3 fails and requires human help. The deterministic briefing flags the assistance backlog while the facility remains strained. This is not a learned-model claim.",
      claim: "detected",
      focusEntity: "R3",
      focusNode: "Z4",
      entityState: "failed",
      facts: [
        { label: "Robot", value: "R3", detail: "human required", tone: "warning" },
        { label: "Signal", value: "assist backlog", detail: "deterministic briefing" },
        { label: "Facility", value: "Strained", detail: "recorded status", tone: "warning" },
      ],
      evidence: [
        referenceEvidence(
          "events.jsonl",
          undefined,
          "kind",
          "robot_failed",
          '{"t_s":24360,"kind":"robot_failed","payload":{"human_required":true,"note":"spontaneous hardware failure","robot_id":"R3"}}',
        ),
        referenceEvidence(
          "briefing output",
          undefined,
          "recommendations[].rule_id",
          "assist_backlog",
          '{"rule_id":"assist_backlog","urgency":"watch","action":"Track the assistance backlog"}',
        ),
      ],
    },
    {
      id: "recommend",
      navLabel: "Action recommended",
      time: "14:00",
      eyebrow: "Recommended · not yet executed",
      title: "Unload R3 at station H1",
      body:
        "Once H1 is restored, the baseline recommends moving R3’s full payload into the handoff. The stated outcome is to turn stranded payload into washable supply and free the robot for collection.",
      claim: "recommended",
      focusEntity: "R3",
      focusNode: "H1",
      entityState: "active",
      facts: [
        { label: "Urgency", value: "Soon", detail: "payload_stranded" },
        { label: "Payload", value: "600 / 600", detail: "R3 recorded state", tone: "warning" },
        { label: "Execution", value: "Unconfirmed", detail: "until an event is recorded" },
      ],
      evidence: [
        referenceEvidence(
          "recommendation output",
          undefined,
          "recommendations[].action",
          "Unload R3 at station H1",
          '{"rule_id":"payload_stranded","urgency":"soon","action":"Unload R3 at station H1","expected_outcome":"Payload becomes washable supply and the robot is freed for collection"}',
        ),
      ],
    },
    {
      id: "task",
      navLabel: "Task state changes",
      time: "14:00+",
      eyebrow: "Simulated task state recorded",
      title: "R3 is sent to the restored handoff",
      body:
        "The replay later records R3 at H1 with a handoff task and a full payload. Recommendation, shield approval, and observed robot state stay visibly separate.",
      claim: "simulated",
      focusEntity: "R3",
      focusNode: "H1",
      entityState: "active",
      facts: [
        { label: "Decision", value: "Send to H1", detail: "shield allowed" },
        { label: "Robot", value: "R3", detail: "recorded at handoff" },
        { label: "Task", value: "Unload", detail: "later replay state", tone: "positive" },
      ],
      evidence: [
        referenceEvidence(
          "decision output",
          undefined,
          "action.name",
          "send_to_handoff:R3",
          '{"action":{"name":"send_to_handoff:R3","shield_allowed":true}}',
        ),
        referenceEvidence(
          "facility_states.jsonl",
          undefined,
          "robots[id=R3].activity",
          "queued_handoff / unloading",
          "Observed later in the supplied replay; load the original bundle to verify exact source lines.",
        ),
      ],
    },
    {
      id: "outcome",
      navLabel: "Outcome",
      time: "22:00",
      eyebrow: "Recorded end-of-replay outcome",
      title: "The shift closes without a stockout",
      body:
        "The terminal replay records 100% service availability, zero stockout minutes, and 16,763 balls processed with three human interventions. The artifacts do not assert that the recommendation alone caused the outcome.",
      claim: "recorded",
      facts: [
        { label: "Service", value: "100%", detail: "availability", tone: "positive" },
        { label: "Stockout", value: "0 min", detail: "terminal metric", tone: "positive" },
        { label: "Assists", value: "3", detail: "cumulative" },
      ],
      evidence: [
        referenceEvidence(
          "facility_states.jsonl",
          undefined,
          "demand.service_availability",
          "1.0",
          '{"meta":{"minute_of_day":1320},"demand":{"service_availability":1.0,"stockout_minutes":0.0}}',
        ),
        referenceEvidence(
          "events.jsonl",
          undefined,
          "kind=human_done",
          "3 records",
          "Cumulative assist count shown by the terminal replay summary.",
        ),
      ],
    },
  ],
};

const JSON_PATHS = {
  t: ["t_s", "meta.t_s", "time", "timestamp"],
  minute: ["meta.minute_of_day", "minute_of_day"],
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getPath(record: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) => {
    if (!isObject(value)) return undefined;
    return value[key];
  }, record);
}

function firstPath(record: unknown, paths: string[]): unknown {
  for (const path of paths) {
    const value = getPath(record, path);
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function compareCodePoints(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function mergeArtifactSelections<T extends { name: string }>(
  loaded: T[],
  selected: T[],
): { files: T[]; startsNewSelection: boolean } {
  const startsNewSelection = selected.some(
    (file) => file.name.toLowerCase() === "events.jsonl",
  );
  const files = [...(startsNewSelection ? [] : loaded), ...selected].sort(
    (a, b) =>
      compareCodePoints(a.name.toLowerCase(), b.name.toLowerCase()),
  );
  return { files, startsNewSelection };
}

function recordTime(record: JsonObject): number | undefined {
  return asNumber(firstPath(record, JSON_PATHS.t));
}

function compareSourcedRecords(a: SourcedRecord, b: SourcedRecord): number {
  return (
    (recordTime(a.value) ?? Number.MAX_SAFE_INTEGER) -
      (recordTime(b.value) ?? Number.MAX_SAFE_INTEGER) ||
    compareCodePoints(a.source, b.source) ||
    a.line - b.line
  );
}

export function parseJsonLines(source: string, text: string): ParseResult {
  const records: SourcedRecord[] = [];
  const issues: ParseIssue[] = [];
  const input = text.replace(/^\uFEFF/, "");
  const encoder = new TextEncoder();
  let cursor = 0;
  let lineNumber = 1;
  let recordCount = 0;

  while (cursor <= input.length) {
    const newline = input.indexOf("\n", cursor);
    const end = newline === -1 ? input.length : newline;
    const line = input.slice(
      cursor,
      end > cursor && input[end - 1] === "\r" ? end - 1 : end,
    );

    if (line.trim()) {
      recordCount += 1;
      if (recordCount > MAX_ARTIFACT_RECORDS) {
        throw new Error(
          `${source} exceeds the ${MAX_ARTIFACT_RECORDS} JSONL record limit`,
        );
      }
      if (encoder.encode(line).byteLength > MAX_ARTIFACT_RECORD_BYTES) {
        throw new Error(
          `${source} line ${lineNumber} exceeds the 256 KiB record limit`,
        );
      }
      try {
        const value = JSON.parse(line) as unknown;
        if (isObject(value)) {
          records.push({ source, line: lineNumber, value });
        } else {
          issues.push({
            source,
            line: lineNumber,
            message: "expected a JSON object",
          });
        }
      } catch {
        issues.push({ source, line: lineNumber, message: "invalid JSON" });
      }
    }

    if (newline === -1) break;
    cursor = newline + 1;
    lineNumber += 1;
  }

  records.sort(compareSourcedRecords);
  return { records, issues };
}

function artifactRecordIssue(source: string, value: JsonObject): string | undefined {
  const normalized = source.toLowerCase();
  if (normalized === "events.jsonl") {
    const time = asNumber(value.t_s);
    if (time === undefined || time < 0) {
      return "event requires non-negative finite t_s";
    }
    if (!asString(value.kind)) return "event requires non-empty kind";
    if (!isObject(value.payload)) return "event requires object payload";
    const episodeSeed = asNumber(value.payload.seed);
    if (
      value.kind === "episode_start" &&
      (!asString(value.payload.scenario) ||
        episodeSeed === undefined ||
        !Number.isInteger(episodeSeed))
    ) {
      return "episode_start requires scenario and integer seed";
    }
    return undefined;
  }
  if (normalized === "facility_states.jsonl") {
    const time = asNumber(getPath(value, "meta.t_s"));
    const minute = asNumber(getPath(value, "meta.minute_of_day"));
    if (time === undefined || time < 0) {
      return "facility state requires non-negative finite meta.t_s";
    }
    if (minute === undefined || minute < 0) {
      return "facility state requires non-negative finite meta.minute_of_day";
    }
    if (Math.abs(time / 60 - minute) > 1e-6) {
      return "facility state clock fields disagree";
    }
    if (!asString(getPath(value, "meta.scenario_name"))) {
      return "facility state requires meta.scenario_name";
    }
    const seed = asNumber(getPath(value, "meta.seed"));
    if (seed === undefined || !Number.isInteger(seed)) {
      return "facility state requires integer meta.seed";
    }
    if (typeof getPath(value, "meta.facility_open") !== "boolean") {
      return "facility state requires boolean meta.facility_open";
    }
    if (!Array.isArray(value.robots)) {
      return "facility state requires canonical robot snapshots";
    }
    if (value.robots.length > MAX_ARTIFACT_NESTED_ITEMS) {
      return `facility state exceeds the ${MAX_ARTIFACT_NESTED_ITEMS} robot limit`;
    }
    if (
      !value.robots.every(
        (robot) =>
          isObject(robot) &&
          asString(robot.robot_id) !== undefined &&
          isCanonicalRobotLocation(robot.location),
      )
    ) {
      return "facility state requires canonical robot snapshots";
    }
    const service = asNumber(getPath(value, "demand.service_availability"));
    const stockout = asNumber(getPath(value, "demand.stockout_minutes"));
    if (
      service === undefined ||
      service < 0 ||
      service > 1 ||
      stockout === undefined ||
      stockout < 0
    ) {
      return "facility state requires valid terminal demand metrics";
    }
    return undefined;
  }
  if (/recommend|briefing/.test(normalized)) {
    const time = recordTime(value);
    if (time === undefined || time < 0) {
      return "advisory record requires a non-negative finite recorded time";
    }
    if (!Array.isArray(value.recommendations)) {
      return "advisory record requires canonical recommendation objects";
    }
    if (value.recommendations.length > MAX_ARTIFACT_NESTED_ITEMS) {
      return `advisory record exceeds the ${MAX_ARTIFACT_NESTED_ITEMS} recommendation limit`;
    }
    if (
      !value.recommendations.every(
        (recommendation) =>
          isObject(recommendation) &&
          asString(recommendation.rule_id) !== undefined &&
          asString(recommendation.action) !== undefined &&
          Array.isArray(recommendation.affected_resources) &&
          recommendation.affected_resources.every(
            (resource) => typeof resource === "string",
          ),
      )
    ) {
      return "advisory record requires canonical recommendation objects";
    }
    return undefined;
  }
  return "unsupported artifact record type";
}

export function parseArtifactJsonLines(source: string, text: string): ParseResult {
  const parsed = parseJsonLines(source, text);
  const records: SourcedRecord[] = [];
  const issues = [...parsed.issues];
  for (const record of parsed.records) {
    const message = artifactRecordIssue(source, record.value);
    if (message) {
      if (message.includes(" exceeds the ")) {
        throw new Error(`${source} line ${record.line}: ${message}`);
      }
      issues.push({ source, line: record.line, message });
    } else {
      records.push(record);
    }
  }
  issues.sort(
    (a, b) => compareCodePoints(a.source, b.source) || a.line - b.line,
  );
  return { records, issues };
}

function parseJsonObject(source: string, text: string): JsonObject {
  if (new TextEncoder().encode(text).byteLength > MAX_ARTIFACT_RECORD_BYTES) {
    throw new Error(`${source} exceeds the 256 KiB record limit`);
  }
  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch {
    throw new Error(`${source} contains invalid JSON`);
  }
  if (!isObject(value)) throw new Error(`${source} must contain a JSON object`);
  return value;
}

export function parseLayoutArtifact(text: string): JsonObject {
  const layout = parseJsonObject("layout.json", text);
  if (layout.schema !== "nxt-range-viewer/layout/v1") {
    throw new Error("layout.json has an unsupported or missing schema");
  }
  const stationCount = Array.isArray(layout.stations) ? layout.stations.length : 0;
  const zoneCount = Array.isArray(layout.zones) ? layout.zones.length : 0;
  if (stationCount + zoneCount > MAX_ARTIFACT_NESTED_ITEMS) {
    throw new Error(
      `layout.json exceeds the ${MAX_ARTIFACT_NESTED_ITEMS} station and zone limit`,
    );
  }
  if (!asString(layout.scenario) || layoutPoints(layout).length === 0) {
    throw new Error("layout.json is missing scenario or usable geometry");
  }
  return layout;
}

export function selectedArtifactIdentityIssues(
  records: Map<string, SourcedRecord[]>,
  layout: JsonObject | undefined,
): string[] {
  const issues: string[] = [];
  const states = records.get("facility_states.jsonl") ?? [];
  const events = records.get("events.jsonl") ?? [];
  const stateScenarios = new Set(
    states.map((state) => asString(getPath(state.value, "meta.scenario_name"))),
  );
  const stateSeeds = new Set(
    states.map((state) => asNumber(getPath(state.value, "meta.seed"))),
  );
  if (stateScenarios.size > 1) {
    issues.push("selected facility states contain multiple scenarios");
  }
  if (stateSeeds.size > 1) {
    issues.push("selected facility states contain multiple seeds");
  }

  const stateScenario = stateScenarios.values().next().value;
  const stateSeed = stateSeeds.values().next().value;
  const layoutScenario = asString(layout?.scenario);
  if (
    stateScenario &&
    layoutScenario &&
    stateScenario !== layoutScenario
  ) {
    issues.push("layout scenario does not match selected facility states");
  }

  const episodeStarts = events.filter(
    (event) => asString(event.value.kind) === "episode_start",
  );
  if (episodeStarts.length > 1) {
    issues.push("selected events contain multiple episode_start records");
  }
  if (
    episodeStarts.some((event) => {
      const eventScenario = asString(getPath(event.value, "payload.scenario"));
      return (
        (stateScenario !== undefined && eventScenario !== stateScenario) ||
        (layoutScenario !== undefined && eventScenario !== layoutScenario)
      );
    })
  ) {
    issues.push("episode_start scenario does not match selected artifacts");
  }
  if (
    stateSeed !== undefined &&
    episodeStarts.some(
      (event) => asNumber(getPath(event.value, "payload.seed")) !== stateSeed,
    )
  ) {
    issues.push("episode_start seed does not match selected facility states");
  }
  return issues;
}

function formatClock(tSeconds: number | undefined, state?: JsonObject): string {
  const minuteFromState = asNumber(firstPath(state, JSON_PATHS.minute));
  const totalMinutes =
    tSeconds !== undefined
      ? Math.round(tSeconds / 60)
      : minuteFromState !== undefined
        ? Math.round(minuteFromState)
        : undefined;
  if (totalMinutes === undefined) return "Sequence";
  const hours = Math.floor(totalMinutes / 60) % 24;
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function prettyToken(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function eventPayload(record: JsonObject): JsonObject {
  const payload = record.payload;
  return isObject(payload) ? payload : {};
}

function eventEntity(record: JsonObject): string | undefined {
  const payload = eventPayload(record);
  return asString(
    firstPath(payload, ["robot_id", "station_id", "zone_id", "entity_id"]),
  );
}

function eventNodeId(payload: JsonObject): string | undefined {
  const zoneId = asString(payload.zone_id);
  if (zoneId) return `zone:${zoneId}`;
  const stationId = asString(payload.station_id);
  return stationId ? `station:${stationId}` : undefined;
}

function evidenceFrom(
  record: SourcedRecord,
  path: string,
  value: unknown,
): Evidence {
  return {
    source: record.source,
    line: record.line,
    path,
    value: typeof value === "string" ? value : JSON.stringify(value),
    raw: JSON.stringify(record.value),
  };
}

function recommendationEvidence(
  match: RecommendationMatch,
  path: string,
  value: string,
): Evidence {
  return {
    source: match.source.source,
    line: match.source.line,
    path,
    value,
    raw: JSON.stringify(match.rec),
  };
}

function nearestState(
  states: SourcedRecord[],
  time: number | undefined,
): SourcedRecord | undefined {
  if (!states.length) return undefined;
  if (time === undefined) return undefined;
  let winner: SourcedRecord | undefined;
  for (const state of states) {
    const stateTime = recordTime(state.value);
    if (stateTime === undefined) continue;
    if (stateTime > time) break;
    winner = state;
  }
  return winner;
}

function recommendationsFrom(record: SourcedRecord): JsonObject[] {
  const candidates = record.value.recommendations;
  if (!Array.isArray(candidates)) return [];
  return candidates.filter(isObject);
}

function recommendationOwner(source: SourcedRecord, rec: JsonObject): string {
  const declared = asString(
    firstPath(rec, [
      "owner",
      "producer.owner",
      "provenance.owner",
    ]),
  );
  if (declared) return declared;
  if (source.source.toLowerCase() === "briefings.jsonl") {
    return "nxt_facility (briefings artifact contract)";
  }
  return "owner not declared by artifact";
}

function recResources(rec: JsonObject): string[] {
  const resources = rec.affected_resources;
  return Array.isArray(resources)
    ? resources.filter((value): value is string => typeof value === "string")
    : [];
}

function collectRecommendations(
  briefings: SourcedRecord[],
): RecommendationMatch[] {
  return briefings
    .flatMap((source) =>
      recommendationsFrom(source).map((rec, index) => ({
        source,
        rec,
        index,
        owner: recommendationOwner(source, rec),
        policyId: asString(rec.policy_id),
      })),
    )
    .sort(
      (a, b) =>
        compareSourcedRecords(a.source, b.source) ||
        a.index - b.index ||
        compareCodePoints(a.owner, b.owner) ||
        compareCodePoints(a.policyId ?? "", b.policyId ?? ""),
    );
}

function layoutPoints(layout: JsonObject): RawMapNode[] {
  const points: RawMapNode[] = [];
  const addPoint = (
    id: string,
    label: string,
    kind: MapNode["kind"],
    position: unknown,
  ) => {
    if (!isObject(position)) return;
    const rawX = asNumber(position.x_m);
    const rawY = asNumber(position.y_m);
    if (rawX === undefined || rawY === undefined) return;
    points.push({ id, label, kind, x: 0, y: 0, rawX, rawY });
  };

  if (isObject(layout.dispenser)) {
    addPoint("dispenser", "Dispenser", "dispenser", layout.dispenser);
  }
  if (isObject(layout.charger)) {
    addPoint("charger", "Charger", "charger", layout.charger.position);
  }
  if (Array.isArray(layout.stations)) {
    layout.stations.filter(isObject).forEach((station) => {
      const id = asString(station.station_id);
      if (id) addPoint(`station:${id}`, id, "station", station.position);
    });
  }
  if (Array.isArray(layout.zones)) {
    layout.zones.filter(isObject).forEach((zone) => {
      const id = asString(zone.zone_id);
      if (id) addPoint(`zone:${id}`, id, "zone", zone.position);
    });
  }
  return points;
}

function normalizeLayout(layout: JsonObject | undefined): MapNode[] {
  if (!layout) return REFERENCE_NODES;
  const points = layoutPoints(layout);
  if (!points.length) return REFERENCE_NODES;

  const xs = points.map((point) => point.rawX);
  const ys = points.map((point) => point.rawY);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spreadX = Math.max(1, maxX - minX);
  const spreadY = Math.max(1, maxY - minY);

  return points.map(({ rawX, rawY, ...point }) => ({
    ...point,
    x: 9 + ((rawX - minX) / spreadX) * 82,
    y: 12 + ((maxY - rawY) / spreadY) * 72,
  }));
}

function stateRobot(state: SourcedRecord | undefined, robotId: string): JsonObject | undefined {
  const robots = state?.value.robots;
  if (!Array.isArray(robots)) return undefined;
  return robots
    .filter(isObject)
    .find((robot) => asString(firstPath(robot, ["robot_id", "id"])) === robotId);
}

function locationNodeId(value: unknown): string | undefined {
  const location = asString(value);
  if (!location) return undefined;
  if (location === "charger" || location === "dispenser") return location;
  return /^(?:zone|station):[^:]+$/.test(location) ? location : undefined;
}

function isCanonicalRobotLocation(value: unknown): boolean {
  return value === "transit" || locationNodeId(value) !== undefined;
}

export function deriveStory(
  records: Map<string, SourcedRecord[]>,
  layout: JsonObject | undefined,
  files: string[],
  warnings: string[] = [],
): StoryData {
  const events = records.get("events.jsonl") ?? [];
  const states = records.get("facility_states.jsonl") ?? [];
  const briefingRecords = Array.from(records.entries())
    .filter(([name]) => /recommend|briefing/i.test(name))
    .flatMap(([, sourceRecords]) => sourceRecords)
    .sort(compareSourcedRecords);

  const operationalKinds = [
    "robot_failed",
    "emergency_stop",
    "station_outage",
    "stockout",
    "zone_closed",
    "battery_depleted",
    "dock_failed",
    "task_switched",
  ];
  const candidates = events.filter((event) =>
    operationalKinds.includes(asString(event.value.kind) ?? ""),
  );
  const focus = candidates[0] ?? events[0];
  if (!focus) {
    throw new Error("events.jsonl contains no readable event records");
  }

  const kind = asString(focus.value.kind) ?? "operational_event";
  const focusTime = recordTime(focus.value);
  const entity = eventEntity(focus.value) ?? "facility";
  const beforeState = nearestState(states, focusTime);
  const recMatches = collectRecommendations(briefingRecords);
  const multipleRecs = recMatches.length > 1;
  const recMatch = recMatches.length === 1 ? recMatches[0] : undefined;
  const displayedRecMatches = recMatches.slice(
    0,
    MAX_RENDERED_EVIDENCE_RECORDS,
  );
  const rec = recMatch?.rec;
  const payload = eventPayload(focus.value);
  const note = asString(firstPath(payload, ["note", "reason", "message"]));
  const focusClock = formatClock(focusTime, beforeState?.value);
  const ruleId = asString(rec?.rule_id);
  const rationale = asString(rec?.rationale);
  const action = asString(rec?.action);
  const expectedOutcome = asString(rec?.expected_outcome);
  const urgency = asString(rec?.urgency);
  const recOwner = recMatch?.owner;
  const recPolicyId = recMatch?.policyId;
  const advisoryClock = recMatch
    ? formatClock(recordTime(recMatch.source.value))
    : multipleRecs
      ? "Multiple"
      : "Incomplete";
  const explicitAiProvenance = [
    firstPath(rec, ["provenance.ai_model", "provenance.model_id", "producer.model_id", "model_id", "ai_model"]),
    firstPath(recMatch?.source.value, ["provenance.ai_model", "provenance.model_id", "producer.model_id", "model_id", "ai_model"]),
  ].some((value) => asString(value) !== undefined);
  const decisionActor = explicitAiProvenance ? "AI" : "Decision layer";
  const robot = stateRobot(beforeState, entity);
  const fleetOperable = asNumber(firstPath(beforeState?.value, ["fleet.operable"]));
  const fleetTotal = asNumber(firstPath(beforeState?.value, ["fleet.total"]));
  const focusNode =
    eventNodeId(payload) ??
    locationNodeId(firstPath(robot, ["location"])) ??
    entity;
  const focusEntityState: StoryStage["entityState"] =
    kind === "station_outage" || kind === "zone_closed"
      ? "offline"
      : ["robot_failed", "emergency_stop", "battery_depleted"].includes(kind)
        ? "failed"
        : undefined;

  const relatedEvents = events.filter((event) => {
    const time = recordTime(event.value);
    const eventKind = asString(event.value.kind) ?? "";
    return (
      event !== focus &&
      focusTime !== undefined &&
      time !== undefined &&
      time >= focusTime &&
      time <= focusTime + 3600 &&
      eventEntity(event.value) === entity &&
      [
        "human_requested",
        "human_arrived",
        "human_done",
        "robot_recovered",
        "task_switched",
        "travel_started",
        "travel_completed",
        "collection_done",
        "unloaded",
      ].includes(eventKind)
    );
  });
  const taskEvidence =
    relatedEvents.find((event) => asString(event.value.kind) === "robot_recovered") ??
    relatedEvents.at(-1);
  const taskKind = taskEvidence
    ? asString(taskEvidence.value.kind) ?? "state_changed"
    : "No matching execution event";
  const taskClock = taskEvidence
    ? formatClock(recordTime(taskEvidence.value), nearestState(states, recordTime(taskEvidence.value))?.value)
    : "Incomplete";
  const taskState = taskEvidence
    ? nearestState(states, recordTime(taskEvidence.value))
    : undefined;
  const taskRobot = stateRobot(taskState, entity);
  const taskFocusNode =
    locationNodeId(firstPath(taskRobot, ["location"])) ?? focusNode;
  const taskEntityState: StoryStage["entityState"] =
    taskKind === "robot_recovered" ? "recovered" : undefined;

  const lastState = states.at(-1);
  const closeMinute = asNumber(getPath(layout, "hours.close_minute"));
  const lastMinute = asNumber(
    firstPath(lastState?.value, JSON_PATHS.minute),
  );
  const lastTime = recordTime(lastState?.value ?? {});
  const finalState =
    lastState &&
    getPath(lastState.value, "meta.facility_open") === false &&
    lastTime !== undefined &&
    (focusTime === undefined || lastTime >= focusTime) &&
    (closeMinute === undefined ||
      (lastMinute !== undefined && lastMinute >= closeMinute))
      ? lastState
      : undefined;
  const finalService = asNumber(
    firstPath(finalState?.value, ["demand.service_availability"]),
  );
  const finalStockout = asNumber(
    firstPath(finalState?.value, ["demand.stockout_minutes"]),
  );
  const assists = events.filter(
    (event) => asString(event.value.kind) === "human_done",
  ).length;
  const outcomeClock = finalState
    ? formatClock(recordTime(finalState.value), finalState.value)
    : "Incomplete";

  const focusTitle =
    kind === "robot_failed"
      ? `${entity} drops out of fleet capacity`
      : kind === "station_outage"
        ? `${entity} becomes unavailable`
        : kind === "stockout"
          ? "The clean-ball buffer reaches zero"
          : `${prettyToken(kind)} changes the operating state`;
  const focusBody = note
    ? `The event stream records ${note}. The map snaps to the recorded entity state; no path or physics is invented.`
    : `The event stream records ${prettyToken(kind)} for ${entity}. The map uses only recorded snapshots and layout geometry.`;

  const stateFacts: Fact[] = [
    { label: "Event", value: prettyToken(kind), detail: focus.source },
    { label: "Entity", value: entity, detail: "recorded identifier", tone: "warning" },
  ];
  if (fleetOperable !== undefined && fleetTotal !== undefined) {
    stateFacts.unshift({
      label: "Fleet ready",
      value: `${fleetOperable} / ${fleetTotal}`,
      detail: "snapshot value",
      tone: fleetOperable < fleetTotal ? "warning" : "positive",
    });
  }

  const stages: StoryStage[] = [
    {
      id: "state",
      navLabel: "Facility changes",
      time: focusClock,
      eyebrow: "Observed state change",
      title: focusTitle,
      body: focusBody,
      claim: "observed",
      focusEntity: entity,
      focusNode,
      entityState: focusEntityState,
      facts: stateFacts,
      evidence: [evidenceFrom(focus, "kind", kind)],
    },
    {
      id: "risk",
      navLabel: multipleRecs
        ? "Advisories recorded"
        : rec
          ? "Risk detected"
          : "Risk unavailable",
      time: advisoryClock,
      eyebrow: rec
        ? explicitAiProvenance
          ? "AI output · explicit provenance"
          : "Decision output · AI provenance absent"
        : multipleRecs
          ? "Advisory outputs kept separate"
        : "Evidence gap",
      title: multipleRecs
        ? "Multiple advisory records remain separate"
        : ruleId
        ? `${decisionActor} records ${prettyToken(ruleId).toLowerCase()}`
        : "No advisory output was supplied",
      body: multipleRecs
        ? "The presentation does not rank, merge, deduplicate, associate, or resolve advisory outputs. The source records remain separate."
        : rec
          ? `${rationale ?? "The artifact records this advisory output."} It is shown independently because the capture contract does not link recommendations to events.`
          : "This stage remains intentionally incomplete because no recommendation or briefing output was supplied.",
      claim: rec || multipleRecs ? "detected" : "unconfirmed",
      focusEntity: entity,
      focusNode,
      facts: [
        { label: "Records", value: String(recMatches.length), detail: "kept separate" },
        { label: "Rule", value: ruleId ?? (multipleRecs ? "Multiple" : "—"), detail: "artifact value" },
        { label: "Owner", value: recOwner ?? (multipleRecs ? "See evidence" : "—"), detail: recPolicyId ? `policy ${recPolicyId}` : "preserved identity" },
      ],
      evidence: multipleRecs
        ? displayedRecMatches.map((match) =>
            recommendationEvidence(
              match,
              `recommendations[${match.index}].rule_id`,
              `${match.owner}${match.policyId ? ` · policy ${match.policyId}` : ""}: ${asString(match.rec.rule_id) ?? "not recorded"}`,
            ),
          )
        : recMatch && ruleId
        ? [evidenceFrom(recMatch.source, "recommendations[].rule_id", ruleId)]
        : [evidenceFrom(focus, "kind", kind)],
      evidenceTotal: multipleRecs ? recMatches.length : undefined,
    },
    {
      id: "recommend",
      navLabel: action
        ? "Action recommended"
        : multipleRecs
          ? "Recommendations separate"
          : "Recommendation unavailable",
      time: advisoryClock,
      eyebrow: action
        ? `${explicitAiProvenance ? "AI recommended" : "Recommended"} · not yet executed`
        : multipleRecs
          ? "Advisory outputs kept separate"
        : "Evidence gap",
      title: multipleRecs
        ? "No recommendation was selected"
        : action ?? "No recommendation action was supplied",
      body: multipleRecs
        ? "Multiple sources remain individually visible; this layer has no selection or conflict-resolution contract."
        : action
          ? `${expectedOutcome ?? "The expected outcome was not recorded."} This advice is not associated with the focus event or with execution.`
          : "The layer will not invent an action when a recommendation output is absent.",
      claim: action ? "recommended" : "unconfirmed",
      focusEntity: entity,
      focusNode,
      facts: [
        { label: "Urgency", value: urgency ? prettyToken(urgency) : "—", detail: "artifact value" },
        { label: "Resources", value: rec ? recResources(rec).join(" + ") || "—" : "—", detail: "explicitly affected" },
        { label: "Execution", value: "Unconfirmed", detail: "until an event is recorded" },
      ],
      evidence: multipleRecs
        ? displayedRecMatches.map((match) =>
            recommendationEvidence(
              match,
              `recommendations[${match.index}].action`,
              `${match.owner}${match.policyId ? ` · policy ${match.policyId}` : ""}: ${asString(match.rec.action) ?? "not recorded"}`,
            ),
          )
        : recMatch && action
        ? [evidenceFrom(recMatch.source, "recommendations[].action", action)]
        : [evidenceFrom(focus, "kind", kind)],
      evidenceTotal: multipleRecs ? recMatches.length : undefined,
    },
    {
      id: "task",
      navLabel: taskEvidence ? "Task state changes" : "Task unavailable",
      time: taskClock,
      eyebrow: taskEvidence ? "Related simulated event recorded" : "Evidence gap",
      title: taskEvidence
        ? `${entity}: ${prettyToken(taskKind).toLowerCase()}`
        : "No matching execution event was found",
      body: taskEvidence
        ? `${relatedEvents.length} related simulated event${relatedEvents.length === 1 ? "" : "s"} follow the focus event. They are temporal evidence, not proof that a recommendation caused execution.`
        : "No related execution evidence was found; the layer does not turn advice into execution.",
      claim: taskEvidence ? "simulated" : "unconfirmed",
      focusEntity: entity,
      focusNode: taskFocusNode,
      entityState: taskEntityState,
      facts: [
        { label: "Entity", value: entity, detail: "recorded identifier" },
        { label: "Transition", value: prettyToken(taskKind), detail: taskEvidence?.source ?? "not recorded", tone: taskEvidence ? "positive" : "warning" },
        { label: "Events", value: String(relatedEvents.length), detail: "within 60 sim min" },
      ],
      evidence: taskEvidence
        ? [evidenceFrom(taskEvidence, "kind", taskKind)]
        : [evidenceFrom(focus, "kind", kind)],
    },
    {
      id: "outcome",
      navLabel: "Outcome",
      time: outcomeClock,
      eyebrow: finalState ? "Recorded simulation outcome" : "Evidence gap",
      title:
        !finalState
          ? "No terminal operating outcome was supplied"
          : finalService === 1 && finalStockout === 0
          ? "The replay closes with full service availability"
          : "The terminal operating outcome",
      body: finalState
        ? "Terminal values are copied from the final facility snapshot. They share a replay with the preceding sequence; no causal claim is added."
        : states.length
          ? "The selected state stream does not contain an evidenced terminal snapshot, so the layer cannot state a terminal outcome."
          : "No facility state stream was supplied, so the layer cannot state a terminal outcome.",
      claim: finalState ? "recorded" : "unconfirmed",
      facts: [
        { label: "Service", value: finalService !== undefined ? `${Math.round(finalService * 100)}%` : "—", detail: "availability", tone: finalService === 1 ? "positive" : "neutral" },
        { label: "Stockout", value: finalStockout !== undefined ? `${Number.isInteger(finalStockout) ? finalStockout : finalStockout.toFixed(1)} min` : "—", detail: "terminal metric", tone: finalStockout === 0 ? "positive" : "warning" },
        { label: "Assists", value: String(assists), detail: "human_done event count" },
      ],
      evidence: finalState
        ? [evidenceFrom(finalState, "demand", finalState.value.demand ?? "missing")]
        : lastState
          ? [evidenceFrom(lastState, "meta", lastState.value.meta ?? "missing")]
          : [evidenceFrom(focus, "kind", kind)],
    },
  ];

  const scenario =
    asString(layout?.scenario) ??
    asString(firstPath(beforeState?.value, ["meta.scenario_name"])) ??
    "identity_unverified";
  const policy = recMatches.length
    ? "selected advisory artifacts · episode identity unverified"
    : "capture identity unverified";

  return {
    title: `${entity} operational sequence`,
    scenario,
    policy,
    sourceMode: "artifacts",
    sourceLabel: "Selected capture artifacts · identity unverified",
    sourceFiles: [...files].sort(compareCodePoints),
    warnings:
      states.length && !finalState
        ? [
            ...warnings,
            "The selected state stream has no evidenced terminal snapshot; outcome remains unavailable.",
          ]
        : warnings,
    layoutMode: layout ? "artifact" : "reference-fallback",
    stages,
    nodes: normalizeLayout(layout),
    focusEntity: entity,
    focusNode,
  };
}

function claimLabel(claim: StoryStage["claim"]): string {
  return {
    observed: "Observed",
    detected: "Decision output",
    recommended: "Recommended",
    simulated: "Simulated task",
    unconfirmed: "Unconfirmed",
    recorded: "Recorded",
  }[claim];
}

function FacilityMap({
  story,
  stageIndex,
}: {
  story: StoryData;
  stageIndex: number;
}) {
  const stage = story.stages[stageIndex];
  const isReference = story.sourceMode === "reference";
  const stageFocusNode =
    !isReference && stage.id === "outcome"
      ? undefined
      : stage.focusNode ?? story.focusNode;
  const stageFocusEntity = stage.focusEntity ?? story.focusEntity;
  const focusNode = story.nodes.find((node) => node.id === stageFocusNode);
  const fallbackNode = story.nodes.find((node) => node.kind === "zone") ?? story.nodes[0];
  const robotAnchor = focusNode ?? (isReference ? fallbackNode : undefined);
  const recovered = stage.entityState === "recovered";
  const failed = stage.entityState === "failed";
  const showRobotFocus = isReference
    ? !["station", "dispenser", "charger"].includes(focusNode?.kind ?? "zone") ||
      stageFocusEntity.startsWith("R")
    : story.layoutMode === "artifact" &&
      Boolean(focusNode) &&
      stageFocusEntity.startsWith("R");
  const mapMode =
    story.layoutMode === "artifact"
      ? "Selected layout · discrete evidence only"
      : story.layoutMode === "reference-fallback"
        ? "Reference layout context · selected layout missing"
        : "Recorded snapshots · no interpolated motion";

  return (
    <div
      className={`facility-map stage-${stage.id}`}
      aria-label={story.layoutMode === "artifact" ? "Selected facility layout" : "Reference facility layout"}
    >
      <div className="map-grid" aria-hidden="true" />
      <div className="range-arc arc-one" aria-hidden="true" />
      <div className="range-arc arc-two" aria-hidden="true" />
      <div className="range-arc arc-three" aria-hidden="true" />
      <div className="map-caption">
        <span>Facility view</span>
        <span className="map-mode">{mapMode}</span>
      </div>

      {story.nodes.map((node) => {
        const isFocus = isReference
          ? node.id === story.focusNode
          : story.layoutMode === "artifact" && node.id === stageFocusNode;
        const style = { "--x": `${node.x}%`, "--y": `${node.y}%` } as CSSProperties;
        return (
          <div
            className={`map-node ${node.kind} ${isFocus ? "focus" : ""} ${isFocus && stage.entityState === "offline" ? "offline" : ""}`}
            key={node.id}
            style={style}
            aria-label={`${node.label}, ${node.kind}`}
          >
            <span className="node-core" />
            <span className="node-label">{node.label}</span>
            {node.kind === "zone" ? <span className="node-value">collection zone</span> : null}
          </div>
        );
      })}

      {robotAnchor && showRobotFocus ? (
        <div
          className={`robot-marker robot-focus ${failed ? "failed" : ""} ${recovered ? "recovered" : ""}`}
          style={{
            "--x": `${Math.min(94, robotAnchor.x + 2.8)}%`,
            "--y": `${Math.max(8, robotAnchor.y - 7)}%`,
          } as CSSProperties}
          aria-label={`${stageFocusEntity} ${failed ? "failed" : recovered ? "recovered" : "recorded"}`}
        >
          <span className="robot-pulse" aria-hidden="true" />
          <span className="robot-dot" />
          <span className="robot-name">{stageFocusEntity}</span>
          <span className="robot-state">{failed ? "failed" : recovered ? "recovered" : "recorded"}</span>
        </div>
      ) : null}

      {isReference ? (
        <>
          <div className="robot-marker robot-one" aria-label="R1 recorded">
            <span className="robot-dot" />
            <span className="robot-name">R1</span>
          </div>
          <div className="robot-marker robot-two" aria-label="R2 recorded">
            <span className="robot-dot" />
            <span className="robot-name">R2</span>
          </div>
        </>
      ) : null}

      {isReference && stage.id === "recommend" ? (
        <div className="action-link" aria-hidden="true">
          <span>TECH ASSIST</span>
        </div>
      ) : null}

      {stage.id === "task" && stage.claim === "simulated" ? (
        <div className="event-toast">
          <span className="event-toast-dot" />
          <div>
            <strong>related_event_recorded</strong>
            <span>{stage.time}</span>
          </div>
        </div>
      ) : null}

      {stage.id === "outcome" && stage.claim === "recorded" ? (
        <div className="outcome-glow" aria-hidden="true" />
      ) : null}

      <div className="map-legend" aria-label="Facility legend">
        <span><i className="legend-dot recorded" /> Recorded</span>
        <span><i className="legend-dot attention" /> Attention</span>
        <span><i className="legend-dot recovered" /> Recovered</span>
      </div>
    </div>
  );
}

function EvidencePanel({
  evidence,
  total = evidence.length,
}: {
  evidence: Evidence[];
  total?: number;
}) {
  const displayedEvidence = evidence.slice(0, MAX_RENDERED_EVIDENCE_RECORDS);
  return (
    <div className="evidence-list">
      {total > displayedEvidence.length ? (
        <p className="evidence-limit">
          Showing the first {displayedEvidence.length} of {total} records.
          Review the selected source files for the complete set.
        </p>
      ) : null}
      {displayedEvidence.map((item, index) => (
        <article className="evidence-record" key={`${item.source}-${item.path}-${index}`}>
          <div className="evidence-meta">
            <span>{item.source}{item.line ? ` · line ${item.line}` : ""}</span>
            <code>{item.path}</code>
          </div>
          <div className="evidence-value">{item.value}</div>
          <pre>{item.raw}</pre>
        </article>
      ))}
    </div>
  );
}

export function ReplayStory({
  initialStory = REFERENCE_STORY,
  initialStageIndex = 0,
  initialEvidenceOpen = false,
}: {
  initialStory?: StoryData;
  initialStageIndex?: number;
  initialEvidenceOpen?: boolean;
} = {}) {
  const [story, setStory] = useState<StoryData>(initialStory);
  const [stageIndex, setStageIndex] = useState(() =>
    Math.max(0, Math.min(initialStory.stages.length - 1, initialStageIndex)),
  );
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [evidenceOpen, setEvidenceOpen] = useState(initialEvidenceOpen);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [loadMessage, setLoadMessage] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const loadedArtifactFiles = useRef<File[]>([]);
  const stage = story.stages[stageIndex];

  const goTo = useCallback((next: number) => {
    setStageIndex(Math.max(0, Math.min(4, next)));
  }, []);

  useEffect(() => {
    if (!playing) return;
    if (stageIndex >= story.stages.length - 1) return;
    const timer = window.setTimeout(
      () => {
        if (stageIndex >= story.stages.length - 2) {
          setStageIndex(story.stages.length - 1);
          setPlaying(false);
        } else {
          setStageIndex((current) => Math.min(current + 1, story.stages.length - 1));
        }
      },
      4600 / speed,
    );
    return () => window.clearTimeout(timer);
  }, [playing, speed, stageIndex, story.stages.length]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, button")) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        goTo(stageIndex + 1);
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        goTo(stageIndex - 1);
      }
      if (event.key === " ") {
        event.preventDefault();
        if (stageIndex === story.stages.length - 1) setStageIndex(0);
        setPlaying((current) => !current);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [goTo, stageIndex, story.stages.length]);

  const progress = useMemo(
    () => `${(stageIndex / (story.stages.length - 1)) * 100}%`,
    [stageIndex, story.stages.length],
  );

  const togglePlayback = () => {
    if (stageIndex === story.stages.length - 1) setStageIndex(0);
    setPlaying((current) => !current);
  };

  const handleArtifactFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (!selectedFiles.length) return;
    try {
      const { files, startsNewSelection } = mergeArtifactSelections(
        loadedArtifactFiles.current,
        selectedFiles,
      );
      if (files.length > MAX_ARTIFACT_FILES) {
        throw new Error(
          `Select no more than ${MAX_ARTIFACT_FILES} artifact files`,
        );
      }
      const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
      if (totalBytes > MAX_ARTIFACT_TOTAL_BYTES) {
        throw new Error("Selected artifacts exceed the 30 MiB bundle limit");
      }
      const recordMap = new Map<string, SourcedRecord[]>();
      const parseIssues: ParseIssue[] = [];
      let totalRecordCount = 0;
      const seenNames = new Set<string>();
      let layout: JsonObject | undefined;
      for (const file of files) {
        const normalizedName = file.name.toLowerCase();
        if (file.size > MAX_ARTIFACT_FILE_BYTES) {
          throw new Error(`${file.name} exceeds the 10 MiB per-file limit`);
        }
        if (seenNames.has(normalizedName)) {
          throw new Error(`Duplicate artifact filename: ${file.name}`);
        }
        seenNames.add(normalizedName);
        const text = await file.text();
        if (normalizedName === "layout.json") {
          layout = parseLayoutArtifact(text);
          continue;
        }
        const isAdvisoryFile =
          /recommend|briefing/.test(normalizedName) &&
          (normalizedName.endsWith(".jsonl") || normalizedName.endsWith(".txt"));
        if (
          normalizedName === "events.jsonl" ||
          normalizedName === "facility_states.jsonl" ||
          isAdvisoryFile
        ) {
          const parsed = parseArtifactJsonLines(file.name, text);
          totalRecordCount += parsed.records.length + parsed.issues.length;
          if (totalRecordCount > MAX_ARTIFACT_TOTAL_RECORDS) {
            throw new Error(
              `Selected artifacts exceed the ${MAX_ARTIFACT_TOTAL_RECORDS} total record limit`,
            );
          }
          recordMap.set(normalizedName, parsed.records);
          parseIssues.push(...parsed.issues);
          continue;
        }
        throw new Error(`Unsupported artifact filename: ${file.name}`);
      }
      if (!recordMap.has("events.jsonl")) {
        throw new Error("Select events.jsonl with the capture files");
      }
      const identityIssues = selectedArtifactIdentityIssues(recordMap, layout);
      const distinctIdentityIssues = [...new Set(identityIssues)];
      if (distinctIdentityIssues.length) {
        throw new Error(distinctIdentityIssues.join("; "));
      }
      const warnings: string[] = [];
      if (parseIssues.length) {
        warnings.push(
          `${parseIssues.length} malformed, non-object, or contract-invalid record${parseIssues.length === 1 ? " was" : "s were"} ignored.`,
        );
      }
      warnings.push(
        "Selected-file scenario and seed checks are not cryptographic proof that every artifact belongs to one episode; policy identity remains unverified.",
      );
      if (!recordMap.has("facility_states.jsonl")) {
        warnings.push("facility_states.jsonl is missing; terminal outcome remains unavailable.");
      }
      if (!layout) {
        warnings.push("layout.json is missing; the recovered reference layout is shown as context only.");
      }
      if (
        !Array.from(recordMap.keys()).some((name) =>
          /recommend|briefing/.test(name),
        )
      ) {
        warnings.push("No recommendation or briefing artifact was supplied.");
      }
      const nextStory = deriveStory(
        recordMap,
        layout,
        files.map((file) => file.name),
        warnings,
      );
      loadedArtifactFiles.current = files;
      setStory(nextStory);
      setStageIndex(0);
      setPlaying(false);
      setEvidenceOpen(false);
      setSourceOpen(false);
      setLoadMessage(
        `${selectedFiles.length} artifact${selectedFiles.length === 1 ? "" : "s"} ${startsNewSelection ? "loaded" : "added"} read-only; ${files.length} total${parseIssues.length ? `; ${parseIssues.length} malformed record${parseIssues.length === 1 ? "" : "s"} ignored` : ""}`,
      );
    } catch (error) {
      setLoadMessage(error instanceof Error ? error.message : "Could not read this bundle");
    } finally {
      event.target.value = "";
    }
  };

  const onStageKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      goTo(index + 1);
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goTo(index - 1);
    }
  };

  return (
    <main className="replay-app">
      <div className="simulation-banner">
        <span>Simulation results</span>
        <p>Placeholder-provenance parameters · not real facility performance</p>
      </div>

      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <div>
            <strong>NXTektal</strong>
            <span>Range Operations</span>
          </div>
        </div>
        <div className="topbar-title">
          <span>Operational replay</span>
          <strong>Story mode</strong>
        </div>
        <div className="topbar-actions">
          <button className="ghost-button" type="button" onClick={() => setSourceOpen((value) => !value)} aria-expanded={sourceOpen}>
            <span className={`source-dot ${story.sourceMode}`} />
            {story.sourceMode === "artifacts" ? "Artifacts loaded" : "Reference source"}
          </button>
          <button className="load-button" type="button" onClick={() => fileInput.current?.click()}>
            Load artifacts
          </button>
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept=".json,.jsonl,.txt"
            multiple
            onChange={handleArtifactFiles}
            aria-label="Load replay artifact files"
          />
        </div>
      </header>

      {sourceOpen ? (
        <section className="source-strip" aria-label="Replay data sources">
          <div>
            <span className="source-strip-label">Source mode</span>
            <strong>{story.sourceLabel}</strong>
          </div>
          <div className="source-files">
            {story.sourceFiles.map((file) => <span key={file}>{file}</span>)}
          </div>
          <p>
            {story.sourceMode === "reference"
              ? "Values are a recovered simulation-reference transcript. Original artifacts are not embedded; load a verified bundle for exact source-line evidence."
              : "Files are parsed in-browser and held read-only. No artifact is uploaded, changed, or written back."}
          </p>
          {story.warnings.length ? (
            <ul className="source-warnings">
              {story.warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          ) : null}
        </section>
      ) : null}

      {loadMessage ? (
        <div className="load-message" role="status">
          <span>{loadMessage}</span>
          <button type="button" onClick={() => setLoadMessage(null)} aria-label="Dismiss message">×</button>
        </div>
      ) : null}

      <section className="replay-heading">
        <div>
          <span className="section-kicker">Investor replay · {story.title}</span>
          <h1>See the operation understand, decide, and recover.</h1>
        </div>
        <div className="replay-meta">
          <span>Scenario</span>
          <strong>{story.scenario}</strong>
          <span>Policy</span>
          <strong>{story.policy}</strong>
        </div>
      </section>

      <nav className="story-rail" aria-label="Operational story stages">
        <div className="rail-line" aria-hidden="true"><i style={{ width: progress }} /></div>
        {story.stages.map((item, index) => {
          const state = index < stageIndex ? "complete" : index === stageIndex ? "active" : "upcoming";
          return (
            <button
              type="button"
              className={`story-step ${state}`}
              key={item.id}
              onClick={() => { setPlaying(false); goTo(index); }}
              onKeyDown={(event) => onStageKeyDown(event, index)}
              aria-current={index === stageIndex ? "step" : undefined}
            >
              <span className="step-number">{index < stageIndex ? "✓" : index + 1}</span>
              <span className="step-copy">
                <strong>{item.navLabel}</strong>
                <small>{item.time}</small>
              </span>
            </button>
          );
        })}
      </nav>

      <section className="story-workspace">
        <div className="viewer-panel">
          <div className="viewer-toolbar">
            <div>
              <span className="live-indicator"><i /> Replay</span>
              <span className="sim-clock">{stage.time}</span>
              <small>sim time</small>
            </div>
            <div className="viewer-toolbar-right">
              <span className={`claim-chip claim-${stage.claim}`}>{claimLabel(stage.claim)}</span>
              <span className="read-only-chip">Read only</span>
            </div>
          </div>

          <FacilityMap story={story} stageIndex={stageIndex} />

          <div className="viewer-facts">
            {stage.facts.map((fact) => (
              <div className={`viewer-fact ${fact.tone ?? "neutral"}`} key={`${stage.id}-${fact.label}`}>
                <span>{fact.label}</span>
                <strong>{fact.value}</strong>
                <small>{fact.detail}</small>
              </div>
            ))}
          </div>
        </div>

        <aside className="narrative-panel" aria-live="polite">
          <div className="narrative-progress">
            <span>Moment {stageIndex + 1} of {story.stages.length}</span>
            <span>{stage.time}</span>
          </div>
          <span className={`narrative-eyebrow stage-color-${stage.id}`}>
            <i /> {stage.eyebrow}
          </span>
          <h2>{stage.title}</h2>
          <p className="narrative-body">{stage.body}</p>

          {stage.id === "recommend" && stage.claim === "recommended" ? (
            <div className="recommendation-card">
              <span>Recommended action</span>
              <strong>{stage.title}</strong>
              <small>Advisory only · awaiting separate simulated task evidence</small>
            </div>
          ) : null}

          {stage.id === "task" ? (
            <div
              className="mini-sequence"
              aria-label={stage.claim === "simulated" ? "Recorded simulated task sequence" : "Task evidence gap"}
            >
              {stage.facts.map((fact, index) => (
                <div key={fact.label}>
                  <span>{fact.label}</span>
                  <i className={stage.claim === "simulated" && index === stage.facts.length - 1 ? "done" : ""} />
                  <strong>{fact.value}</strong>
                </div>
              ))}
            </div>
          ) : null}

          {stage.id === "outcome" && stage.claim === "recorded" ? (
            <div className="outcome-statement">
              <span>Operational outcome</span>
              <strong>Continuity is visible. Causality stays honest.</strong>
            </div>
          ) : null}

          <div className="narrative-spacer" />
          <div className="narrative-actions">
            <button type="button" className="evidence-button" onClick={() => setEvidenceOpen((value) => !value)} aria-expanded={evidenceOpen}>
              <span>Evidence</span>
              <strong>{stage.evidenceTotal ?? stage.evidence.length} record{(stage.evidenceTotal ?? stage.evidence.length) === 1 ? "" : "s"}</strong>
              <i>{evidenceOpen ? "−" : "+"}</i>
            </button>
            {evidenceOpen ? (
              <EvidencePanel
                evidence={stage.evidence}
                total={stage.evidenceTotal}
              />
            ) : null}
          </div>
        </aside>
      </section>

      <section className="playback-bar" aria-label="Story playback controls">
        <button className="play-button" type="button" onClick={togglePlayback} aria-label={playing ? "Pause story" : "Play story"}>
          <span>{playing ? "Ⅱ" : stageIndex === story.stages.length - 1 ? "↻" : "▶"}</span>
        </button>
        <button className="skip-button" type="button" onClick={() => { setPlaying(false); goTo(stageIndex - 1); }} disabled={stageIndex === 0} aria-label="Previous story moment">←</button>
        <div className="playback-track">
          <div className="playback-track-line"><i style={{ width: progress }} /></div>
          {story.stages.map((item, index) => (
            <button
              key={item.id}
              type="button"
              className={index <= stageIndex ? "reached" : ""}
              style={{ left: `${(index / (story.stages.length - 1)) * 100}%` }}
              onClick={() => { setPlaying(false); goTo(index); }}
              aria-label={`Jump to ${item.navLabel}, ${item.time}`}
            ><span>{item.time}</span></button>
          ))}
        </div>
        <button className="skip-button" type="button" onClick={() => { setPlaying(false); goTo(stageIndex + 1); }} disabled={stageIndex === story.stages.length - 1} aria-label="Next story moment">→</button>
        <div className="speed-control" aria-label="Playback speed">
          {[1, 2].map((value) => (
            <button key={value} type="button" className={speed === value ? "active" : ""} onClick={() => setSpeed(value)}>{value}×</button>
          ))}
        </div>
        <div className="keyboard-hint"><kbd>←</kbd><kbd>→</kbd> moments <kbd>space</kbd> play</div>
      </section>

      <footer className="trust-footer">
        <div><span className="trust-mark">✓</span><strong>Presentation layer only</strong></div>
        <p>Replay artifacts in. Story annotations out. FacilityState, RangeSimulation, advisory owners, and replay contracts remain untouched.</p>
        <span>{story.sourceMode === "reference" ? "Reference transcript" : "Artifact-backed"}</span>
      </footer>
    </main>
  );
}
