"""Simulation / hardware adapters. Everything simulator-specific lives here."""

from nxt_sim.adapters.base import AdapterNotAvailableError, create_adapter

__all__ = ["AdapterNotAvailableError", "create_adapter"]
