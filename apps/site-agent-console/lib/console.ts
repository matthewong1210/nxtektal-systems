/**
 * The page-level read and action bindings for the Manager Console.
 *
 * `readConsole` is the single consistent read behind the fixture panels;
 * `createConsoleActions` binds the typed client to a console controller
 * exactly as `app/page.tsx` mounts it, so tests can exercise the same
 * request path the page uses.
 */

import type { ConsoleController } from "./actions";
import type {
  Briefing,
  FixtureInfo,
  Health,
  ManagerApiClient,
  Recommendation,
  RespondInput,
  StateProjection,
} from "./api";

export interface ConsoleData {
  health: Health;
  state: StateProjection;
  recommendations: Recommendation[];
  briefing: Briefing;
  fixture: FixtureInfo;
}

/** One consistent read of every panel's projection. A single failing
 * endpoint fails the whole read so a partially updated view is never
 * committed; the last good view stays visible instead. */
export async function readConsole(client: ManagerApiClient): Promise<ConsoleData> {
  const [health, state, recommendations, briefing, fixture] = await Promise.all([
    client.health(),
    client.state(),
    client.recommendations(),
    client.briefing(),
    client.fixture(),
  ]);
  return { health, state, recommendations, briefing, fixture };
}

export interface ConsoleActions {
  refresh: () => Promise<void>;
  respond: (
    recommendationId: string,
    kind: "accept" | "reject" | "modify",
    input: RespondInput,
  ) => Promise<void>;
  advance: () => Promise<void>;
  restart: () => Promise<void>;
  reset: () => Promise<void>;
}

export function createConsoleActions(
  client: ManagerApiClient,
  controller: () => ConsoleController | null,
): ConsoleActions {
  const mutate = async (operation: () => Promise<unknown>) => {
    const active = controller();
    if (!active) throw new Error("The console view is not connected.");
    await active.mutate(operation);
  };
  return {
    refresh: async () => {
      await controller()?.refresh();
    },
    // Manager responses stay human workflow evidence: this path records a
    // decision in the existing ledger and is not connected to task creation.
    respond: (recommendationId, kind, input) =>
      mutate(() => client.respond(recommendationId, kind, input)),
    advance: () => mutate(() => client.advance()),
    restart: () => mutate(() => client.restart()),
    reset: () => mutate(() => client.reset()),
  };
}
