"""Verified read-only adapters into Shadow Ops contracts."""

from .facility_state import (
    NATIVE_STREAM_SCHEMA,
    AdapterError,
    FacilityStateAdapterContext,
    NativeFacilityStateCapture,
    NativeStreamMetadata,
    adapt_facility_state,
    read_native_facility_state_capture,
    read_native_facility_state_stream,
)

__all__ = [
    "NATIVE_STREAM_SCHEMA",
    "AdapterError",
    "FacilityStateAdapterContext",
    "NativeFacilityStateCapture",
    "NativeStreamMetadata",
    "adapt_facility_state",
    "read_native_facility_state_capture",
    "read_native_facility_state_stream",
]
