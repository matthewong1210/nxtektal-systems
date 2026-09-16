import type { StateProjection } from "../lib/api";
import {
  formatAge,
  formatBalls,
  formatConfidence,
  scenarioClock,
} from "../lib/format";
import { Badge, EmptyNote, KeyValue, Section, type Tone } from "./ui";

function readingTone(status: string | undefined): { tone: Tone; label: string } {
  if (status === "ok") return { tone: "ok", label: "OK" };
  if (status === "stale") return { tone: "warn", label: "STALE" };
  if (status === "missing") return { tone: "bad", label: "MISSING" };
  return { tone: "muted", label: "NO READING" };
}

export function StatePanel({ state }: { state: StateProjection }) {
  if (!state.available || !state.dispenser || !state.envelope) {
    return (
      <Section title="Current Facility State">
        <EmptyNote>
          No admitted facility state has been published yet
          {state.reason ? ` — ${state.reason}` : ""}. This is an explicit
          no-data condition, not zero inventory.
        </EmptyNote>
      </Section>
    );
  }
  const dispenser = state.dispenser;
  // The service reports the worst of the count/sensed channel statuses,
  // so a stale sensed reading is never masked by a fresh count.
  const reading = readingTone(
    dispenser.reading_status ?? dispenser.count_source?.status,
  );
  const report = state.quality?.assembly_report ?? null;
  const quality = state.quality?.runtime_quality ?? null;
  return (
    <Section
      title="Current Facility State"
      aside={<Badge tone={reading.tone}>reading {reading.label}</Badge>}
    >
      <div className="inventory-hero">
        <div className="inventory-count">
          <span className="inventory-number mono">
            {formatBalls(dispenser.clean_available_balls)}
          </span>
          <span className="inventory-unit">clean balls in dispenser</span>
        </div>
        <dl className="kv-grid">
          <KeyValue label="Sensed reading">
            {formatBalls(dispenser.clean_sensed_balls)} balls
          </KeyValue>
          <KeyValue label="Observed at">
            {scenarioClock(state.envelope.observation_timestamp_s)} scenario
            time · sequence {state.envelope.sequence_number}
          </KeyValue>
          <KeyValue label="Reading age">
            {formatAge(dispenser.reading_age_s)}
          </KeyValue>
          <KeyValue label="Effective confidence">
            {formatConfidence(quality?.effective_confidence)}
          </KeyValue>
          <KeyValue label="Assembly grade">
            {report?.provenance_grade ?? "unknown"}
          </KeyValue>
          <KeyValue label="Calibration">
            <span className="mono">
              {dispenser.count_source?.calibration_id ?? "—"}
            </span>
          </KeyValue>
        </dl>
      </div>
      {report && report.missing_channels.length > 0 ? (
        <p className="inline-flag flag-bad">
          <Badge tone="bad">MISSING</Badge> {report.missing_channels.length}{" "}
          channel(s): {report.missing_channels.join(", ")}
        </p>
      ) : null}
      {report && report.stale_channels.length > 0 ? (
        <p className="inline-flag flag-warn">
          <Badge tone="warn">STALE</Badge> {report.stale_channels.length}{" "}
          channel(s): {report.stale_channels.join(", ")}
        </p>
      ) : null}
      <p className="fineprint">
        Envelope <span className="mono">{state.envelope.envelope_id}</span>
      </p>
    </Section>
  );
}
