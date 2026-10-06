/**
 * The page-level read and action bindings for the Manager Console.
 *
 * `readConsole` is the single consistent read behind the fixture panels:
 * one `GET /api/v0/supervisor-snapshot`, split into the shapes the panels
 * already consume. `createConsoleActions` binds the typed client to a
 * console controller exactly as `app/page.tsx` mounts it, so tests can
 * exercise the same request path the page uses.
 */

import type { ConsoleController } from "./actions";
import type { ManagerApiClient, RespondInput } from "./api";
import { splitSnapshot, type SplitSnapshot } from "./snapshot";

export type ConsoleData = SplitSnapshot;

/** One read, one generation. The whole snapshot is composed by the service
 * under one lock, so every panel below renders the same generation; a
 * failed read commits nothing and the last good view stays visible. */
export async function readConsole(client: ManagerApiClient): Promise<ConsoleData> {
  return splitSnapshot(await client.supervisorSnapshot());
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
