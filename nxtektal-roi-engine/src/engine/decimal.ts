import { Decimal } from "decimal.js";

// Fixed configuration for determinism (spec §4, §14 "Determinism", §15.1).
// Same input snapshot + model_version must produce identical numeric output.
Decimal.set({
  precision: 34,
  rounding: Decimal.ROUND_HALF_EVEN,
  toExpNeg: -20,
  toExpPos: 40,
});

export { Decimal };

export type Numeric = Decimal | number | string;

export const D = (x: Numeric): Decimal => (x instanceof Decimal ? x : new Decimal(x));
export const ZERO = new Decimal(0);
export const ONE = new Decimal(1);

/** CLAMP(x, min, max) — spec §4: rates clamp to [0,1] before entering formulas. */
export function clamp(x: Decimal, min: Numeric, max: Numeric): Decimal {
  const lo = D(min);
  const hi = D(max);
  if (x.lt(lo)) return lo;
  if (x.gt(hi)) return hi;
  return x;
}

export const clamp01 = (x: Decimal): Decimal => clamp(x, 0, 1);

/** Division that returns null on a zero denominator (spec §8.6: null, never infinity). */
export function safeDiv(num: Decimal, den: Decimal): Decimal | null {
  if (den.isZero()) return null;
  return num.div(den);
}

/** Compound growth factor (1+rate)^(t-1): year 1 uses base values (F-M01). */
export function growthFactor(rate: Decimal, t: number): Decimal {
  return ONE.add(rate).pow(t - 1);
}

/** Presentation-layer rounding only (spec §4: intermediate results keep full precision). */
export function toMoney(x: Decimal | null): number | null {
  if (x === null) return null;
  const n = Number(x.toDecimalPlaces(2, Decimal.ROUND_HALF_EVEN));
  return n === 0 ? 0 : n; // normalize -0 so output survives JSON round-trips
}

export function toRate(x: Decimal | null, dp = 4): number | null {
  if (x === null) return null;
  const n = Number(x.toDecimalPlaces(dp, Decimal.ROUND_HALF_EVEN));
  return n === 0 ? 0 : n;
}

/** Full-precision string for audit / determinism checks. */
export function toRaw(x: Decimal | null): string | null {
  return x === null ? null : x.toString();
}
