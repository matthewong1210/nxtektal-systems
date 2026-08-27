import type { BriefingException } from "../lib/api";
import { Badge, EmptyNote, Section, type Tone } from "./ui";

function toneFor(tag: string): Tone {
  if (tag === "STALE") return "warn";
  if (tag === "MISSING") return "bad";
  return "muted";
}

function describe(exception: BriefingException): string {
  switch (exception.kind) {
    case "rejected_cycle":
      return `Cycle rejected before publication (${exception.failure_code}) — ${
        exception.cycle_label ?? "fixture cycle"
      }${exception.scenario_time ? ` at ${exception.scenario_time}` : ""}`;
    case "missing_channel":
      return `Channel missing from the latest admitted state: ${exception.channel}`;
    case "stale_channel":
      return `Channel stale in the latest admitted state: ${exception.channel}`;
    case "service_degraded":
      return `Service degraded${exception.detail ? ` — ${exception.detail}` : ""}`;
    case "service_failed":
      return `Service failed${exception.detail ? ` — ${exception.detail}` : ""}`;
    default:
      return exception.kind;
  }
}

export function ExceptionsPanel({
  exceptions,
}: {
  exceptions: BriefingException[];
}) {
  return (
    <Section
      title="Exceptions"
      aside={
        exceptions.length === 0 ? (
          <Badge tone="ok">NONE</Badge>
        ) : (
          <Badge tone="warn">{exceptions.length} OPEN</Badge>
        )
      }
    >
      {exceptions.length === 0 ? (
        <EmptyNote>
          No missing inputs, stale readings, rejected cycles, or service
          degradation in this shift.
        </EmptyNote>
      ) : (
        <ul className="exception-list">
          {exceptions.map((exception, index) => (
            <li key={`${exception.kind}-${index}`}>
              <Badge tone={toneFor(exception.tag)}>{exception.tag}</Badge>
              <span>{describe(exception)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
