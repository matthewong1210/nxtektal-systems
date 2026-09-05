/** Small deterministic formatting helpers (scenario time, never wall clock). */

export function scenarioClock(tS: number | null | undefined): string {
  if (tS === null || tS === undefined || !Number.isFinite(tS) || tS < 0) {
    return "—";
  }
  const minutes = Math.floor(tS / 60);
  const hh = String(Math.floor(minutes / 60) % 24).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function formatAge(seconds: number | null | undefined): string {
  if (
    seconds === null ||
    seconds === undefined ||
    !Number.isFinite(seconds) ||
    seconds < 0
  ) {
    return "unknown";
  }
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "unknown";
  }
  return `${Math.round(value * 100)}%`;
}

export function formatIsoTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /T(\d{2}:\d{2})/.exec(iso);
  return match ? `${match[1]}Z` : iso;
}

export function shortId(id: string | null | undefined, keep = 14): string {
  if (!id) return "—";
  return id.length <= keep ? id : `${id.slice(0, keep)}…`;
}

export function formatBalls(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}
