import type { ReactNode } from "react";
import type { Evidence, Selection } from "../../lib/planning";
import { formatSiteTime } from "../../lib/site-time";
import { Badge, type Tone } from "../ui";

export const sourceLabel = (kind: string) => kind.replace(/_/g, " ");

export function statusTone(status: string): Tone {
  if (["READY", "CONFIRMED", "DISPATCHED"].includes(status)) return "ok";
  if (["SCHEDULED", "MISSING_DATA", "INVALIDATED"].includes(status)) return "warn";
  if (["EXPIRED", "INFEASIBLE", "CANCELLED", "REJECTED", "MISSED"].includes(status)) return "bad";
  return "info";
}

export const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

export function EvidenceMeta({ evidence, timeZone }: { evidence: Evidence<unknown, string>; timeZone: string }) {
  return (
    <span className="evidence-meta">
      <Badge tone={evidence.source_kind === "MEASURED" ? "ok" : "info"}>{sourceLabel(evidence.source_kind)}</Badge>{" "}
      <span className="mono">{evidence.source_ref}</span> · observed {formatSiteTime(evidence.observed_at_utc, timeZone)} · valid until{" "}
      {formatSiteTime(evidence.valid_until_utc, timeZone)}
    </span>
  );
}

export function EvidenceRow({
  label,
  evidence,
  timeZone,
  render,
}: {
  label: string;
  evidence: Evidence<unknown, string> | null;
  timeZone: string;
  render: (value: never) => ReactNode;
}) {
  return (
    <div className="kv evidence-row">
      <dt>{label}</dt>
      <dd>
        {evidence === null ? (
          <>
            <Badge tone="warn">UNKNOWN</Badge> not provided
          </>
        ) : (
          <>
            <span className="evidence-value">{render(evidence.value as never)}</span>{" "}
            <EvidenceMeta evidence={evidence} timeZone={timeZone} />
          </>
        )}
      </dd>
    </div>
  );
}

export function SelectionText({ selection, timeZone }: { selection: Selection | null; timeZone: string }) {
  if (selection === null) return <span className="detail-text">none</span>;
  return (
    <span>
      zone <span className="mono">{selection.zone_id}</span> · robot <span className="mono">{selection.robot_id}</span> · start{" "}
      {formatSiteTime(selection.start_at_utc, timeZone)}
    </span>
  );
}

export const yesNo = (value: boolean) => (value ? "Yes" : "No");
