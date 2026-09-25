"""Deterministic experiment infrastructure for the Vesuvius Challenge."""

from .geometry import Region3D
from .oracle import GateDecision, GatePolicy, gate_experiment

__all__ = ["GateDecision", "GatePolicy", "Region3D", "gate_experiment"]
__version__ = "0.1.0"
