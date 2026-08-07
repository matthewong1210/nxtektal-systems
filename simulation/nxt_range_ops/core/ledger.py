"""Integer ball-location ledger enforcing the conservation invariant.

Every ball in the facility is in exactly one location. All movement goes
through :meth:`BallLedger.move`, which is atomic and validates both sides, so
the total is conserved by construction — and :meth:`assert_conserved` lets the
simulation re-verify after every step.

Location naming convention:

* ``dispenser``            — clean balls available to customers
* ``zone:<zone_id>``       — balls lying on the range in a zone
* ``robot:<robot_id>``     — balls in a robot's payload basket
* ``station:<station_id>`` — unloaded balls buffered at a handoff station
* ``washer``               — balls inside the washing/processing loop
"""

from __future__ import annotations

DISPENSER = "dispenser"
WASHER = "washer"


def zone_loc(zone_id: str) -> str:
    return f"zone:{zone_id}"


def robot_loc(robot_id: str) -> str:
    return f"robot:{robot_id}"


def station_loc(station_id: str) -> str:
    return f"station:{station_id}"


class BallConservationError(AssertionError):
    """The total ball count changed — a simulation bug, never user error."""


class BallLedger:
    def __init__(self, locations: list[str], initial: dict[str, int]):
        if len(set(locations)) != len(locations):
            raise ValueError("duplicate ledger locations")
        self._counts: dict[str, int] = {loc: 0 for loc in locations}
        for loc, n in initial.items():
            if loc not in self._counts:
                raise ValueError(f"unknown ledger location {loc!r}")
            if n < 0:
                raise ValueError(f"negative initial count for {loc!r}")
            self._counts[loc] = int(n)
        self._total = sum(self._counts.values())

    @property
    def total(self) -> int:
        return self._total

    def count(self, location: str) -> int:
        return self._counts[location]

    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def move(self, src: str, dst: str, n: int) -> int:
        """Atomically move ``n`` balls (clamped to availability) src -> dst.

        Returns the number actually moved. Negative ``n`` is a bug.
        """
        if n < 0:
            raise ValueError(f"cannot move a negative ball count ({n})")
        if src not in self._counts:
            raise ValueError(f"unknown ledger location {src!r}")
        if dst not in self._counts:
            raise ValueError(f"unknown ledger location {dst!r}")
        moved = min(int(n), self._counts[src])
        self._counts[src] -= moved
        self._counts[dst] += moved
        return moved

    def assert_conserved(self) -> None:
        actual = sum(self._counts.values())
        if actual != self._total:
            raise BallConservationError(
                f"ball conservation violated: total {actual} != {self._total} "
                f"(counts={self._counts})"
            )
        for loc, n in self._counts.items():
            if n < 0:
                raise BallConservationError(f"negative ball count at {loc!r}: {n}")
