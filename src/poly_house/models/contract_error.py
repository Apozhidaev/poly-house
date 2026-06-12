"""Domain models for Polymarket contract errors."""

from __future__ import annotations


class ContractError(RuntimeError):
    """Event or market does not satisfy the poly-house contract."""
