import type { CollectionExecutionsSnapshot } from "../../lib/collection-executions";
import { Badge, KeyValue } from "../ui";
import { SESSION_STATE_BADGE, simSeconds, utcLabel } from "./shared";

/** The current session on the simulation clock and the service read on the
 * wall clock, kept on separate rows: a fresh read says nothing about whether
 * the simulation is running, and neither grants any execution authority. */
export function ExecutionSessionStrip({
  snapshot,
  stale,
  loading,
  lastReadAtMs,
  nowMs,
  onRetry,
}: {
  snapshot: CollectionExecutionsSnapshot;
  stale: boolean;
  loading: boolean;
  lastReadAtMs: number | null;
  nowMs: number;
  onRetry: () => void;
}) {
  const session = SESSION_STATE_BADGE[snapshot.session_state];
  const age = lastReadAtMs === null ? null : Math.max(0, Math.round((nowMs - lastReadAtMs) / 1000));
  return (
    <div className="exec-strip" aria-label="Execution session and service read">
      <div className="exec-strip-row">
        <Badge tone="sim">SIMULATION</Badge>
        <Badge tone="muted">READ ONLY</Badge>
        <Badge tone={session.tone}>{session.label}</Badge>
        <span className="detail-text">
          Series <span className="mono">{snapshot.series_id}</span> · session <span className="mono">{snapshot.session_id}</span> · round{" "}
          <span className="mono">{snapshot.round_id}</span> (index {snapshot.round_index})
        </span>
      </div>
      <dl className="kv-grid">
        <KeyValue label="Simulation time">
          {simSeconds(snapshot.now_sim_t_s)}
          <small>{utcLabel(snapshot.simulation_time_utc)} · session epoch {utcLabel(snapshot.session_epoch_utc)}</small>
        </KeyValue>
        <KeyValue label="Session horizon">
          ends at {simSeconds(snapshot.session_end_sim_t_s)}
          <small>control interval {snapshot.control_interval_s} s</small>
        </KeyValue>
        <KeyValue label="Service read">
          {utcLabel(snapshot.server_time_utc)}
          <small>service wall clock; read metadata only</small>
        </KeyValue>
        <KeyValue label="Last successful read">
          {age === null ? "none yet" : `${age} s ago`}
          <small>browser wall clock</small>
        </KeyValue>
      </dl>
      {snapshot.session_state === "PAUSED" ? (
        <p className="fineprint" role="status">
          Simulation paused: the simulation clock is frozen at {simSeconds(snapshot.now_sim_t_s)}; service reads may still succeed and records keep their last state.
        </p>
      ) : null}
      {snapshot.session_state === "ENDED" ? <p className="fineprint">The session has ended; no record can still be pending or running.</p> : null}
      <div className="exec-strip-row">
        <Badge tone={stale ? "warn" : "info"}>{stale ? "READ STALE" : "READ FRESH"}</Badge>
        <span className="detail-text">
          A successful read only shows recorded evidence. It grants no execution authority, starts nothing and restarts nothing.
        </span>
        <button type="button" className="btn btn-quiet" onClick={onRetry} disabled={loading}>
          Retry execution read
        </button>
      </div>
      <details className="trace-details">
        <summary>Session digests</summary>
        <p className="fineprint mono">
          engine {snapshot.engine_digest} · config {snapshot.config_digest} · replay {snapshot.replay_digest}
        </p>
      </details>
    </div>
  );
}
