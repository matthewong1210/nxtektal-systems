"use client";

import { useEffect, useRef, useState } from "react";
import { ManagerApiError } from "../../lib/api";
import { createCollectionExecutionsClient, type CollectionExecutionsSnapshot } from "../../lib/collection-executions";
import { Badge, EmptyNote, Section } from "../ui";
import { BindingOnlyCard, ExecutionRecordCard } from "./ExecutionRecordCard";
import { ExecutionSessionStrip } from "./ExecutionSessionStrip";

export const COLLECTION_EXECUTIONS_POLL_MS = 5_000;
/** Wall-clock validity of the last successful read. It is independent of
 * request errors and of the simulation clock: a read that keeps waiting, or
 * a PAUSED session, still lets the health expire. */
export const EXECUTION_READ_EXPIRY_MS = 15_000;
export const COLLECTION_EXECUTION_PANEL_TITLE = "Collection execution · simulated V3 session";

/** The read state of the panel. `nowMs` is stamped by the read loop so the
 * render itself stays pure; ages are shown, never used to decide anything. */
export type ExecutionReadView = {
  data: CollectionExecutionsSnapshot | null;
  loading: boolean;
  error: string | null;
  /** The service has no collection-executions route (404). */
  unavailable: boolean;
  /** The latest response was rejected by the strict contract parser. */
  invalid: boolean;
  lastReadAtMs: number | null;
  nowMs: number;
};

export const INITIAL_EXECUTION_VIEW: ExecutionReadView = {
  data: null, loading: true, error: null, unavailable: false, invalid: false, lastReadAtMs: null, nowMs: 0,
};

/** Pure render of one read state. No control here starts, retries, recovers
 * or stops an execution; the only button re-reads the projection. */
export function CollectionExecutionView({ view, onRetry }: { view: ExecutionReadView; onRetry: () => void }) {
  const data = view.data;
  const age = view.lastReadAtMs === null ? null : Math.max(0, Math.round((view.nowMs - view.lastReadAtMs) / 1000));
  const expired = view.lastReadAtMs !== null && view.nowMs - view.lastReadAtMs > EXECUTION_READ_EXPIRY_MS;
  const stale = data !== null && (view.error !== null || expired);
  return (
    <Section
      title={COLLECTION_EXECUTION_PANEL_TITLE}
      aside={
        <>
          <Badge tone="sim">SIMULATION</Badge>
          <Badge tone="muted">READ ONLY</Badge>
        </>
      }
    >
      <p className="sim-note">Follow one confirmed collection task through the simulated session: admission, durable request, device acceptance, start, collection and unloading.</p>
      <p className="fineprint dispatch-boundary">
        This panel only reads recorded evidence. It cannot start, retry, recover or stop an execution, and a successful read grants no execution authority. Quantities come from the
        simulation ball ledger and are never copied into washing, supply, inventory or manager-recorded outcomes.
      </p>
      {view.unavailable ? (
        <div className="dispatch-service-note" role="status">
          <Badge tone="muted">UNAVAILABLE</Badge>
          <p>
            This service does not provide collection execution evidence. The V3 execution route is not connected on this service; nothing shown on this page is a live execution
            integration.{data ? ` The last valid snapshot, read ${age ?? "?"} s ago, remains below marked stale.` : ""}
          </p>
        </div>
      ) : view.error !== null ? (
        <div className="load-warning" role="alert">
          <Badge tone={data ? "warn" : "bad"}>{data ? "READ STALE" : "OFFLINE"}</Badge> {view.error}{" "}
          {view.invalid ? "The latest response did not pass the collection-execution contract check and was not rendered." : ""}{" "}
          {data ? `Showing the last valid snapshot, read ${age ?? "?"} s ago.` : "No execution evidence is available."}
        </div>
      ) : view.loading && data === null ? (
        <EmptyNote>Loading collection execution evidence…</EmptyNote>
      ) : null}
      {data ? (
        // One display scope per session round: a new round discards this
        // panel's own expanded state and nothing else on the page.
        <div className="exec-body" key={`${data.series_id}:${data.session_id}:${data.round_id}`}>
          <ExecutionSessionStrip snapshot={data} stale={stale} expired={expired} loading={view.loading} lastReadAtMs={view.lastReadAtMs} nowMs={view.nowMs} onRetry={onRetry} />
          <div className="exec-list">
            {data.executions.map((record) => (
              <ExecutionRecordCard key={record.execution_id} record={record} snapshot={data} />
            ))}
            {data.bindings
              .filter((binding) => !data.requests.some((request) => request.binding_id === binding.binding_id))
              .map((binding) => (
                <BindingOnlyCard key={binding.binding_id} binding={binding} />
              ))}
            {data.executions.length === 0 && data.bindings.length === 0 ? <EmptyNote>No execution record or binding exists in this session yet.</EmptyNote> : null}
          </div>
        </div>
      ) : view.error === null && !view.unavailable ? null : (
        <div className="form-actions">
          <button type="button" className="btn" onClick={onRetry} disabled={view.loading}>
            Retry execution read
          </button>
        </div>
      )}
    </Section>
  );
}

const describe = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

/** One serial read loop over the Codex-owned GET-only client. A failed or
 * rejected read keeps the exact previous snapshot; unmount aborts. */
export function CollectionExecutionPanel() {
  const [view, setView] = useState<ExecutionReadView>(INITIAL_EXECUTION_VIEW);
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    const client = createCollectionExecutionsClient((input, init) => fetch(input, init));
    let active = true;
    let pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    const read = async () => {
      if (!active || pending) return;
      clearTimeout(timer);
      pending = true;
      request = new AbortController();
      setView((previous) => ({ ...previous, loading: true, nowMs: Date.now() }));
      try {
        const data = await client.read(request.signal);
        if (active) {
          const now = Date.now();
          setView({ data, loading: false, error: null, unavailable: false, invalid: false, lastReadAtMs: now, nowMs: now });
        }
      } catch (cause) {
        if (active) {
          const status = cause instanceof ManagerApiError ? cause.status : null;
          const code = cause instanceof ManagerApiError ? cause.code : null;
          setView((previous) => ({
            ...previous,
            loading: false,
            error: describe(cause),
            unavailable: status === 404,
            invalid: status !== 404 && code === "invalid_collection_executions",
            nowMs: Date.now(),
          }));
        }
      } finally {
        pending = false;
        if (active) timer = setTimeout(() => void read(), COLLECTION_EXECUTIONS_POLL_MS);
      }
    };
    refresh.current = () => void read();
    void read();
    return () => {
      active = false;
      clearTimeout(timer);
      request?.abort();
      refresh.current = () => {};
    };
  }, []);
  useEffect(() => {
    // The read health expires by itself on the wall clock. One timer per
    // successful read re-stamps the view at the boundary so READ FRESH flips
    // to READ STALE even while a read keeps waiting or the session is PAUSED;
    // the next success re-arms it, and unmount clears it.
    if (view.lastReadAtMs === null) return;
    const delay = Math.max(0, view.lastReadAtMs + EXECUTION_READ_EXPIRY_MS - Date.now() + 1);
    const timer = setTimeout(() => setView((previous) => ({ ...previous, nowMs: Date.now() })), delay);
    return () => clearTimeout(timer);
  }, [view.lastReadAtMs]);
  return (
    <div className="dispatch-shell">
      <CollectionExecutionView view={view} onRetry={() => refresh.current()} />
    </div>
  );
}
