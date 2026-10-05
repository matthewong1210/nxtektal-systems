/**
 * Site-timezone wall-clock conversion for planning forms and display.
 *
 * Wire times are always RFC3339 UTC with `Z`; the site timezone from the
 * planning context is display and entry metadata only. Conversions use
 * the runtime's IANA timezone data through Intl; nothing here reads the
 * wall clock.
 *
 * Precision rules: an entered local time converts at the precision it was
 * typed (minutes, seconds, or up to six fractional digits). A stored UTC
 * string is rendered for the entry widget at millisecond precision at
 * most; callers keep the original string and re-emit it verbatim while the
 * rendered value is unchanged (`sameSiteInput`). A local time that occurs
 * twice (daylight-saving fall-back) is refused with both candidate
 * instants instead of silently picking an offset.
 */

const LOCAL_INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/;
const UTC_TEXT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/;

interface WallClock {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

export class SiteTimeAmbiguityError extends Error {
  readonly candidates: [string, string];

  constructor(local: string, timeZone: string, candidates: [string, string]) {
    super(
      `${local} occurs twice in ${timeZone} because of a daylight-saving change ` +
        `(${candidates[0]} or ${candidates[1]}). Enter a time outside the repeated hour, or keep the original value.`,
    );
    this.candidates = candidates;
  }
}

function wallClockIn(instantMs: number, timeZone: string): WallClock {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts: Record<string, number> = {};
  for (const part of formatter.formatToParts(new Date(instantMs))) {
    if (part.type !== "literal") parts[part.type] = Number(part.value);
  }
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour === 24 ? 0 : parts.hour,
    minute: parts.minute,
    second: parts.second,
  };
}

/** Zone offset (ms) at an instant: local wall clock minus UTC. */
function zoneOffsetMs(instantMs: number, timeZone: string): number {
  const wall = wallClockIn(instantMs, timeZone);
  const asUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute, wall.second);
  return asUtc - Math.floor(instantMs / 1000) * 1000;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** RFC3339 UTC text for a whole-second instant plus the fraction digits as typed. */
function utcTextOf(wholeSecondMs: number, fraction: string): string {
  return `${new Date(wholeSecondMs).toISOString().slice(0, 19)}${fraction ? `.${fraction}` : ""}Z`;
}

/** RFC3339 UTC text without fractional seconds, as the contract examples use. */
export function utcText(instantMs: number): string {
  return new Date(instantMs).toISOString().replace(/\.\d{3}Z$/, "Z");
}

interface ParsedUtc {
  wholeSecondMs: number;
  fraction: string;
}

function parseUtc(utc: string): ParsedUtc | null {
  const match = UTC_TEXT.exec(utc);
  if (match) {
    const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
    return { wholeSecondMs: Date.UTC(year, month - 1, day, hour, minute, second), fraction: match[7] ?? "" };
  }
  const parsed = Date.parse(utc);
  if (!Number.isFinite(parsed)) return null;
  const wholeSecondMs = Math.floor(parsed / 1000) * 1000;
  const millis = parsed - wholeSecondMs;
  return { wholeSecondMs, fraction: millis ? String(millis).padStart(3, "0").replace(/0+$/, "") : "" };
}

/** Convert a `datetime-local` value entered as site-local wall clock to UTC,
 * at the precision typed. Nonexistent (spring-forward) times are rejected;
 * repeated (fall-back) times raise `SiteTimeAmbiguityError`. */
