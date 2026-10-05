/**
 * Request-state controller for the Manager Console's fixture panels.
 *
 * The console owns no truth: every view is rebuilt from the Manager API.
 * What it must own is *which* response is allowed to become the view.
 * Each read is tagged with a generation; a change (manager response or
 * fixture control) advances the generation before any network work, so
 * a read that was already in flight can no longer commit data, error,
 * or loading state once a newer read exists. The rules are:
 *
 * - Initial read, manual refresh, and post-change refresh follow the
 *   same generation rule; only the current generation commits.
 * - A change keeps the console busy from its request through the
 *   refresh that follows it and the commit of that refreshed view.
 *   Repeated invocations while busy are inert; a second change can
 *   neither interleave nor reuse stale control state.
 * - A failed current read keeps the last good view visible, marks it
 *   stale (`error`), and refuses changes until a read succeeds. A late
 *   result of an obsolete read can neither clear nor set staleness.
 * - A change whose outcome is unknown (transport failure or a 5xx
 *   envelope) is reported as uncertain, never retried, and never
 *   reported as either success or "not submitted"; the refresh that
 *   follows shows the service's current state.
 * - Stopping the controller (component unmount) invalidates everything
 *   in flight; nothing is published afterwards.
 */

import { isAmbiguousMutation } from "./task-ops";

export interface ConsoleView<T> {
  /** Last successfully committed snapshot; stays visible when a later read fails. */
  data: T | null;
  /** A read for the current generation is in flight. */
  loading: boolean;
  /** A change (request → refresh → commit) is in flight. */
  busy: boolean;
  /** The latest current read failed; `data` is stale until a read succeeds. */
  error: string | null;
  /** The last change returned an uncertain outcome and was not retried. */
  notice: string | null;
}

export interface ConsoleController {
  start(): void;
  stop(): void;
  refresh(): Promise<void>;
  /** `requireCurrent: false` lets a recovery read-and-replay run over a stale
   * or empty view; it still needs an active, idle controller. */
  mutate(operation: () => Promise<unknown>, options?: { requireCurrent?: boolean }): Promise<void>;
}

export const UNCERTAIN_OUTCOME_NOTICE =
  "The last change returned an uncertain outcome and was not retried. " +
  "Check the refreshed status below before responding again.";

export function initialConsoleView<T>(): ConsoleView<T> {
  return { data: null, loading: true, busy: false, error: null, notice: null };
}

/** Changes are allowed only over a committed, current, idle view. A read
 * in flight does not block a change: the change invalidates that read. */
export const canMutateConsole = <T,>(view: ConsoleView<T>): boolean =>
  view.data !== null && !view.busy && view.error === null;

const describe = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause);

export function createConsoleController<T>(
  read: () => Promise<T>,
  publish: (view: ConsoleView<T>) => void,
): ConsoleController {
  let view = initialConsoleView<T>();
  let active = false;
  let generation = 0;
  let current: { generation: number; promise: Promise<void> } | null = null;

  const emit = (patch: Partial<ConsoleView<T>>) => {
    view = { ...view, ...patch };
    if (active) publish(view);
  };

  /** Start (or join) the read for the current generation. Never rejects. */
  function load(): Promise<void> {
    if (!active) return Promise.resolve();
    if (current && current.generation === generation) return current.promise;
    const entry = { generation, promise: Promise.resolve() };
    current = entry;
    if (!view.loading) emit({ loading: true });
    entry.promise = (async () => {
      try {
        const data = await read();
        if (active && generation === entry.generation) {
          emit({ data, error: null, loading: false });
        }
      } catch (cause) {
        if (active && generation === entry.generation) {
          emit({ error: describe(cause), loading: false });
        }
      } finally {
        if (current === entry) current = null;
      }
    })();
    return entry.promise;
  }

  return {
    start() {
      active = true;
      emit({});
      void load();
    },
    stop() {
      active = false;
      generation += 1;
      current = null;
    },
    refresh() {
      // While a change is in flight its own refresh follows; a manual
      // refresh must not add a read that could commit before it.
      if (!active || view.busy) return Promise.resolve();
      return load();
    },
    async mutate(operation, options = {}) {
      if (!active) throw new Error("The console view is not connected.");
      if (view.busy) return; // a repeated click during an in-flight change is inert
      if (options.requireCurrent !== false && !canMutateConsole(view)) {
        throw new Error(
          "Changes are disabled until the console view is refreshed successfully.",
        );
      }
      generation += 1; // any read already in flight can no longer commit
      emit({ busy: true, notice: null });
      let failure: { cause: unknown } | null = null;
      try {
        await operation();
      } catch (cause) {
        failure = { cause };
        if (isAmbiguousMutation(cause)) emit({ notice: UNCERTAIN_OUTCOME_NOTICE });
      }
      try {
        await load();
      } finally {
        emit({ busy: false });
      }
      if (failure) throw failure.cause;
    },
  };
}
