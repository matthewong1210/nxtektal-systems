import { describe, expect, test } from "vitest";

import {
  MAX_ARTIFACT_NESTED_ITEMS,
  MAX_ARTIFACT_RECORD_BYTES,
  MAX_ARTIFACT_RECORDS,
  REFERENCE_STORY,
  compareCodePoints,
  deriveStory,
  mergeArtifactSelections,
  parseArtifactJsonLines,
  parseJsonLines,
  parseLayoutArtifact,
  selectedArtifactIdentityIssues,
  type JsonObject,
  type SourcedRecord,
} from "../app/ReplayStory";

function records(name: string, values: JsonObject[]): SourcedRecord[] {
  return parseJsonLines(name, values.map((value) => JSON.stringify(value)).join("\n"))
    .records;
}

const layout: JsonObject = {
  schema: "nxt-range-viewer/layout/v1",
  scenario: "test_scenario",
  hours: { open_minute: 360, close_minute: 505 },
  dispenser: { x_m: 0, y_m: 0 },
  zones: [{ zone_id: "Z4", position: { x_m: 10, y_m: 8 } }],
};

describe("JSONL parsing", () => {
  test("orders deterministically and reports malformed records", () => {
    const parsed = parseJsonLines(
      "events.jsonl",
      [
        JSON.stringify({ t_s: 20, kind: "later" }),
        "{broken",
        JSON.stringify(["not", "an", "object"]),
        JSON.stringify({ t_s: 10, kind: "earlier" }),
        JSON.stringify({ kind: "untimed" }),
      ].join("\n"),
    );

    expect(parsed.records.map((record) => record.value.kind)).toEqual([
      "earlier",
      "later",
      "untimed",
    ]);
    expect(parsed.records.map((record) => record.line)).toEqual([4, 1, 5]);
    expect(parsed.issues).toEqual([
      { source: "events.jsonl", line: 2, message: "invalid JSON" },
      {
        source: "events.jsonl",
        line: 3,
        message: "expected a JSON object",
      },
    ]);
  });

  test("removes a UTF-8 BOM without changing source line evidence", () => {
    const parsed = parseJsonLines("events.jsonl", '\uFEFF{"t_s":1,"kind":"ok"}\n');
    expect(parsed.records).toHaveLength(1);
    expect(parsed.records[0].line).toBe(1);
    expect(parsed.issues).toEqual([]);
  });

  test("rejects over-cardinality JSONL instead of returning a partial replay", () => {
    const jsonl = `${"{}\n".repeat(MAX_ARTIFACT_RECORDS)}{}`;

    expect(() => parseJsonLines("events.jsonl", jsonl)).toThrow(
      `events.jsonl exceeds the ${MAX_ARTIFACT_RECORDS} JSONL record limit`,
    );
  });

  test("rejects an oversized record before JSON parsing", () => {
    const oversized = `{"payload":"${"x".repeat(MAX_ARTIFACT_RECORD_BYTES)}"}`;

    expect(() => parseJsonLines("events.jsonl", oversized)).toThrow(
      "events.jsonl line 1 exceeds the 256 KiB record limit",
    );
  });

  test("rejects oversized nested artifact collections without truncation", () => {
    const oversizedBriefing = JSON.stringify({
      t_s: 21600,
      recommendations: Array.from(
        { length: MAX_ARTIFACT_NESTED_ITEMS + 1 },
        () => ({}),
      ),
    });

    expect(() =>
      parseArtifactJsonLines("briefings.jsonl", oversizedBriefing),
    ).toThrow(
      `briefings.jsonl line 1: advisory record exceeds the ${MAX_ARTIFACT_NESTED_ITEMS} recommendation limit`,
    );
  });

  test("validates the canonical shapes consumed by each artifact adapter", () => {
    const events = parseArtifactJsonLines(
      "events.jsonl",
      [
        JSON.stringify({ t_s: 21600, kind: "robot_failed", payload: { robot_id: "R3" } }),
        JSON.stringify({ kind: "robot_failed", payload: {} }),
        JSON.stringify({ t_s: -1, kind: "robot_failed", payload: {} }),
        JSON.stringify({ t_s: 1, kind: "episode_start", payload: { seed: 7 } }),
      ].join("\n"),
    );
    expect(events.records).toHaveLength(1);
    expect(events.issues.map((issue) => issue.line)).toEqual([2, 3, 4]);

    const states = parseArtifactJsonLines(
      "facility_states.jsonl",
      [
        JSON.stringify({
          meta: { t_s: 21600, minute_of_day: 360, scenario_name: "test_scenario", seed: 7, facility_open: true },
          robots: [{ robot_id: "R3", location: "zone:Z4" }],
          demand: { service_availability: 1, stockout_minutes: 0 },
        }),
        JSON.stringify({
          meta: { t_s: 21600, minute_of_day: 361, scenario_name: "test_scenario", seed: 7, facility_open: true },
          robots: [],
          demand: { service_availability: 1, stockout_minutes: 0 },
        }),
        JSON.stringify({
          meta: { t_s: 21600, minute_of_day: 360, scenario_name: "test_scenario", seed: 7, facility_open: true },
          robots: [],
          demand: {},
        }),
        JSON.stringify({
          meta: { t_s: 21600, minute_of_day: 360, scenario_name: "test_scenario", seed: 7, facility_open: true },
          robots: [{ robot_id: "R3", location: "warpgate:Z4" }],
          demand: { service_availability: 1, stockout_minutes: 0 },
        }),
      ].join("\n"),
    );
    expect(states.records).toHaveLength(1);
    expect(states.issues.map((issue) => issue.message)).toEqual([
      "facility state clock fields disagree",
      "facility state requires valid terminal demand metrics",
      "facility state requires canonical robot snapshots",
    ]);

    const briefings = parseArtifactJsonLines(
      "briefings.jsonl",
      [
        JSON.stringify({
          t_s: 21600,
          recommendations: [
            {
              rule_id: "robot_down",
              action: "Request human assistance",
              affected_resources: ["robot:R3"],
            },
          ],
        }),
        JSON.stringify({ t_s: 21600, recommendations: [{}] }),
        JSON.stringify({ recommendations: [] }),
      ].join("\n"),
    );
    expect(briefings.records).toHaveLength(1);
    expect(briefings.issues).toHaveLength(2);
  });

  test("rejects unknown layout contracts", () => {
    expect(() => parseLayoutArtifact("{}")).toThrow(/unsupported or missing schema/);
    expect(() =>
      parseLayoutArtifact(JSON.stringify({ ...layout, schema: "future/layout/v2" })),
    ).toThrow(/unsupported/);
    expect(parseLayoutArtifact(JSON.stringify(layout))).toEqual(layout);
  });

  test("rejects known mixed selected identities without stream metadata", () => {
    const selected = new Map([
      [
        "facility_states.jsonl",
        records("facility_states.jsonl", [
          {
            meta: { t_s: 21600, minute_of_day: 360, scenario_name: "test_scenario", seed: 7, facility_open: true },
            robots: [],
            demand: { service_availability: 1, stockout_minutes: 0 },
          },
          {
            meta: { t_s: 21660, minute_of_day: 361, scenario_name: "test_scenario", seed: 8, facility_open: true },
            robots: [],
            demand: { service_availability: 1, stockout_minutes: 0 },
          },
        ]),
      ],
      [
        "events.jsonl",
        records("events.jsonl", [
          { t_s: 21600, kind: "episode_start", payload: { scenario: "another_scenario", seed: 9 } },
        ]),
      ],
    ]);

    expect(selectedArtifactIdentityIssues(selected, layout)).toEqual([
      "selected facility states contain multiple seeds",
      "episode_start scenario does not match selected artifacts",
      "episode_start seed does not match selected facility states",
    ]);
  });

  test("rejects concatenated event streams with multiple episode starts", () => {
    const selected = new Map([
      [
        "events.jsonl",
        records("events.jsonl", [
          { t_s: 21600, kind: "episode_start", payload: { scenario: "episode_a", seed: 1 } },
          { t_s: 21660, kind: "episode_start", payload: { scenario: "episode_b", seed: 2 } },
        ]),
      ],
    ]);

    expect(selectedArtifactIdentityIssues(selected, undefined)).toEqual([
      "selected events contain multiple episode_start records",
    ]);
  });

  test("uses locale-independent code-point ordering", () => {
    expect(["é.jsonl", "z.jsonl", "a.jsonl"].sort(compareCodePoints)).toEqual([
      "a.jsonl",
      "z.jsonl",
      "é.jsonl",
    ]);
  });

  test("adds a separately selected briefing in memory and resets on events", () => {
    const captureFiles = [
      { name: "events.jsonl" },
      { name: "facility_states.jsonl" },
      { name: "layout.json" },
    ];
    const first = mergeArtifactSelections([{ name: "stale.jsonl" }], captureFiles);
    expect(first.startsNewSelection).toBe(true);
    expect(first.files.map((file) => file.name)).not.toContain("stale.jsonl");

    const second = mergeArtifactSelections(first.files, [
      { name: "briefings.jsonl" },
    ]);
    expect(second.startsNewSelection).toBe(false);
    expect(second.files.map((file) => file.name)).toEqual([
      "briefings.jsonl",
      "events.jsonl",
      "facility_states.jsonl",
      "layout.json",
    ]);
  });
});

