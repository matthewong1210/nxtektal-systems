"""The three V0 reference adapters and the kit that composes them.

Each adapter turns already-read raw device samples into existing
canonical ``nxt_telemetry.observations.Observation`` objects.  No adapter
opens a transport, reads a register, writes an output, plans motion, or
touches an emergency stop; the package has no such surface and cannot
grow one without failing its architecture guards.

Honesty rules enforced here, in one place so they cannot drift:

* A reading that cannot be trusted becomes an explicit ``MISSING``
  Observation on its commissioned channel plus a named
  :class:`~nxt_edge_observation.contracts.RejectedField`.  It never
  becomes an optimistic default, and a missing load-cell reading never
  becomes zero inventory.
* A reading older than its declared staleness horizon becomes ``STALE``
  with capped confidence; it is never re-labelled ``OK``.
* A raw field with no canonical destination is reported as unmapped.  It
  is never dropped, and it never widens ``FacilityState``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from nxt_telemetry.observations import (
    Observation,
    ObservationStatus,
    SourceType,
)

from .bindings import AdapterBinding, AdapterBindingSet
from .channels import ChannelKind, channel_kind, robot_channel
from .contracts import (
    ADAPTER_REPORT_SCHEMA,
    CONFIDENCE_BY_STATUS,
    EDGE_ADAPTER_VERSION,
    AcceptedField,
    DigitalDeviceProfile,
    DigitalInputProfile,
    DigitalIOSnapshot,
    EdgeAdapterError,
    EdgeAdapterReport,
    LoadCellProfile,
    LoadCellSample,
    RawSampleBatch,
    RejectedField,
    RejectionCode,
    RobotStatusProfile,
    RobotStatusSample,
    UnmappedField,
)

# Device self-reports this layer understands.  Anything else is an unknown
# vocabulary and fails closed rather than being read as healthy.
DEVICE_STATUS_OK = "ok"
DEVICE_STATUS_FAULT = "fault"
DEVICE_STATUS_MISSING = "no_data"
_KNOWN_DEVICE_STATUS = frozenset(
    {DEVICE_STATUS_OK, DEVICE_STATUS_FAULT, DEVICE_STATUS_MISSING}
)

_ROBOT_CANONICAL_FIELDS = (
    "activity",
    "health",
    "battery_frac",
    "payload_balls",
    "location",
    "destination",
    "assigned_zone",
    "estop_latched",
    "awaiting_human",
)


class _Collector:
    """Accumulates one batch's observations and diagnostics deterministically."""

    def __init__(self, *, cycle_index: int, frame_t_s: float) -> None:
        self.cycle_index = cycle_index
        self.frame_t_s = frame_t_s
        self.observations: list[Observation] = []
        self.accepted: list[AcceptedField] = []
        self.rejected: list[RejectedField] = []
        self.unmapped: list[UnmappedField] = []
        self._claimed: dict[str, str] = {}  # channel -> sensor_id

    # -- diagnostics ----------------------------------------------------

    def reject(
        self,
        *,
        sensor_id: str,
        channel: str | None,
        raw_field: str,
        code: RejectionCode,
        detail: str,
    ) -> None:
        self.rejected.append(
            RejectedField(
                sensor_id=sensor_id,
                channel=channel,
                raw_field=raw_field,
                code=code,
                detail=detail,
            )
        )

    def unmap(self, *, sensor_id: str, raw_field: str, reason: str) -> None:
        self.unmapped.append(
            UnmappedField(sensor_id=sensor_id, raw_field=raw_field, reason=reason)
        )

    # -- observation emission -------------------------------------------

    def emit(
        self,
        *,
        binding: AdapterBinding,
        raw_field: str,
        value: object,
        status: ObservationStatus,
        sample_timestamp_s: float,
        available_timestamp_s: float,
    ) -> None:
        """Emit one canonical Observation, refusing to claim a channel twice."""
        channel = binding.channel
        owner = self._claimed.get(channel)
        if owner is not None:
            self.reject(
                sensor_id=binding.sensor_id,
                channel=channel,
                raw_field=raw_field,
                code=RejectionCode.DUPLICATE_CHANNEL,
                detail=(
                    f"channel {channel!r} was already produced by sensor "
                    f"{owner!r}; conflicting observations are not merged"
                ),
            )
            self.demote_to_missing(channel)
            return
        self._claimed[channel] = binding.sensor_id
        self.observations.append(
            Observation(
                channel=channel,
                value=value,
                sample_timestamp_s=sample_timestamp_s,
                available_timestamp_s=available_timestamp_s,
                status=status,
                source_type=SourceType(binding.source_type),
                source_id=binding.source_id,
                calibration_id=binding.calibration_id,
                confidence=CONFIDENCE_BY_STATUS[status.value],
                seq=self.cycle_index,
            )
        )
        if status is ObservationStatus.MISSING:
            return
        self.accepted.append(
            AcceptedField(
                sensor_id=binding.sensor_id,
                channel=channel,
                raw_field=raw_field,
                status=status.value,
            )
        )

    def emit_missing(
        self,
        *,
        binding: AdapterBinding,
        raw_field: str,
        code: RejectionCode,
        detail: str,
        sample_timestamp_s: float,
        available_timestamp_s: float,
    ) -> None:
        """Record why a channel is untrustworthy and publish that as MISSING."""
        self.reject(
            sensor_id=binding.sensor_id,
            channel=binding.channel,
            raw_field=raw_field,
            code=code,
            detail=detail,
        )
        self.emit(
            binding=binding,
            raw_field=raw_field,
            value=None,
            status=ObservationStatus.MISSING,
            sample_timestamp_s=sample_timestamp_s,
            available_timestamp_s=available_timestamp_s,
        )

    def demote_to_missing(self, channel: str) -> None:
        """A conflict invalidates the value already emitted for a channel."""
        for index, observation in enumerate(self.observations):
            if observation.channel != channel:
                continue
            if observation.status is ObservationStatus.MISSING:
                return
            self.observations[index] = Observation(
                channel=channel,
                value=None,
                sample_timestamp_s=observation.sample_timestamp_s,
                available_timestamp_s=observation.available_timestamp_s,
                status=ObservationStatus.MISSING,
                source_type=observation.source_type,
                source_id=observation.source_id,
                calibration_id=observation.calibration_id,
                confidence=CONFIDENCE_BY_STATUS["missing"],
                seq=observation.seq,
            )
            self.accepted = [
                item for item in self.accepted if item.channel != channel
            ]
            return

    @property
    def claimed_channels(self) -> frozenset[str]:
        return frozenset(self._claimed)

    def report(self) -> EdgeAdapterReport:
        return EdgeAdapterReport(
            schema=ADAPTER_REPORT_SCHEMA,
            adapter_version=EDGE_ADAPTER_VERSION,
            cycle_index=self.cycle_index,
            frame_t_s=self.frame_t_s,
            accepted=tuple(
                sorted(self.accepted, key=lambda item: (item.channel, item.raw_field))
            ),
            rejected=tuple(
                sorted(
                    self.rejected,
                    key=lambda item: (item.sensor_id, item.raw_field, item.code.value),
                )
            ),
            unmapped=tuple(
                sorted(self.unmapped, key=lambda item: (item.sensor_id, item.raw_field))
            ),
        )


