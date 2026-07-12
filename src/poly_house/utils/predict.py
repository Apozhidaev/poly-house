from __future__ import annotations

from typing import Protocol, Sequence

from poly_house.models.tag_profile import TagProfileAggregate
from poly_house.utils import wilson_ci


class VoteHolder(Protocol):
    proxy_wallet: str
    outcome_index: int
    amount: float


def compute_stable_growth(
    pnl_growth: float | None,
    pnl_stability: float | None,
) -> float:
    if pnl_growth is None or pnl_stability is None:
        return 0.0
    return pnl_stability if pnl_growth > 0 else 0.0


def compute_roi(pnl: float | None, volume: float | None) -> float:
    if pnl is None or volume is None:
        return 0.0
    if volume == 0:
        return 0.0
    if pnl <= 0:
        return 0.0
    return pnl / volume


def compute_win_votes(
    agg: TagProfileAggregate | None,
) -> float:
    if agg is None:
        return 0.0
    if (
        agg.win_holder_count is None
        or agg.markets_count is None
        or agg.pnl is None
    ):
        return 0.0

    markets_count = int(agg.markets_count)

    if markets_count < 5:
        return 0.0
    if agg.pnl <= 0:
        return 0.0
    if agg.win_holder_count == 0:
        return 0.0

    hit_rate = agg.win_holder_count / agg.markets_count
    if hit_rate <= 0.5:
        return 0.0

    ci = wilson_ci(hit_rate, markets_count, confidence=0.99999)
    return ci[0]


def compute_market_vote_prices(
    holders: Sequence[VoteHolder],
    aggregates: dict[str, TagProfileAggregate],
) -> list[float | None]:
    votes = [0.0, 0.0]
    for holder in holders:
        agg = aggregates.get(holder.proxy_wallet)
        votes[holder.outcome_index] += compute_win_votes(agg)
        # votes[1 - holder.outcome_index] += compute_lose_votes(agg)
    total = votes[0] + votes[1]
    if total == 0.0:
        return [None, None]
    return [votes[0] / total, votes[1] / total]
