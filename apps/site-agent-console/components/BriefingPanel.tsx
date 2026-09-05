import type { Briefing } from "../lib/api";
import { Badge, EmptyNote, Section, type Tone } from "./ui";

const TAG_TONES: Record<string, Tone> = {
  OBSERVED: "info",
  DETECTED: "info",
  RECOMMENDED: "warn",
  MANAGER_DECISION: "ok",
  MISSING: "bad",
  STALE: "warn",
  SERVICE: "muted",
  SIMULATED: "sim",
};

export function BriefingPanel({ briefing }: { briefing: Briefing }) {
  return (
    <Section
      title="Shift Briefing"
      aside={
        <span className="cycle-tally">
          {briefing.cycles.admitted} admitted · {briefing.cycles.rejected}{" "}
          rejected
        </span>
      }
    >
      <p className="sim-note">
        <Badge tone="sim">SIMULATED</Badge> {briefing.disclaimer}
      </p>
      {briefing.timeline.length === 0 ? (
        <EmptyNote>
          Nothing has happened this shift yet. Advance the fixture to run the
          first cycle.
        </EmptyNote>
      ) : (
        <ol className="timeline">
          {briefing.timeline.map((entry, index) => (
            <li key={index} className="timeline-entry">
              <span className="timeline-time mono">
                {entry.scenario_time ?? "--:--"}
              </span>
              <Badge tone={TAG_TONES[entry.tag] ?? "muted"}>{entry.tag}</Badge>
              <span className="timeline-text">{entry.text}</span>
            </li>
          ))}
        </ol>
      )}
      {briefing.unresolved.length > 0 ? (
        <>
          <h3 className="subhead">Unresolved</h3>
          <ul className="unresolved-list">
            {briefing.unresolved.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </>
      ) : (
        <p className="fineprint">Nothing unresolved at the end of the log.</p>
      )}
    </Section>
  );
}
