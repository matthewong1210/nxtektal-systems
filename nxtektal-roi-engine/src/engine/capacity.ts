/** F-C01..F-C09 — demand, robot capacity, capacity fit. */

import { Decimal, ONE, ZERO, clamp01, safeDiv } from "./decimal.js";
import type { TraceCollector, WarningCollector } from "./trace.js";
import type { ResolvedSite, ResolvedSystem } from "./resolve.js";
import { annualBallsProcessed, annualRobotScheduledHours } from "./time.js";

export interface CapacityComputation {
  annual_balls_processed: Decimal | null;
  avg_daily_ball_demand: Decimal | null;
  peak_daily_ball_demand: Decimal | null;
  target_daily_ball_throughput: Decimal | null;
  operational_collection_rate_bph: Decimal | null;
  operational_daily_capacity_per_robot: Decimal | null;
  required_robot_count: Decimal | null;
  robot_count: Decimal | null;
  daily_capacity_fit: Decimal | null;
  peak_window_capacity_fit: Decimal;
  /** F-C08 final fit — never null; falls back to 1 with an "estimated" warning (§8.2). */
  final_fit: Decimal;
  final_fit_estimated: boolean;
  robot_utilization: Decimal | null;
  annual_robot_scheduled_hours: Decimal | null;
}

