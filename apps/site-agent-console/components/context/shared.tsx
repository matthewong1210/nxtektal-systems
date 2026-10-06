/**
 * Presentation helpers for the business operational-context sections.
 *
 * Every value arrives labelled by its owner (PLANNED, SOURCE_RECORDED,
 * DERIVED or UNKNOWN) with the freshness of its source. These helpers only
 * render those labels; nothing is computed, estimated or converted here.
 */
import type { ContextValue, EvidenceLabel, SourceFreshness, UnavailableSection } from "../../lib/api";
import { Badge, type Tone } from "../ui";

export const LABEL_TEXT: Record<EvidenceLabel, { label: string; tone: Tone }> = {
  PLANNED: { label: "PLANNED", tone: "info" },
  SOURCE_RECORDED: { label: "RECORDED", tone: "info" },
  DERIVED: { label: "DERIVED", tone: "muted" },
  UNKNOWN: { label: "UNKNOWN", tone: "warn" },
};

export function sectionStatus(status: string, noun: string): { label: string; tone: Tone } {
  if (status === "ok") return { label: `${noun} SOURCE OK`, tone: "ok" };
  if (status === "stale") return { label: `${noun} SOURCE STALE`, tone: "warn" };
  if (status === "missing") return { label: `NO ${noun} SOURCE`, tone: "bad" };
  if (status === "unavailable") return { label: "UNAVAILABLE", tone: "warn" };
  return { label: status.toUpperCase(), tone: "muted" };
}

export const isUnavailable = (section: { status: string } | null | undefined): section is UnavailableSection =>
  section !== null && section !== undefined && section.status === "unavailable";

/** Counts render as digits; null is a dash, never a zero. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value)} min`;
}

/** A UTC instant rendered in the site's zone as HH:MM, plus the zone name. */
export function formatSiteClock(iso: string | null | undefined, timeZone: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  if (!timeZone) return `${iso.replace(/(\.\d+)?Z$/, "")} UTC`;
  try {
    const text = new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
    return `${text} ${timeZone}`;
  } catch {
    return `${iso.replace(/(\.\d+)?Z$/, "")} UTC`;
  }
}

/** One labelled value: text first, the evidence label beside it, and the
 * reason when nothing trustworthy was recorded. Source staleness is said in
 * words on the row, so a stale number never reads as current. */
export function ValueRow<T>({ label, item, render }: { label: string; item: ContextValue<T> | undefined; render?: (value: T) => string }) {
  if (!item) return null;
  const tag = LABEL_TEXT[item.label] ?? LABEL_TEXT.UNKNOWN;
  const text = item.value === null || item.value === undefined ? "—" : render ? render(item.value) : typeof item.value === "number" ? formatCount(item.value) : String(item.value);
  return (
    <div className="kv context-row">
      <dt>{label}</dt>
      <dd>
        <span className="mono context-value">{text}</span> <Badge tone={tag.tone}>{tag.label}</Badge>
        {item.label === "UNKNOWN" && item.reason ? <span className="detail-text"> · {item.reason}</span> : null}
        {item.source_status === "stale" ? <span className="detail-text"> · source stale</span> : null}
        {item.source_status === "missing" ? <span className="detail-text"> · source missing</span> : null}
      </dd>
    </div>
  );
}

export function SourceLine({ name, source }: { name: string; source: SourceFreshness | undefined }) {
  if (!source) return <p className="fineprint">{name}: no source declared.</p>;
  const age = source.age_s === null ? "age unknown" : `${Math.round(source.age_s)} s old`;
  const coverage = source.coverage_end ? `coverage to ${source.coverage_end.replace(/\.\d+Z$/, "Z")}` : "no coverage";
  return (
    <p className="fineprint">
      {name}: {source.status} · {coverage} ({source.coverage_basis ?? "n/a"}) · {age} · stale after {source.stale_after_s ?? "?"} s · batches accepted {source.accepted_batches}, rejected{" "}
      {source.rejected_batches}, duplicate {source.duplicate_batches}
      {source.rejected_batches > 0 && source.last_rejection ? ` · last rejection: ${source.last_rejection.error_count} error(s), first at row ${source.last_rejection.errors[0]?.row_number ?? "header"} (${source.last_rejection.errors[0]?.reason ?? "unknown"})` : ""}
    </p>
  );
}

export function UnavailableNote({ section, what }: { section: UnavailableSection; what: string }) {
  return (
    <p className="empty-note">
      {what} is unavailable ({section.code}): {section.detail}. Nothing partial is shown; no value here is a zero.
    </p>
  );
}
