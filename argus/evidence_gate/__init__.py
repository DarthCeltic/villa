"""Small, offline-first evidence gate for ARGUS-compatible workflows."""

__version__ = "0.1.0"

from .pipeline import EvidenceReport, evaluate_payload

__all__ = ["EvidenceReport", "evaluate_payload", "__version__"]