export function computeCapacity(
  site: ResolvedSite,
  system: ResolvedSystem,
  trace: TraceCollector,
  warnings: WarningCollector,
): CapacityComputation {
  const annualBalls = annualBallsProcessed(site, trace);

  // F-C01 — average & peak daily demand.
  let avgDaily: Decimal | null = null;
  if (site.avg_baskets_per_day !== null && site.avg_balls_per_basket !== null) {
    avgDaily = site.avg_baskets_per_day.mul(site.avg_balls_per_basket);
  } else if (annualBalls !== null && site.operating_days_per_year !== null) {
    avgDaily = safeDiv(annualBalls, site.operating_days_per_year);
  }

  let peakDaily: Decimal | null = null;
  if (site.peak_daily_balls_override !== null) {
    peakDaily = site.peak_daily_balls_override;
  } else if (site.peak_baskets_per_day !== null && site.avg_balls_per_basket !== null) {
    peakDaily = site.peak_baskets_per_day.mul(site.avg_balls_per_basket);
  }

  let targetThroughput: Decimal | null = null;
  if (peakDaily !== null) {
    targetThroughput = peakDaily.mul(ONE.add(site.safety_buffer_rate));
    trace.add("F-C01", null, [avgDaily, peakDaily, site.safety_buffer_rate], targetThroughput);
  }

  // F-C02 — operational collection rate (no actual_uptime here — §8.2).
  let opRate: Decimal | null = null;
  if (
    system.nominal_collection_rate_bph !== null &&
    system.route_efficiency !== null &&
    system.terrain_efficiency !== null &&
    system.ball_density_efficiency !== null &&
    system.productive_time_fraction !== null
  ) {
    opRate = system.nominal_collection_rate_bph
      .mul(clamp01(system.route_efficiency))
      .mul(clamp01(system.terrain_efficiency))
      .mul(clamp01(system.ball_density_efficiency))
      .mul(clamp01(system.productive_time_fraction));
    trace.add(
      "F-C02",
      null,
      [
        system.nominal_collection_rate_bph,
        system.route_efficiency,
        system.terrain_efficiency,
        system.ball_density_efficiency,
        system.productive_time_fraction,
      ],
      opRate,
    );
  }

  // F-C03 — per-robot daily operational capacity.
  let opDailyCapacity: Decimal | null = null;
  if (opRate !== null && system.scheduled_robot_hours_per_day !== null) {
    opDailyCapacity = opRate.mul(system.scheduled_robot_hours_per_day);
    trace.add("F-C03", null, [opRate, system.scheduled_robot_hours_per_day], opDailyCapacity);
  }

  // F-C04 — design robot count (design_uptime only used for sizing).
  let requiredCount: Decimal | null = null;
  if (
    targetThroughput !== null &&
    opDailyCapacity !== null &&
    system.design_uptime !== null &&
    !opDailyCapacity.mul(system.design_uptime).isZero()
  ) {
    const designCapacity = opDailyCapacity.mul(clamp01(system.design_uptime));
    requiredCount = targetThroughput.div(designCapacity).ceil();
    trace.add("F-C04", null, [targetThroughput, designCapacity], requiredCount);
  }

  // Robot count: input or computed (spec §5.3: "required or computed by formula").
  const robotCount = system.robot_count ?? requiredCount;

  if (requiredCount !== null && robotCount !== null && robotCount.lt(requiredCount)) {
    warnings.add(
      "capacity_warning",
      null,
      `robot_count (${robotCount}) is below required_robot_count (${requiredCount}); calculation proceeds with a capacity warning (§8.2).`,
    );
  }

  // F-C05 — daily capacity fit (no actual uptime — F-L05 applies uptime once).
  let dailyFit: Decimal | null = null;
  if (system.capacity_fit_override !== null) {
    dailyFit = clamp01(system.capacity_fit_override);
    trace.add("F-C05", null, [system.capacity_fit_override], dailyFit);
  } else if (robotCount !== null && opDailyCapacity !== null && targetThroughput !== null) {
    const fleet = robotCount.mul(opDailyCapacity);
    const ratio = safeDiv(fleet, targetThroughput);
    dailyFit = ratio === null ? ONE : Decimal.min(ONE, ratio);
    trace.add("F-C05", null, [fleet, targetThroughput], dailyFit);
  }

  // F-C06 — peak window requirement (Full mode only, needs window data).
  let windowFit = ONE; // no peak window data ⇒ 1 (F-C08 rule)
  if (
    system.peak_hourly_ball_demand !== null &&
    system.replenishment_window_hours !== null &&
    system.peak_window_collection_hours_per_robot !== null &&
    robotCount !== null &&
    opRate !== null
  ) {
    const usableBuffer = Decimal.max(
      ZERO,
      (system.starting_buffer_balls ?? ZERO).sub(system.minimum_buffer_balls ?? ZERO),
    );
    const requiredInWindow = Decimal.max(
      ZERO,
      system.peak_hourly_ball_demand.mul(system.replenishment_window_hours).sub(usableBuffer),
    );
    trace.add(
      "F-C06",
      null,
      [system.peak_hourly_ball_demand, system.replenishment_window_hours, usableBuffer],
      requiredInWindow,
    );

    // F-C07 — window capacity fit (robot_count × per-robot window hours, locked v1.0).
    const windowCapacity = robotCount
      .mul(opRate)
      .mul(system.peak_window_collection_hours_per_robot);
    if (requiredInWindow.isZero()) {
      windowFit = ONE;
    } else {
      windowFit = Decimal.min(ONE, windowCapacity.div(requiredInWindow));
    }
    trace.add("F-C07", null, [windowCapacity, requiredInWindow], windowFit);
  }

  // F-C08 — final fit = MIN(daily, window). §8.2: a missing target throughput
  // must not silently become fit=1 — it falls back to 1 but flagged estimated.
  let finalFit: Decimal;
  let finalFitEstimated = false;
  if (dailyFit !== null) {
    finalFit = Decimal.min(dailyFit, windowFit);
  } else {
    finalFit = windowFit; // = 1 when no window data either
    finalFitEstimated = true;
    warnings.add(
      "capacity_fit_assumed_estimated",
      null,
      "capacity_fit defaulted to 1 because demand/capacity inputs are missing; treat as an ESTIMATE, not a confirmed fit (§8.2).",
    );
  }
  trace.add("F-C08", null, [dailyFit, windowFit], finalFit);

  // F-C09 — utilization (may exceed 1 ⇒ under-provisioned + warning).
  let utilization: Decimal | null = null;
  if (
    targetThroughput !== null &&
    robotCount !== null &&
    opDailyCapacity !== null &&
    system.actual_uptime !== null
  ) {
    const denom = robotCount.mul(opDailyCapacity).mul(clamp01(system.actual_uptime));
    utilization = safeDiv(targetThroughput, denom);
    if (utilization !== null) {
      trace.add("F-C09", null, [targetThroughput, denom], utilization);
      if (utilization.gt(ONE)) {
        warnings.add(
          "capacity_warning",
          null,
          `robot_utilization ${utilization.toFixed(3)} > 1: configuration cannot meet target throughput (§8.2 / F-C09).`,
        );
      }
    }
  }

  const scheduledHours = annualRobotScheduledHours(robotCount, system, site, trace);

  return {
    annual_balls_processed: annualBalls,
    avg_daily_ball_demand: avgDaily,
    peak_daily_ball_demand: peakDaily,
    target_daily_ball_throughput: targetThroughput,
    operational_collection_rate_bph: opRate,
    operational_daily_capacity_per_robot: opDailyCapacity,
    required_robot_count: requiredCount,
    robot_count: robotCount,
    daily_capacity_fit: dailyFit,
    peak_window_capacity_fit: windowFit,
    final_fit: finalFit,
    final_fit_estimated: finalFitEstimated,
    robot_utilization: utilization,
    annual_robot_scheduled_hours: scheduledHours,
  };
}
