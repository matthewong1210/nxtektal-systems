/**
 * Splits the one coherent Supervisor Snapshot into the shapes the existing
 * panels already consume plus the business-context sections.
 *
 * `splitSnapshot` is pure: the five embedded projections are handed to the
 * existing panels with unchanged prop shapes, so one read is one generation
 * and no panel can ever show a fact from a different snapshot than its
 * neighbours. A snapshot of a foreign schema is refused, never partially read.
 */

import {
  ManagerApiError,
  SUPERVISOR_SNAPSHOT_SCHEMA,
  type Briefing,
  type FixtureInfo,
  type Health,
  type Recommendation,
  type StateProjection,
  type SupervisorSnapshot,
} from "./api";

/** Everything in the snapshot that is not one of the five existing projections. */
export type SupervisorContext = Omit<
  SupervisorSnapshot,
  "health" | "state" | "recommendations" | "briefing" | "fixture"
>;

export interface SplitSnapshot {
  health: Health;
  state: StateProjection;
  recommendations: Recommendation[];
  briefing: Briefing;
  fixture: FixtureInfo;
  supervisor: SupervisorContext;
}

export function splitSnapshot(snapshot: SupervisorSnapshot): SplitSnapshot {
  if (snapshot === null || typeof snapshot !== "object" || snapshot.snapshot_schema !== SUPERVISOR_SNAPSHOT_SCHEMA) {
    throw new ManagerApiError(200, {
      code: "schema_mismatch",
      detail: `expected ${SUPERVISOR_SNAPSHOT_SCHEMA}, got ${String((snapshot as { snapshot_schema?: unknown } | null)?.snapshot_schema)}`,
    });
  }
  const { health, state, recommendations, briefing, fixture, ...supervisor } = snapshot;
  for (const [name, part] of Object.entries({ health, state, briefing, fixture })) {
    if (part === null || typeof part !== "object") {
      throw new ManagerApiError(200, { code: "unreadable_response", detail: `the snapshot has no ${name} projection` });
    }
  }
  if (!Array.isArray(recommendations)) {
    throw new ManagerApiError(200, { code: "unreadable_response", detail: "the snapshot has no recommendations list" });
  }
  return { health, state, recommendations, briefing, fixture, supervisor };
}

/** Inverse of `splitSnapshot`, for tests and fixtures that script the service. */
export function joinSnapshot(parts: SplitSnapshot): SupervisorSnapshot {
  return { ...parts.supervisor, health: parts.health, state: parts.state, recommendations: parts.recommendations, briefing: parts.briefing, fixture: parts.fixture };
}
