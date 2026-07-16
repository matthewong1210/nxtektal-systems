/** F-T01..F-T04 — time normalization & annual throughput. */

import { Decimal, D } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedLaborTask, ResolvedSite, ResolvedSystem } from "./resolve.js";

/**
 * F-T01 / F-T02 — annual occurrences for a task. Returns null when the task
 * cannot be annualized (task becomes `incomplete`; missing is NOT zero).
 */
export function occurrencesYear(
  task: ResolvedLaborTask,
  site: ResolvedSite,
  trace: TraceCollector,
  warnings: WarningCollector,
): Decimal | null {
  // F-T01: override has priority.
  if (task.occurrences_per_year_override !== null) {
    trace.add("F-T01", task.task_id, [task.occurrences_per_year_override], task.occurrences_per_year_override);
    return task.occurrences_per_year_override;
  }

  const fv = task.frequency_value;

  switch (task.frequency_basis) {
    case "per_day": {
      if (fv === null || site.operating_days_per_year === null) break;
      const v = fv.mul(site.operating_days_per_year);
      trace.add("F-T01", task.task_id, [fv, site.operating_days_per_year], v);
      return v;
    }
    case "per_week": {
      if (fv === null) break;
      const v = fv.mul(site.active_weeks_per_year);
      trace.add("F-T01", task.task_id, [fv, site.active_weeks_per_year], v);
      return v;
    }
    case "per_month": {
      if (fv === null) break;
      const v = fv.mul(12);
      trace.add("F-T01", task.task_id, [fv, 12], v);
      return v;
    }
    case "per_year": {
      if (fv === null) break;
      trace.add("F-T01", task.task_id, [fv], fv);
      return fv;
    }
    case "per_event": {
      if (task.annual_event_count === null) break;
      const v = task.annual_event_count.mul(task.occurrences_per_event);
      trace.add("F-T01", task.task_id, [task.annual_event_count, task.occurrences_per_event], v);
      return v;
    }
    case "interval_hours": {
      // F-T02 — CEILING result is only a suggestion; a confirmed field count
      // must be stored as cycles_per_day_override.
      if (fv === null || fv.isZero()) break;
      let cyclesPerDay: Decimal;
      if (task.cycles_per_day_override !== null) {
        cyclesPerDay = task.cycles_per_day_override;
      } else {
        if (site.operating_hours_per_day === null) break;
        cyclesPerDay = site.operating_hours_per_day.div(fv).ceil();
      }
      if (site.operating_days_per_year === null) break;
      const v = cyclesPerDay.mul(site.operating_days_per_year);
      trace.add("F-T02", task.task_id, [fv, cyclesPerDay, site.operating_days_per_year], v);
      return v;
    }
  }

  warnings.add(
    "incomplete_task",
    task.task_id,
    `Task "${task.task_id}" cannot be annualized (missing frequency/site inputs); marked incomplete. Missing is not zero.`,
  );
  return null;
}

/** F-T03 — annual balls processed. Null when no branch has data. */
export function annualBallsProcessed(
  site: ResolvedSite,
  trace: TraceCollector,
): Decimal | null {
  if (site.annual_balls_processed_override !== null) {
    trace.add("F-T03", null, [site.annual_balls_processed_override], site.annual_balls_processed_override);
    return site.annual_balls_processed_override;
  }
  if (site.annual_baskets_sold !== null && site.avg_balls_per_basket !== null) {
    const v = site.annual_baskets_sold.mul(site.avg_balls_per_basket);
    trace.add("F-T03", null, [site.annual_baskets_sold, site.avg_balls_per_basket], v);
    return v;
  }
  if (
    site.avg_baskets_per_day !== null &&
    site.avg_balls_per_basket !== null &&
    site.operating_days_per_year !== null
  ) {
    const v = site.avg_baskets_per_day
      .mul(site.avg_balls_per_basket)
      .mul(site.operating_days_per_year);
    trace.add(
      "F-T03",
      null,
      [site.avg_baskets_per_day, site.avg_balls_per_basket, site.operating_days_per_year],
      v,
    );
    return v;
  }
  return null;
}

/** F-T04 — annual scheduled robot hours (scheduled, NOT productive, no uptime). */
export function annualRobotScheduledHours(
  robotCount: Decimal | null,
  system: ResolvedSystem,
  site: ResolvedSite,
  trace: TraceCollector,
): Decimal | null {
  if (
    robotCount === null ||
    system.scheduled_robot_hours_per_day === null ||
    site.operating_days_per_year === null
  ) {
    return null;
  }
  const v = robotCount
    .mul(system.scheduled_robot_hours_per_day)
    .mul(site.operating_days_per_year);
  trace.add(
    "F-T04",
    null,
    [robotCount, system.scheduled_robot_hours_per_day, site.operating_days_per_year],
    v,
  );
  return v;
}

export const YEAR_MONTHS = D(12);
