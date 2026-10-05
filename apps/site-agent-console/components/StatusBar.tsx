import type { Health } from "../lib/api";
import { formatConfidence } from "../lib/format";
import { Badge, KeyValue, Section, type Tone } from "./ui";

function serviceTone(health: Health): { tone: Tone; label: string } {
  if (health.service_state === "failed") return { tone: "bad", label: "FAILED" };
  if (health.service_state === "stopped")
    return { tone: "muted", label: "STOPPED" };
  if (health.degraded) return { tone: "warn", label: "DEGRADED" };
  return { tone: "ok", label: "SERVING" };
}

export function StatusBar({ health }: { health: Health }) {
  const status = serviceTone(health);
  return (
    <Section
      title="Site / Service Status"
      aside={<Badge tone={status.tone}>{status.label}</Badge>}
    >
      <dl className="kv-grid">
        <KeyValue label="Site">{health.site_id}</KeyValue>
        <KeyValue label="Deployment">{health.deployment_id}</KeyValue>
        <KeyValue label="Workflow">{health.workflow_id}</KeyValue>
        <KeyValue label="Readiness">
          <Badge
            tone={
              health.workflow_readiness === "READY_FOR_FIXTURE_SHADOW_MODE"
                ? "ok"
                : "bad"
            }
          >
            {health.workflow_readiness}
          </Badge>
        </KeyValue>
        <KeyValue label="Mode">
          <Badge tone="sim">{health.mode_label}</Badge>
        </KeyValue>
        <KeyValue label="Source">
          {health.source_type}
          {health.source.exhausted ? " (exhausted)" : ""}
        </KeyValue>
        <KeyValue label="Evidence run">{health.run_directory}</KeyValue>
        <KeyValue label="Sequences">
          <span className="mono">
            observed {health.runtime.last_observed_sequence ?? "—"} ·
            published {health.runtime.last_published_sequence ?? "—"} ·
            evaluated {health.runtime.last_evaluated_sequence ?? "—"}
          </span>
        </KeyValue>
        <KeyValue label="Cycles">
          {health.runtime.cycles_completed} run ·{" "}
          {health.source.cursor
            ? `${health.source.cursor.consumed_cycles}/${health.source.declared_cycles} consumed`
            : "cursor unavailable"}
        </KeyValue>
        <KeyValue label="Pending review">
          {health.pending_recommendation_count}
        </KeyValue>
        <KeyValue label="Last confidence">
          {formatConfidence(health.runtime.last_effective_confidence)}
        </KeyValue>
        {health.last_failure_code ? (
          <KeyValue label="Last failure">
            <Badge tone="bad">{health.last_failure_code}</Badge>{" "}
            <span className="detail-text">{health.last_failure_detail}</span>
          </KeyValue>
        ) : null}
      </dl>
    </Section>
  );
}
