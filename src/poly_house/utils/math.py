"""Math utilities."""

from __future__ import annotations

from math import sqrt


def wilson_ci(p_hat: float, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson confidence interval for binomial proportion."""
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = z * sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))
