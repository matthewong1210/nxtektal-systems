/**
 * Exclusive action runner for the console's mutating operations.
 *
 * A fixture-control or manager action must keep the console busy for
 * the entire operation — action request → API completion → full
 * refresh → refreshed state committed — so a repeated click during the
 * refresh window can neither reuse stale control state nor trigger a
 * second action. Re-entrant calls while an operation is in flight are
 * ignored outright as a second line of defense behind the disabled
 * buttons.
 */

export type ActionRunner = (action: () => Promise<unknown>) => Promise<void>;

export function createActionRunner(
  setBusy: (busy: boolean) => void,
  refresh: () => Promise<void>,
): ActionRunner {
  let inFlight = false;
  return async (action: () => Promise<unknown>): Promise<void> => {
    if (inFlight) return;
    inFlight = true;
    setBusy(true);
    try {
      await action();
    } finally {
      try {
        await refresh();
      } finally {
        setBusy(false);
        inFlight = false;
      }
    }
  };
}
