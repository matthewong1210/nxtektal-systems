/**
 * Lockstep contract revision of the `nxt-staffing/v1` wire.
 *
 * The value is the SHA-256 of a manifest over every source that gives the wire
 * its meaning on both sides (see `simulation/docs/contracts/staffing-v1/lockstep.json`).
 * Every request body the console sends carries it, and every snapshot or receipt
 * the console accepts must carry the same value; a service built from another
 * checkout is refused as `staffing_contract_mismatch`. The pin is regenerated,
 * never edited by hand, with
 * `uv run --no-sync python -B scripts/staffing_contract_revision.py --write`
 * from `simulation/`; this module is outside the manifest so rewriting the pin
 * does not change the value it pins.
 */
export const STAFFING_CONTRACT_REVISION = "0e951f1e3a6f91976a7d41735bd7e5bef398fd4f73f2b552761ee5713682bded";
