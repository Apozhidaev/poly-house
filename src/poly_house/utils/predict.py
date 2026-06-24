from __future__ import annotations

from typing import Protocol, Sequence

from poly_house.models.tag_profile import TagProfileAggregate


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


def compute_win_holder_rate(
    win_holder_count: float | None,
    lose_holder_count: float | None,
    markets_count: float | None,
) -> float:
    if win_holder_count is None or lose_holder_count is None or markets_count is None:
        return 0.0
    if markets_count == 0:
        return 0.0
    only_win = (
        win_holder_count - lose_holder_count
        if win_holder_count > lose_holder_count
        else 0.0
    )
    return only_win / markets_count


def filter_for_market_vote(agg: TagProfileAggregate | None) -> float:
    if agg is None:
        return 0.0
    if agg.win_rate is None:
        return 0.0
    if agg.pnl is None or agg.pnl <= 0:
        return 0.0
    if agg.volume is None or agg.volume == 0:
        return 0.0
    roi = agg.pnl / agg.volume
    stable_growth = compute_stable_growth(agg.pnl_growth, agg.pnl_stability)
    win_holder_rate = compute_win_holder_rate(agg.win_holder_count, agg.lose_holder_count, agg.markets_count)
    return roi * stable_growth * agg.win_rate * win_holder_rate


def compute_market_vote_prices_legacy(
    holders: Sequence[VoteHolder],
    aggregates: dict[str, TagProfileAggregate],
) -> list[float | None]:
    votes = [0.0, 0.0]
    for holder in holders:
        agg = aggregates.get(holder.proxy_wallet)
        win = (
            agg.win_holder_count
            if agg is not None and agg.win_holder_count is not None
            else 0.0
        )
        lose = (
            agg.lose_holder_count
            if agg is not None and agg.lose_holder_count is not None
            else 0.0
        )
        votes[holder.outcome_index] += win
        votes[1 - holder.outcome_index] += lose
    total = votes[0] + votes[1]
    if total == 0.0:
        return [None, None]
    return [votes[0] / total, votes[1] / total]


def compute_market_vote_prices(
    holders: Sequence[VoteHolder],
    aggregates: dict[str, TagProfileAggregate],
) -> list[float | None]:
    votes = [0.0, 0.0]
    for holder in holders:
        agg = aggregates.get(holder.proxy_wallet)
        win = (
            agg.strict_win_count
            if agg is not None and agg.strict_win_count is not None
            else 0.0
        )
        votes[holder.outcome_index] += win
    total = votes[0] + votes[1]
    if total == 0.0:
        return [None, None]
    return [votes[0] / total, votes[1] / total]