export function siteTimeToUtc(value: string, timeZone: string): string {
  const match = LOCAL_INPUT.exec(value);
  if (!match) throw new Error("Choose a complete site-local date and time.");
  const [year, month, day, hour, minute] = match.slice(1, 6).map(Number);
  const second = match[6] === undefined ? 0 : Number(match[6]);
  const fraction = match[7] ?? "";
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  if (!Number.isFinite(guess)) throw new Error("That site-local date and time is not valid.");
  const offsets = new Set([-24, -12, 0, 12, 24].map((hours) => zoneOffsetMs(guess + hours * 3_600_000, timeZone)));
  const instants = new Set<number>();
  for (const offset of offsets) {
    const instant = guess - offset;
    const wall = wallClockIn(instant, timeZone);
    if (
      wall.year === year &&
      wall.month === month &&
      wall.day === day &&
      wall.hour === hour &&
      wall.minute === minute &&
      wall.second === second
    ) {
      instants.add(instant);
    }
  }
  const candidates = [...instants].sort((a, b) => a - b);
  if (candidates.length === 0) {
    throw new Error("That site-local time does not exist. Check the date or a daylight-saving change.");
  }
  if (candidates.length > 1) {
    throw new SiteTimeAmbiguityError(value, timeZone, [utcTextOf(candidates[0], fraction), utcTextOf(candidates[1], fraction)]);
  }
  return utcTextOf(candidates[0], fraction);
}

/** Render a UTC instant as a `datetime-local` value in the site timezone:
 * minutes when the seconds are zero, otherwise seconds and up to three
 * fraction digits (the widget's limit), trailing zeros trimmed. */
export function utcToSiteInput(utc: string, timeZone: string): string {
  const parsed = parseUtc(utc);
  if (parsed === null) throw new Error(`Unreadable UTC time: ${utc}`);
  const wall = wallClockIn(parsed.wholeSecondMs, timeZone);
  const fraction = parsed.fraction.slice(0, 3).replace(/0+$/, "");
  const base = `${wall.year}-${pad(wall.month)}-${pad(wall.day)}T${pad(wall.hour)}:${pad(wall.minute)}`;
  if (wall.second === 0 && fraction === "") return base;
  return `${base}:${pad(wall.second)}${fraction ? `.${fraction}` : ""}`;
}

/** Normalize an entered local value the way `utcToSiteInput` renders one. */
export function canonicalSiteInput(value: string): string | null {
  const match = LOCAL_INPUT.exec(value);
  if (!match) return null;
  const second = match[6] ?? "00";
  const fraction = (match[7] ?? "").slice(0, 3).replace(/0+$/, "");
  const base = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}`;
  if (second === "00" && fraction === "") return base;
  return `${base}:${second}${fraction ? `.${fraction}` : ""}`;
}

/** True when the entered value still shows the stored UTC string unchanged,
 * so the caller may re-emit that string verbatim (seconds, microseconds and
 * daylight-saving offset intact). */
export function sameSiteInput(value: string, originalUtc: string, timeZone: string): boolean {
  try {
    return canonicalSiteInput(value) === utcToSiteInput(originalUtc, timeZone);
  } catch {
    return false;
  }
}

/** Order two RFC3339 UTC strings by real instant, comparing whole seconds
 * and then the fraction digit by digit, so mixed precision never misorders
 * and microseconds are never rounded away. */
export function compareUtc(a: string, b: string): number {
  const left = parseUtc(a);
  const right = parseUtc(b);
  if (left === null || right === null) return a < b ? -1 : a > b ? 1 : 0;
  if (left.wholeSecondMs !== right.wholeSecondMs) return left.wholeSecondMs < right.wholeSecondMs ? -1 : 1;
  const leftMicros = Number(left.fraction.padEnd(6, "0"));
  const rightMicros = Number(right.fraction.padEnd(6, "0"));
  return leftMicros === rightMicros ? 0 : leftMicros < rightMicros ? -1 : 1;
}

/** Display helper: `YYYY-MM-DD HH:MM` in the site timezone, or an em dash. */
export function formatSiteTime(utc: string | null | undefined, timeZone: string): string {
  if (!utc) return "—";
  const parsed = parseUtc(utc);
  if (parsed === null) return utc;
  const wall = wallClockIn(parsed.wholeSecondMs, timeZone);
  return `${wall.year}-${pad(wall.month)}-${pad(wall.day)} ${pad(wall.hour)}:${pad(wall.minute)}`;
}
