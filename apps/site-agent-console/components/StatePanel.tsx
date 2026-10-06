import type { AssemblyReport, SourceReference, StateProjection } from "../lib/api";
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

/** Worse readings rank higher; an unrecognized or absent status ranks lowest
 * so it can never upgrade a verdict. */
const READING_RANK: Record<string, number> = { ok: 1, stale: 2, missing: 3 };

/** The console's own verdict over every dispenser channel it can see: the
 * worst of the service aggregate, each channel's own status, and the
 * assembly report's missing/stale lists for those channels. It never reads
 * better than the aggregate the service published. */
export function readingVerdict(
  dispenser: NonNullable<StateProjection["dispenser"]>,
  report: AssemblyReport | null,
): { status: string | undefined; aggregate: string | undefined } {
  const aggregate = dispenser.reading_status ?? undefined;
  const statuses: (string | undefined)[] = [aggregate];
  for (const source of [dispenser.count_source, dispenser.sensed_source]) {
    if (!source) continue;
    statuses.push(source.status);
    if (report?.missing_channels.includes(source.channel)) statuses.push("missing");
    if (report?.stale_channels.includes(source.channel)) statuses.push("stale");
  }
  let worst: string | undefined;
  for (const status of statuses) {
    if (status === undefined) continue;
    if ((READING_RANK[status] ?? 0) > (worst === undefined ? 0 : (READING_RANK[worst] ?? 0))) {
      worst = status;
    }
  }
  return { status: worst ?? aggregate, aggregate };
}

const KNOWN_PROVENANCE_GRADES = new Set(["high", "medium", "low"]);

/** The assembler's provenance grade is shown only when it is one the console
 * recognizes. A missing, absent, or unrecognized grade is reported as
 * published but unverified; the enclosing view's staleness never changes it. */
export function provenanceLabel(grade: string | null | undefined): string {
  if (typeof grade === "string" && KNOWN_PROVENANCE_GRADES.has(grade)) return grade;
  return "Published but unverified";
}

/** Per-channel source kind from the channel's own reference. A row that says
 * simulation is simulated whatever the service says; a row that says sensor
 * is a sensor binding, and when the service's observation source is a fixture
 * it is labelled as fixture data, never as a live sensor reading. */
export function sourceTypeBadge(source: SourceReference | null | undefined, fixtureSource = false): { tone: Tone; label: string } {
  if (source?.source_type === "simulation") return { tone: "sim", label: "SIMULATED" };
  if (source?.source_type === "sensor") return fixtureSource ? { tone: "sim", label: "SENSOR BINDING · FIXTURE DATA" } : { tone: "info", label: "SENSOR" };
  if (source?.source_type === "external_system") return fixtureSource ? { tone: "sim", label: "EXTERNAL SYSTEM · FIXTURE DATA" } : { tone: "info", label: "EXTERNAL SYSTEM" };
  return { tone: "muted", label: "SOURCE TYPE UNKNOWN" };
}

export function StatePanel({ state, fixtureSource = false }: { state: StateProjection; fixtureSource?: boolean }) {
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
  const report = state.quality?.assembly_report ?? null;
  const quality = state.quality?.runtime_quality ?? null;
  // The service reports the worst of the count/sensed channel statuses; the
  // console re-derives that verdict from the channels it can see and shows
  // the service aggregate alongside whenever the two disagree.
  const verdict = readingVerdict(dispenser, report);
  const reading = readingTone(verdict.status);
  const aggregate = readingTone(verdict.aggregate);
  const countSource = sourceTypeBadge(dispenser.count_source, fixtureSource);
  const sensedSource = sourceTypeBadge(dispenser.sensed_source, fixtureSource);
  return (
    <Section
      title="Current Facility State"
      aside={
        <>
          <Badge tone={reading.tone}>reading {reading.label}</Badge>
          {verdict.status !== verdict.aggregate ? (
            <span className="detail-text">service aggregate: {aggregate.label}</span>
          ) : null}
        </>
      }
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
            {provenanceLabel(report?.provenance_grade)}
          </KeyValue>
          <KeyValue label="Calibration">
            <span className="mono">
              {dispenser.count_source?.calibration_id ?? "—"}
            </span>
          </KeyValue>
          <KeyValue label="Count source">
            <Badge tone={countSource.tone}>{countSource.label}</Badge>
            {dispenser.count_source ? (
              <span className="detail-text"> {dispenser.count_source.channel}</span>
            ) : null}
          </KeyValue>
          <KeyValue label="Sensed source">
            <Badge tone={sensedSource.tone}>{sensedSource.label}</Badge>
            {dispenser.sensed_source ? (
              <span className="detail-text"> {dispenser.sensed_source.channel}</span>
            ) : null}
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