@dataclass(frozen=True, slots=True)
class _TimingVerdict:
    ok: bool
    stale: bool
    code: RejectionCode | None = None
    detail: str = ""


def _check_timing(
    *,
    sample_timestamp_s: float,
    available_timestamp_s: float,
    frame_t_s: float,
    stale_after_s: float,
) -> _TimingVerdict:
    if sample_timestamp_s > frame_t_s:
        return _TimingVerdict(
            ok=False,
            stale=False,
            code=RejectionCode.FUTURE_SAMPLE,
            detail=(
                f"sample timestamp {sample_timestamp_s} is after frame time "
                f"{frame_t_s}"
            ),
        )
    if available_timestamp_s > frame_t_s:
        return _TimingVerdict(
            ok=False,
            stale=False,
            code=RejectionCode.INCONSISTENT_TIMESTAMPS,
            detail=(
                f"available timestamp {available_timestamp_s} is after frame "
                f"time {frame_t_s}; the reading is not yet visible"
            ),
        )
    age = frame_t_s - sample_timestamp_s
    return _TimingVerdict(ok=True, stale=age > stale_after_s)


def _device_status_verdict(
    device_status: str,
) -> tuple[RejectionCode, str] | None:
    if device_status == DEVICE_STATUS_OK:
        return None
    if device_status == DEVICE_STATUS_FAULT:
        return (
            RejectionCode.DEVICE_FAULT,
            "the device reports a fault; its reading is not evidence of state",
        )
    if device_status == DEVICE_STATUS_MISSING:
        return (
            RejectionCode.DEVICE_REPORTED_MISSING,
            "the device reports no data for this cycle",
        )
    return (
        RejectionCode.UNKNOWN_VALUE,
        f"unknown device status {device_status!r}; "
        f"known statuses are {sorted(_KNOWN_DEVICE_STATUS)}",
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


# ---------------------------------------------------------------------------
# 1. Dispenser / load-cell measurements
# ---------------------------------------------------------------------------


class LoadCellAdapter:
    """Convert calibrated mass readings into canonical ball counts.

    The commissioned binding decides the channel; this adapter never picks
    one.  The commissioned ``CalibrationInfo`` decides calibration
    identity and validity; the adapter-local
    :class:`~nxt_edge_observation.contracts.LoadCellProfile` supplies the
    coefficients that contract does not carry, and must agree with it.
    """

    def convert(
        self,
        sample: LoadCellSample,
        *,
        bindings: AdapterBindingSet,
        profiles: dict[str, LoadCellProfile],
        collector: _Collector,
    ) -> None:
        if sample.diagnostic_code is not None:
            collector.unmap(
                sensor_id=sample.sensor_id,
                raw_field="diagnostic_code",
                reason=(
                    "device diagnostic codes have no canonical telemetry "
                    "channel; kept as adapter evidence only"
                ),
            )

        binding = bindings.by_sensor_id(sample.sensor_id)
        if binding is None:
            collector.reject(
                sensor_id=sample.sensor_id,
                channel=None,
                raw_field="raw_value",
                code=RejectionCode.NO_BINDING,
                detail="no commissioned sensor binding for this load cell",
            )
            return

        kind = channel_kind(binding.channel)
        if kind not in (ChannelKind.COUNT, ChannelKind.QUANTITY):
            collector.reject(
                sensor_id=sample.sensor_id,
                channel=binding.channel,
                raw_field="raw_value",
                code=RejectionCode.UNSUPPORTED_CHANNEL,
                detail=(
                    f"channel {binding.channel!r} is {kind.value}; a load cell "
                    "can only produce a counted or measured quantity"
                ),
            )
            return
        if binding.canonical_unit != "balls":
            collector.reject(
                sensor_id=sample.sensor_id,
                channel=binding.channel,
                raw_field="raw_value",
                code=RejectionCode.UNSUPPORTED_UNIT,
                detail=(
                    f"channel {binding.channel!r} has canonical unit "
                    f"{binding.canonical_unit!r}; this adapter only converts "
                    "mass into a ball-denominated channel"
                ),
            )
            return

        timing = sample.timing
        emit_missing = lambda code, detail: collector.emit_missing(  # noqa: E731
            binding=binding,
            raw_field="raw_value",
            code=code,
            detail=detail,
            sample_timestamp_s=timing.sample_timestamp_s,
            available_timestamp_s=timing.available_timestamp_s,
        )

        profile = profiles.get(sample.sensor_id)
        if profile is None:
            emit_missing(
                RejectionCode.CALIBRATION_MISSING,
                "no adapter calibration profile is declared for this load cell",
            )
            return
        if profile.calibration_id != binding.calibration_id:
            emit_missing(
                RejectionCode.CALIBRATION_MISMATCH,
                f"profile calibration {profile.calibration_id!r} does not match "
                f"commissioned calibration {binding.calibration_id!r}",
            )
            return
        if binding.calibration_required:
            if sample.calibration_id is None:
                emit_missing(
                    RejectionCode.CALIBRATION_MISSING,
                    "the sample carries no calibration reference but the "
                    "commissioned binding requires one",
                )
                return
            if sample.calibration_id != binding.calibration_id:
                emit_missing(
                    RejectionCode.CALIBRATION_MISMATCH,
                    f"sample calibration {sample.calibration_id!r} does not "
                    f"match commissioned calibration {binding.calibration_id!r}",
                )
                return
        elif sample.calibration_id is not None:
            # The device claims a calibration the commissioned binding says
            # it does not have.  Silently discarding that claim would hide a
            # real disagreement between the device and the manifest.
            emit_missing(
                RejectionCode.CALIBRATION_MISMATCH,
                f"the sample declares calibration {sample.calibration_id!r} "
                "but the commissioned binding declares calibration is not "
                "required",
            )
            return

        verdict = _check_timing(
            sample_timestamp_s=timing.sample_timestamp_s,
            available_timestamp_s=timing.available_timestamp_s,
            frame_t_s=collector.frame_t_s,
            stale_after_s=profile.stale_after_s,
        )
        if not verdict.ok:
            emit_missing(verdict.code, verdict.detail)
            return

        status_problem = _device_status_verdict(sample.device_status)
        if status_problem is not None:
            emit_missing(*status_problem)
            return
        if sample.raw_value is None:
            emit_missing(
                RejectionCode.DEVICE_REPORTED_MISSING,
                "the load cell reported no value; a missing reading is never "
                "zero inventory",
            )
            return
        if sample.raw_unit != profile.raw_unit:
            emit_missing(
                RejectionCode.UNSUPPORTED_UNIT,
                f"sample unit {sample.raw_unit!r} does not match the declared "
                f"calibration unit {profile.raw_unit!r}",
            )
            return
        raw = _finite_number(sample.raw_value)
        if raw is None:
            emit_missing(
                RejectionCode.NON_FINITE_VALUE,
                f"raw value {sample.raw_value!r} is not a finite real number",
            )
            return

        net_raw = raw - profile.tare_raw
        if net_raw < 0.0:
            emit_missing(
                RejectionCode.VALUE_OUT_OF_RANGE,
                f"net mass {net_raw} {profile.raw_unit} is negative after tare; "
                "the cell is faulty or mis-tared",
            )
            return
        balls = net_raw / profile.mass_per_ball_raw
        if not math.isfinite(balls):
            # A finite but enormous raw reading (a common driver fault
            # sentinel) can still divide to infinity.  Guarding only the
            # raw value would let int(round(inf)) raise OverflowError out
            # of convert() and destroy every other sample in the batch.
            emit_missing(
                RejectionCode.NON_FINITE_VALUE,
                f"derived ball count is not finite for net mass {net_raw} "
                f"{profile.raw_unit}",
            )
            return
        if kind is ChannelKind.COUNT:
            value: object = int(round(balls))
        else:
            # Round the sensed quantity so an identical raw sample always
            # serialises to identical canonical bytes.
            value = round(balls, 6)
        if float(value) > float(profile.capacity_balls):
            emit_missing(
                RejectionCode.VALUE_OUT_OF_RANGE,
                f"derived {value} balls exceeds the declared capacity "
                f"{profile.capacity_balls}",
            )
            return

        collector.emit(
            binding=binding,
            raw_field="raw_value",
            value=value,
            status=(
                ObservationStatus.STALE if verdict.stale else ObservationStatus.OK
            ),
            sample_timestamp_s=timing.sample_timestamp_s,
            available_timestamp_s=timing.available_timestamp_s,
        )


# ---------------------------------------------------------------------------
# 2. Washer / Universal Handoff digital I/O
# ---------------------------------------------------------------------------


class DigitalIOAdapter:
    """Convert already-read digital-input snapshots into canonical states.

    Every point maps one-to-one to its own commissioned binding.  No
    input's value is ever derived from another, so an observed emergency
    stop can influence nothing: it is evidence, never authority.  The
    adapter reads inputs only -- it has no output, coil, register, or
    GPIO write path.
    """

    def convert(
        self,
        snapshot: DigitalIOSnapshot,
        *,
        bindings: AdapterBindingSet,
        device_profile: DigitalDeviceProfile,
        profiles: dict[str, DigitalInputProfile],
        collector: _Collector,
    ) -> None:
        if device_profile.device_id != snapshot.device_id:
            collector.reject(
                sensor_id=snapshot.device_id,
                channel=None,
                raw_field="device_id",
                code=RejectionCode.IDENTITY_MISMATCH,
                detail=(
                    f"snapshot device {snapshot.device_id!r} does not match the "
                    f"declared profile device {device_profile.device_id!r}"
                ),
            )
            return

        by_name = {item.input_name: item for item in snapshot.inputs}

        def logical_state(sample: DigitalInputSample) -> bool | None:
            """Apply declared polarity before any semantic comparison.

            A normally-closed point reads ``False`` when it is physically
            asserted, so comparing raw bits here would invert exactly the
            wiring ``DigitalInputProfile.active_low`` exists to describe.
            """
            if sample.raw_state is None:
                return None
            profile = profiles.get(sample.sensor_id)
            if profile is not None and profile.active_low:
                return not sample.raw_state
            return sample.raw_state

        impossible: set[str] = set()
        for first, second in device_profile.mutually_exclusive_inputs:
            left = by_name.get(first)
            right = by_name.get(second)
            if left is None or right is None:
                continue
            if logical_state(left) is True and logical_state(right) is True:
                impossible.update({first, second})
                for name in (first, second):
                    collector.reject(
                        sensor_id=by_name[name].sensor_id,
                        channel=None,
                        raw_field=name,
                        code=RejectionCode.IMPOSSIBLE_STATE,
                        detail=(
                            f"inputs {first!r} and {second!r} are mutually "
                            "exclusive but both are asserted"
                        ),
                    )

        seen_sensors: dict[str, str] = {}
        status_problem = _device_status_verdict(snapshot.device_status)
        timing = snapshot.timing

        for sample in snapshot.inputs:
            binding = bindings.by_sensor_id(sample.sensor_id)
            if binding is None:
                if sample.input_name in device_profile.raw_only_inputs:
                    collector.unmap(
                        sensor_id=sample.sensor_id,
                        raw_field=sample.input_name,
                        reason=(
                            "declared raw-only input: no canonical telemetry "
                            "channel exists for it in this repository"
                        ),
                    )
                else:
                    collector.reject(
                        sensor_id=sample.sensor_id,
                        channel=None,
                        raw_field=sample.input_name,
                        code=RejectionCode.NO_BINDING,
                        detail=(
                            "unknown digital input: neither commissioned nor "
                            "declared raw-only"
                        ),
                    )
                continue

            previous = seen_sensors.get(sample.sensor_id)
            if previous is not None:
                collector.reject(
                    sensor_id=sample.sensor_id,
                    channel=binding.channel,
                    raw_field=sample.input_name,
                    code=RejectionCode.DUPLICATE_CHANNEL,
                    detail=(
                        f"sensor {sample.sensor_id!r} is already bound to input "
                        f"{previous!r} in this snapshot"
                    ),
                )
                collector.demote_to_missing(binding.channel)
                continue
            seen_sensors[sample.sensor_id] = sample.input_name

            emit_missing = lambda code, detail: collector.emit_missing(  # noqa: E731
                binding=binding,
                raw_field=sample.input_name,
                code=code,
                detail=detail,
                sample_timestamp_s=timing.sample_timestamp_s,
                available_timestamp_s=timing.available_timestamp_s,
            )

            profile = profiles.get(sample.sensor_id)
            if profile is None:
                emit_missing(
                    RejectionCode.CALIBRATION_MISSING,
                    "no adapter profile declares the polarity of this input",
                )
                continue
            if profile.calibration_id != binding.calibration_id:
                emit_missing(
                    RejectionCode.CALIBRATION_MISMATCH,
                    f"profile calibration {profile.calibration_id!r} does not "
                    f"match commissioned calibration {binding.calibration_id!r}",
                )
                continue

            kind = channel_kind(binding.channel)
            if kind not in (ChannelKind.BOOLEAN, ChannelKind.COUNT):
                emit_missing(
                    RejectionCode.UNSUPPORTED_CHANNEL,
                    f"channel {binding.channel!r} is {kind.value}; a digital "
                    "input can only produce a boolean or a 0/1 occupancy count",
                )
                continue
            # A single bit is never a quantity of balls.  Without this the
            # adapter would publish an occupancy bit as a full-confidence
            # ball count on a ball-denominated channel, inventing a
            # physical fact that no device measured.
            expected_unit = "1" if kind is ChannelKind.BOOLEAN else "count"
            if binding.canonical_unit != expected_unit:
                emit_missing(
                    RejectionCode.UNSUPPORTED_UNIT,
                    f"channel {binding.channel!r} has canonical unit "
                    f"{binding.canonical_unit!r}; a digital input can only "
                    f"serve a {expected_unit!r}-denominated channel",
                )
                continue
            # A discrete point carries no calibration reference, so it can
            # never satisfy a binding whose commissioned sensor requires
            # calibrated evidence.
            if binding.calibration_required:
                emit_missing(
                    RejectionCode.CALIBRATION_MISSING,
                    f"channel {binding.channel!r} requires calibrated evidence "
                    f"({binding.calibration_id!r}); a digital input carries no "
                    "calibration reference",
                )
                continue

            verdict = _check_timing(
                sample_timestamp_s=timing.sample_timestamp_s,
                available_timestamp_s=timing.available_timestamp_s,
                frame_t_s=collector.frame_t_s,
                stale_after_s=profile.stale_after_s,
            )
            # Diagnostic precedence is identical in all three adapters:
            # structural problems, then payload timing validity, then the
            # device's own self-report, then value validity.  Every branch
            # yields MISSING, so the order only decides which cause is
            # named -- but naming the most fundamental one keeps the
            # adapter report actionable.
            if not verdict.ok:
                emit_missing(verdict.code, verdict.detail)
                continue
            if status_problem is not None:
                emit_missing(*status_problem)
                continue
            if sample.input_name in impossible:
                emit_missing(
                    RejectionCode.IMPOSSIBLE_STATE,
                    "the input participates in an impossible equipment state",
                )
                continue
            if sample.raw_state is None:
                emit_missing(
                    RejectionCode.DEVICE_REPORTED_MISSING,
                    "the digital input reported no state for this cycle",
                )
                continue

            logical = (
                (not sample.raw_state) if profile.active_low else sample.raw_state
            )
            value: object = logical if kind is ChannelKind.BOOLEAN else int(logical)
            collector.emit(
                binding=binding,
                raw_field=sample.input_name,
                value=value,
                status=(
                    ObservationStatus.STALE
                    if verdict.stale
                    else ObservationStatus.OK
                ),
                sample_timestamp_s=timing.sample_timestamp_s,
                available_timestamp_s=timing.available_timestamp_s,
            )


# ---------------------------------------------------------------------------
# 3. Robot heartbeat and operational status
# ---------------------------------------------------------------------------


class RobotStatusAdapter:
    """Convert already-received robot status reports into canonical channels.

    ``estop_latched`` is telemetry.  This adapter cannot clear, reset, or
    command an emergency stop, and infers no capability, ETA, expected
    yield, collection permission, or washer availability from any robot
    field: those facts have no evidence-backed source here, and the
    downstream Guardian is expected to keep failing closed on them.
    """

    def convert(
        self,
        sample: RobotStatusSample,
        *,
        bindings: AdapterBindingSet,
        profiles: dict[str, RobotStatusProfile],
        coordinate_frame: str,
        collector: _Collector,
    ) -> None:
        profile = profiles.get(sample.robot_id)
        if profile is None:
            collector.reject(
                sensor_id=sample.robot_id,
                channel=None,
                raw_field="robot_id",
                code=RejectionCode.IDENTITY_MISMATCH,
                detail=(
                    "unknown robot identity: no commissioned robot profile "
                    f"named {sample.robot_id!r}"
                ),
            )
            return

        self._report_fields_without_channel(sample, coordinate_frame, collector)

        timing = sample.timing
        verdict = _check_timing(
            sample_timestamp_s=timing.sample_timestamp_s,
            available_timestamp_s=timing.available_timestamp_s,
            frame_t_s=collector.frame_t_s,
            stale_after_s=profile.stale_after_s,
        )
        status_problem = _device_status_verdict(sample.device_status)

        for field in _ROBOT_CANONICAL_FIELDS:
            channel = robot_channel(sample.robot_id, field)
            binding = bindings.by_channel(channel)
            if binding is None:
                collector.unmap(
                    sensor_id=sample.robot_id,
                    raw_field=field,
                    reason=(
                        f"no commissioned sensor binding declares channel "
                        f"{channel!r}"
                    ),
                )
                continue
            if binding.calibration_id != profile.calibration_id:
                collector.emit_missing(
                    binding=binding,
                    raw_field=field,
                    code=RejectionCode.CALIBRATION_MISMATCH,
                    detail=(
                        f"profile calibration {profile.calibration_id!r} does "
                        f"not match commissioned calibration "
                        f"{binding.calibration_id!r}"
                    ),
                    sample_timestamp_s=timing.sample_timestamp_s,
                    available_timestamp_s=timing.available_timestamp_s,
                )
                continue

            emit_missing = lambda code, detail, b=binding, f=field: (  # noqa: E731
                collector.emit_missing(
                    binding=b,
                    raw_field=f,
                    code=code,
                    detail=detail,
                    sample_timestamp_s=timing.sample_timestamp_s,
                    available_timestamp_s=timing.available_timestamp_s,
                )
            )
            if not verdict.ok:
                emit_missing(verdict.code, verdict.detail)
                continue
            if status_problem is not None:
                emit_missing(*status_problem)
                continue

            outcome = self._field_value(sample, profile, field)
            if outcome[0] is not None:
                emit_missing(outcome[0], outcome[1])
                continue
            collector.emit(
                binding=binding,
                raw_field=field,
                value=outcome[2],
                status=(
                    ObservationStatus.STALE
                    if verdict.stale
                    else ObservationStatus.OK
                ),
                sample_timestamp_s=timing.sample_timestamp_s,
                available_timestamp_s=timing.available_timestamp_s,
            )

    @staticmethod
    def _report_fields_without_channel(
        sample: RobotStatusSample,
        coordinate_frame: str,
        collector: _Collector,
    ) -> None:
        if sample.fault_code is not None:
            collector.unmap(
                sensor_id=sample.robot_id,
                raw_field="fault_code",
                reason=(
                    "robot fault codes have no canonical channel; "
                    "robot.<id>.health is the only health destination"
                ),
            )
        if sample.coordinate_frame is not None:
            if sample.coordinate_frame != coordinate_frame:
                collector.reject(
                    sensor_id=sample.robot_id,
                    channel=None,
                    raw_field="coordinate_frame",
                    code=RejectionCode.COORDINATE_FRAME_MISMATCH,
                    detail=(
                        f"sample frame {sample.coordinate_frame!r} does not "
                        f"match the commissioned coordinate reference "
                        f"{coordinate_frame!r}"
                    ),
                )
            else:
                collector.unmap(
                    sensor_id=sample.robot_id,
                    raw_field="coordinate_frame",
                    reason=(
                        "the coordinate frame is validated but has no canonical "
                        "telemetry channel"
                    ),
                )
        if sample.position_x_m is not None or sample.position_y_m is not None:
            collector.unmap(
                sensor_id=sample.robot_id,
                raw_field="position",
                reason=(
                    "metric robot pose has no canonical channel; "
                    "robot.<id>.location carries a named location only"
                ),
            )

    @staticmethod
    def _field_value(
        sample: RobotStatusSample,
        profile: RobotStatusProfile,
        field: str,
    ) -> tuple[RejectionCode | None, str, object]:
        """Return ``(rejection_code, detail, value)`` for one robot field."""
        if field == "battery_frac":
            if sample.battery is None:
                return (
                    RejectionCode.DEVICE_REPORTED_MISSING,
                    "the robot reported no battery level",
                    None,
                )
            number = _finite_number(sample.battery)
            if number is None:
                return (
                    RejectionCode.NON_FINITE_VALUE,
                    f"battery {sample.battery!r} is not a finite real number",
                    None,
                )
            if profile.battery_unit == "percent":
                if not 0.0 <= number <= 100.0:
                    return (
                        RejectionCode.VALUE_OUT_OF_RANGE,
                        f"battery {number} is outside 0-100 percent",
                        None,
                    )
                number = number / 100.0
            elif not 0.0 <= number <= 1.0:
                return (
                    RejectionCode.VALUE_OUT_OF_RANGE,
                    f"battery {number} is outside the 0-1 fraction range",
                    None,
                )
            return (None, "", round(number, 6))

        if field == "payload_balls":
            if sample.payload_balls is None:
                return (
                    RejectionCode.DEVICE_REPORTED_MISSING,
                    "the robot reported no payload",
                    None,
                )
            if sample.payload_balls < 0:
                return (
                    RejectionCode.VALUE_OUT_OF_RANGE,
                    f"payload {sample.payload_balls} is negative",
                    None,
                )
            return (None, "", sample.payload_balls)

        if field in ("estop_latched", "awaiting_human"):
            value = getattr(sample, field)
            if value is None:
                return (
                    RejectionCode.DEVICE_REPORTED_MISSING,
                    f"the robot reported no {field} state",
                    None,
                )
            return (None, "", value)

        vocabularies = {
            "activity": (profile.allowed_activities, False),
            "health": (profile.allowed_health, False),
            "location": (profile.allowed_locations, False),
            "destination": (profile.allowed_locations, True),
            "assigned_zone": (profile.allowed_zones, True),
        }
        allowed, blank_allowed = vocabularies[field]
        value = getattr(sample, field)
        if value is None:
            return (
                RejectionCode.DEVICE_REPORTED_MISSING,
                f"the robot reported no {field}",
                None,
            )
        if type(value) is not str:
            return (
                RejectionCode.UNKNOWN_VALUE,
                f"{field} {value!r} is not a string",
                None,
            )
        if value == "" and blank_allowed:
            return (None, "", "")
        if value not in allowed:
            return (
                RejectionCode.UNKNOWN_VALUE,
                f"{field} {value!r} is outside the declared vocabulary "
                f"{sorted(allowed)}",
                None,
            )
        return (None, "", value)


# ---------------------------------------------------------------------------
# Kit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Canonical observations plus the diagnostics that explain them."""

    observations: tuple[Observation, ...]
    report: EdgeAdapterReport


class EdgeObservationAdapterKit:
    """Compose the three reference adapters over one commissioned deployment.

    The kit owns no facility state, no policy, and no transport.  Given
    already-read raw samples it returns existing canonical Observations
    and a separate diagnostics report; the caller decides what to do with
    both.
    """

    def __init__(
        self,
        *,
        bindings: AdapterBindingSet,
        coordinate_frame: str,
        load_cell_profiles: tuple[LoadCellProfile, ...] = (),
        digital_device_profiles: tuple[DigitalDeviceProfile, ...] = (),
        digital_input_profiles: tuple[DigitalInputProfile, ...] = (),
        robot_profiles: tuple[RobotStatusProfile, ...] = (),
    ) -> None:
        if not isinstance(bindings, AdapterBindingSet):
            raise EdgeAdapterError("bindings must be an AdapterBindingSet")
        if type(coordinate_frame) is not str or not coordinate_frame.strip():
            raise EdgeAdapterError("coordinate_frame must be a non-blank string")
        self._bindings = bindings
        self._coordinate_frame = coordinate_frame
        self._load_cells = _index(load_cell_profiles, "sensor_id", "load cell")
        self._digital_devices = _index(
            digital_device_profiles, "device_id", "digital device"
        )
        self._digital_inputs = _index(
            digital_input_profiles, "sensor_id", "digital input"
        )
        self._robots = _index(robot_profiles, "robot_id", "robot")
        self._load_cell_adapter = LoadCellAdapter()
        self._digital_adapter = DigitalIOAdapter()
        self._robot_adapter = RobotStatusAdapter()

    @property
    def bindings(self) -> AdapterBindingSet:
        return self._bindings

    def convert(self, batch: RawSampleBatch) -> ConversionResult:
        """Convert one raw batch into canonical observations plus diagnostics."""
        if not isinstance(batch, RawSampleBatch):
            raise EdgeAdapterError("batch must be a RawSampleBatch")
        collector = _Collector(
            cycle_index=batch.cycle_index, frame_t_s=batch.frame_t_s
        )
        for sample in sorted(batch.load_cells, key=lambda item: item.sensor_id):
            self._load_cell_adapter.convert(
                sample,
                bindings=self._bindings,
                profiles=self._load_cells,
                collector=collector,
            )
        for snapshot in sorted(batch.digital_io, key=lambda item: item.device_id):
            device_profile = self._digital_devices.get(snapshot.device_id)
            if device_profile is None:
                collector.reject(
                    sensor_id=snapshot.device_id,
                    channel=None,
                    raw_field="device_id",
                    code=RejectionCode.UNKNOWN_SOURCE,
                    detail="no adapter profile is declared for this I/O device",
                )
                continue
            self._digital_adapter.convert(
                snapshot,
                bindings=self._bindings,
                device_profile=device_profile,
                profiles=self._digital_inputs,
                collector=collector,
            )
        for sample in sorted(batch.robots, key=lambda item: item.robot_id):
            self._robot_adapter.convert(
                sample,
                bindings=self._bindings,
                profiles=self._robots,
                coordinate_frame=self._coordinate_frame,
                collector=collector,
            )
        self._reconcile_silent_devices(collector, batch)
        observations = tuple(
            sorted(collector.observations, key=lambda item: item.channel)
        )
        return ConversionResult(
            observations=observations, report=collector.report()
        )

    def _reconcile_silent_devices(
        self, collector: _Collector, batch: RawSampleBatch
    ) -> None:
        """Publish an explicit gap for every commissioned channel with no sample.

        A device that simply stops reporting must not become an absent key.
        Left absent, the assembler's own backfill turns a silent robot into
        ``health=ok`` and a silent dispenser load cell into zero inventory,
        and the adapter report would look like a clean cycle.  Every
        commissioned channel this batch did not claim therefore gets a
        ``MISSING`` Observation and a named ``NO_SAMPLE`` rejection.

        The frame timestamp is used for both observation timestamps: with
        no reading there is no measurement instant to preserve, and a
        ``MISSING`` observation carries no measurement claim.
        """
        claimed = collector.claimed_channels
        for binding in self._bindings.bindings:
            if binding.channel in claimed:
                continue
            collector.emit_missing(
                binding=binding,
                raw_field="(no sample)",
                code=RejectionCode.NO_SAMPLE,
                detail=(
                    f"no raw sample was delivered for commissioned sensor "
                    f"{binding.sensor_id!r}; the device is silent for this cycle"
                ),
                sample_timestamp_s=batch.frame_t_s,
                available_timestamp_s=batch.frame_t_s,
            )


def _index(profiles, key: str, label: str) -> dict:
    indexed: dict = {}
    for profile in profiles:
        identity = getattr(profile, key)
        if identity in indexed:
            raise EdgeAdapterError(f"duplicate {label} profile for {identity!r}")
        indexed[identity] = profile
    return indexed


__all__ = [
    "DEVICE_STATUS_FAULT",
    "DEVICE_STATUS_MISSING",
    "DEVICE_STATUS_OK",
    "ConversionResult",
    "DigitalIOAdapter",
    "EdgeObservationAdapterKit",
    "LoadCellAdapter",
    "RobotStatusAdapter",
]
