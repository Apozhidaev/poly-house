"""Metrics utilities."""

from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

from poly_house.models.ts import TimeSeriesPoint


def wilson_ci(p_hat: float, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson confidence interval for binomial proportion."""
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = z * sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def fit_trend_by_days(
    points: Sequence[TimeSeriesPoint], use_cumulative: bool = False
) -> tuple[float, float, float]:
    """
    Linear regression over time series. Returns (intercept, slope, R²).

    use_cumulative=True  → if pnl in data is a growth over a period (need to accumulate)
    use_cumulative=False → if pnl is already cumulative (from 0 to final profit)

    intercept — y-axis intercept, slope — growth coefficient, R² ∈ [0, 1] — fit quality.
    Uses timestamp as x (days since first point).
    """
    if len(points) < 2:
        return (0.0, 0.0, 0.0)

    sorted_points = sorted(points, key=lambda p: p.timestamp)

    if use_cumulative:
        y = []
        total = 0.0
        for p in sorted_points:
            total += p.value
            y.append(total)
    else:
        y = [p.value for p in sorted_points]

    first_ts = sorted_points[0].timestamp
    x = []
    for p in sorted_points:
        delta = p.timestamp - first_ts
        days = delta.total_seconds() / 86400
        x.append(days)

    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xx = sum((xi - mean_x) ** 2 for xi in x)
    ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))

    if ss_xx == 0:
        return (0.0, 0.0, 0.0)

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    y_pred = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - y_pred_i) ** 2 for yi, y_pred_i in zip(y, y_pred))
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)

    if ss_tot == 0:
        return (0.0, 0.0, 0.0)

    r_squared = 1 - (ss_res / ss_tot)
    return (intercept, slope, r_squared)


def compute_stable_growth(
    pnl_growth: float | None,
    pnl_stability: float | None,
) -> float | None:
    if pnl_growth is None or pnl_stability is None:
        return None
    return pnl_stability if pnl_growth > 0 else 0.0