describe("story derivation", () => {
  test("keeps missing optional artifacts as evidence gaps", () => {
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 100, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
      ]),
      undefined,
      ["events.jsonl"],
      ["facility_states.jsonl is missing"],
    );

    expect(story.stages.slice(1).map((stage) => stage.claim)).toEqual([
      "unconfirmed",
      "unconfirmed",
      "unconfirmed",
      "unconfirmed",
    ]);
    expect(story.stages[2].title).toBe("No recommendation action was supplied");
    expect(story.stages.map((stage) => stage.navLabel)).toEqual([
      "Facility changes",
      "Risk unavailable",
      "Recommendation unavailable",
      "Task unavailable",
      "Outcome",
    ]);
    expect(story.stages[3].claim).toBe("unconfirmed");
    expect(story.stages[4].body).toContain("cannot state a terminal outcome");
    expect(story.layoutMode).toBe("reference-fallback");
    expect(story.sourceLabel).toContain("identity unverified");
    expect(story.warnings).toEqual(["facility_states.jsonl is missing"]);
  });

  test("uses absolute simulation seconds and never borrows a future snapshot", () => {
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 21600, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        [
          "facility_states.jsonl",
          records("facility_states.jsonl", [
            {
              meta: { t_s: 22200, minute_of_day: 370, scenario_name: "test_scenario", seed: 7, facility_open: true },
              fleet: { operable: 2, total: 3 },
              robots: [{ robot_id: "R3", location: "zone:Z4" }],
              demand: { service_availability: 1, stockout_minutes: 0 },
            },
          ]),
        ],
      ]),
      undefined,
      ["events.jsonl", "facility_states.jsonl"],
    );

    expect(story.stages[0].time).toBe("06:00");
    expect(story.stages[0].facts.map((fact) => fact.label)).not.toContain(
      "Fleet ready",
    );
  });

  test("keeps a non-terminal final snapshot as an outcome gap", () => {
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 30000, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        [
          "facility_states.jsonl",
          records("facility_states.jsonl", [
            {
              meta: { t_s: 30240, minute_of_day: 504, scenario_name: "test_scenario", seed: 7, facility_open: true },
              robots: [{ robot_id: "R3", location: "transit" }],
              demand: { service_availability: 0.9, stockout_minutes: 1 },
            },
          ]),
        ],
      ]),
      layout,
      ["events.jsonl", "facility_states.jsonl", "layout.json"],
    );

    expect(story.stages[4].claim).toBe("unconfirmed");
    expect(story.stages[4].title).toContain("No terminal");
    expect(story.warnings.join(" ")).toContain("no evidenced terminal snapshot");
  });

  test("preserves zone and station namespaces when raw IDs collide", () => {
    const collisionLayout: JsonObject = {
      schema: "nxt-range-viewer/layout/v1",
      scenario: "test_scenario",
      dispenser: { x_m: 0, y_m: 0 },
      stations: [{ station_id: "A", position: { x_m: 2, y_m: 2 } }],
      zones: [{ zone_id: "A", position: { x_m: 20, y_m: 20 } }],
    };
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 30000, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        [
          "facility_states.jsonl",
          records("facility_states.jsonl", [
            {
              meta: { t_s: 30000, minute_of_day: 500, scenario_name: "test_scenario", seed: 7, facility_open: true },
              robots: [{ robot_id: "R3", location: "zone:A" }],
              demand: { service_availability: 1, stockout_minutes: 0 },
            },
          ]),
        ],
      ]),
      collisionLayout,
      ["events.jsonl", "facility_states.jsonl", "layout.json"],
    );

    expect(story.nodes.map((node) => node.id)).toEqual(
      expect.arrayContaining(["station:A", "zone:A"]),
    );
    expect(story.stages[0].focusNode).toBe("zone:A");
  });

  test("separates recommendation, simulated task evidence, and outcome", () => {
    const eventValues = [
      { t_s: 30000, kind: "robot_failed", payload: { robot_id: "R3" } },
      { t_s: 30100, kind: "human_done", payload: { robot_id: "R3" } },
    ];
    const stateValues = [
      {
        meta: { t_s: 30000, minute_of_day: 500, scenario_name: "test_scenario", seed: 7, facility_open: true },
        fleet: { operable: 2, total: 3 },
        robots: [{ robot_id: "R3", location: "zone:Z4", assigned_zone: "Z4" }],
        demand: { service_availability: 0.9, stockout_minutes: 1 },
      },
      {
        meta: { t_s: 30300, minute_of_day: 505, scenario_name: "test_scenario", seed: 7, facility_open: false },
        robots: [{ robot_id: "R3", location: "zone:Z4", assigned_zone: "Z4" }],
        demand: { service_availability: 1, stockout_minutes: 0 },
      },
    ];
    const briefingValues = [
      {
        t_s: 30000,
        recommendations: [
          {
            rule_id: "robot_down",
            action: "Request human assistance for R3",
            urgency: "now",
            confidence: "high",
            rationale: "R3 is unavailable",
            expected_outcome: "Operator investigates the simulated failure",
            affected_resources: ["robot:R3"],
          },
        ],
      },
    ];

    const story = deriveStory(
      new Map([
        ["events.jsonl", records("events.jsonl", eventValues)],
        ["facility_states.jsonl", records("facility_states.jsonl", stateValues)],
        ["briefings.jsonl", records("briefings.jsonl", briefingValues)],
      ]),
      layout,
      ["layout.json", "events.jsonl", "facility_states.jsonl", "briefings.jsonl"],
      [],
    );

    expect(story.sourceLabel).toBe(
      "Selected capture artifacts · identity unverified",
    );
    expect(story.stages.map((stage) => stage.claim)).toEqual([
      "observed",
      "detected",
      "recommended",
      "simulated",
      "recorded",
    ]);
    expect(story.stages[1].facts[2].value).toContain("nxt_facility");
    expect(story.stages[2].facts[2].value).toBe("Unconfirmed");
    expect(story.stages[3].body).toContain("not proof");
    expect(story.stages[4].body).toContain("no causal claim");
  });

  test("does not select or reconcile multiple advisory outputs", () => {
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 100, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        [
          "briefings.jsonl",
          records("briefings.jsonl", [
            {
              t_s: 100,
              recommendations: [
                {
                  rule_id: "robot_down",
                  action: "Facility recommendation",
                  affected_resources: ["robot:R3"],
                },
                {
                  policy_id: "ball-availability-guardian",
                  rule_id: "robot_down",
                  action: "Shadow recommendation",
                  affected_resources: ["robot:R3"],
                },
              ],
            },
          ]),
        ],
      ]),
      undefined,
      ["events.jsonl", "briefings.jsonl"],
    );

    expect(story.stages[1].title).toBe("Multiple advisory records remain separate");
    expect(story.stages[1].evidence).toHaveLength(2);
    expect(story.stages[1].evidenceTotal).toBe(2);
    expect(JSON.parse(story.stages[1].evidence[0].raw)).toMatchObject({
      rule_id: "robot_down",
    });
    expect(story.stages[1].evidence[0].raw).not.toContain("recommendations");
    expect(story.stages[2].title).toBe("No recommendation was selected");
    expect(story.stages[2].body).toContain("conflict-resolution contract");
  });

  test("caps materialized advisory evidence while retaining the total", () => {
    const recommendations = Array.from({ length: 101 }, (_, index) => ({
      rule_id: `rule_${index}`,
      action: `Action ${index}`,
      affected_resources: [`robot:R${index}`],
    }));
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 30000, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        [
          "briefings.jsonl",
          records("briefings.jsonl", [{ t_s: 30000, recommendations }]),
        ],
      ]),
      undefined,
      ["events.jsonl", "briefings.jsonl"],
    );

    expect(story.stages[1].evidence).toHaveLength(100);
    expect(story.stages[1].evidenceTotal).toBe(101);
    expect(story.stages[2].evidence).toHaveLength(100);
    expect(story.stages[2].evidenceTotal).toBe(101);
  });

  test("consumes an accepted custom briefing filename", () => {
    const customBriefing = records("custom-briefing.jsonl", [
      {
        t_s: 30000,
        recommendations: [
          {
            rule_id: "robot_down",
            action: "Request human assistance",
            affected_resources: ["robot:R3"],
          },
        ],
      },
    ]);
    const story = deriveStory(
      new Map([
        [
          "events.jsonl",
          records("events.jsonl", [
            { t_s: 30000, kind: "robot_failed", payload: { robot_id: "R3" } },
          ]),
        ],
        ["custom-briefing.jsonl", customBriefing],
      ]),
      undefined,
      ["events.jsonl", "custom-briefing.jsonl"],
    );

    expect(story.stages[2].claim).toBe("recommended");
    expect(story.stages[2].evidence[0].source).toBe("custom-briefing.jsonl");
  });

  test("labels the built-in fallback as recovered simulation reference", () => {
    expect(REFERENCE_STORY.sourceMode).toBe("reference");
    expect(REFERENCE_STORY.sourceLabel).toContain("simulation-reference");
    expect(REFERENCE_STORY.warnings.join(" ")).toContain("not embedded");
    expect(REFERENCE_STORY.stages[3].claim).toBe("simulated");
  });
});
