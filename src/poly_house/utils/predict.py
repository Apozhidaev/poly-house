from __future__ import annotations

from typing import Protocol, Sequence

from poly_house.models.tag_profile import TagProfileAggregate


class VoteHolder(Protocol):
    proxy_wallet: str
    outcome_index: int


def compute_market_vote_prices(
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
