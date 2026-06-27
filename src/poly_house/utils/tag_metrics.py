from __future__ import annotations

from poly_house.db import Database
from poly_house.models.tag_profile import TagProfileAggregate


def compute_tag_weighted_average(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    values: dict[tuple[str, int], float],
) -> float | None:
    weighted_sum = 0.0
    weight_sum = 0.0
    for tag_id in tag_ids:
        value = values.get((wallet, tag_id))
        if value is None:
            continue
        weight = tag_weights.get(tag_id, 0.0)
        if weight == 0.0:
            continue
        weighted_sum += value * weight
        weight_sum += weight
    if weight_sum == 0.0:
        return None
    return weighted_sum / weight_sum


def compute_tag_win_rate(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    win_rates: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(wallet, tag_ids, tag_weights, win_rates)


def compute_tag_pnl_growth(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    pnl_growths: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, pnl_growths
    )


def compute_tag_pnl_stability(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    pnl_stabilities: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, pnl_stabilities
    )


def compute_tag_markets_count(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    markets_counts: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, markets_counts
    )


def compute_tag_win_holder_count(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    win_holder_counts: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, win_holder_counts
    )


def compute_tag_lose_holder_count(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    lose_holder_counts: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, lose_holder_counts
    )


def compute_tag_pnl_volume(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    pnls: dict[tuple[str, int], float],
    volumes: dict[tuple[str, int], float],
) -> tuple[float | None, float | None]:
    weighted_pnl = 0.0
    weighted_volume = 0.0
    for tag_id in tag_ids:
        key = (wallet, tag_id)
        pnl = pnls.get(key)
        volume = volumes.get(key)
        if pnl is None or volume is None:
            continue
        weight = tag_weights.get(tag_id, 0.0)
        if weight == 0.0:
            continue
        weighted_pnl += pnl * weight
        weighted_volume += volume * weight
    if weighted_volume == 0.0:
        return None, None
    return weighted_pnl, weighted_volume


async def load_tag_weights(db: Database, tag_ids: list[int]) -> dict[int, float]:
    if not tag_ids:
        return {}
    cursor = db.tags.find(
        {"_id": {"$in": tag_ids}},
        projection={"_id": 1, "events_count": 1},
    )
    docs = await cursor.to_list(length=None)
    weights: dict[int, float] = {}
    for doc in docs:
        events_count = doc.get("events_count", 0)
        weights[doc["_id"]] = 1 / events_count if events_count > 0 else 0.0
    return weights


async def load_tag_profile_tag_metrics(
    db: Database,
    wallets: list[str],
    tag_ids: list[int],
) -> tuple[
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
]:
    if not wallets or not tag_ids:
        return {}, {}, {}, {}, {}, {}, {}, {}
    cursor = db.tag_profiles.find(
        {
            "proxy_wallet": {"$in": wallets},
            "tag_id": {"$in": tag_ids},
        },
        projection={
            "proxy_wallet": 1,
            "tag_id": 1,
            "win_rate": 1,
            "pnl": 1,
            "volume": 1,
            "pnl_growth": 1,
            "pnl_stability": 1,
            "markets_count": 1,
            "win_holder_count": 1,
            "lose_holder_count": 1,
        },
    )
    docs = await cursor.to_list(length=None)
    win_rates: dict[tuple[str, int], float] = {}
    pnl_growths: dict[tuple[str, int], float] = {}
    pnl_stabilities: dict[tuple[str, int], float] = {}
    pnls: dict[tuple[str, int], float] = {}
    volumes: dict[tuple[str, int], float] = {}
    markets_counts: dict[tuple[str, int], float] = {}
    win_holder_counts: dict[tuple[str, int], float] = {}
    lose_holder_counts: dict[tuple[str, int], float] = {}
    for doc in docs:
        key = (doc["proxy_wallet"], doc["tag_id"])
        markets_counts[key] = float(doc.get("markets_count", 0))
        win_holder_counts[key] = float(doc.get("win_holder_count", 0))
        lose_holder_counts[key] = float(doc.get("lose_holder_count", 0))
        win_rate = doc.get("win_rate")
        if win_rate is not None:
            win_rates[key] = win_rate
        pnl_growth = doc.get("pnl_growth")
        if pnl_growth is not None:
            pnl_growths[key] = pnl_growth
        pnl_stability = doc.get("pnl_stability")
        if pnl_stability is not None:
            pnl_stabilities[key] = pnl_stability
        pnl = doc.get("pnl")
        if pnl is not None:
            pnls[key] = pnl
        volume = doc.get("volume")
        if volume is not None:
            volumes[key] = volume
    return (
        win_rates,
        pnl_growths,
        pnl_stabilities,
        pnls,
        volumes,
        markets_counts,
        win_holder_counts,
        lose_holder_counts,
    )


def wallet_has_tag_metrics(
    wallet: str,
    tag_ids: list[int],
    *maps: dict[tuple[str, int], float],
) -> bool:
    for tag_id in tag_ids:
        key = (wallet, tag_id)
        if any(key in metric_map for metric_map in maps):
            return True
    return False


def aggregate_wallet_tag_metrics(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    win_rates: dict[tuple[str, int], float],
    pnl_growths: dict[tuple[str, int], float],
    pnl_stabilities: dict[tuple[str, int], float],
    pnls: dict[tuple[str, int], float],
    volumes: dict[tuple[str, int], float],
    markets_counts: dict[tuple[str, int], float],
    win_holder_counts: dict[tuple[str, int], float],
    lose_holder_counts: dict[tuple[str, int], float],
) -> TagProfileAggregate:
    pnl, volume = compute_tag_pnl_volume(
        wallet, tag_ids, tag_weights, pnls, volumes
    )
    return TagProfileAggregate(
        proxy_wallet=wallet,
        win_rate=compute_tag_win_rate(wallet, tag_ids, tag_weights, win_rates),
        pnl_growth=compute_tag_pnl_growth(
            wallet, tag_ids, tag_weights, pnl_growths
        ),
        pnl_stability=compute_tag_pnl_stability(
            wallet, tag_ids, tag_weights, pnl_stabilities
        ),
        pnl=pnl,
        volume=volume,
        markets_count=compute_tag_markets_count(
            wallet, tag_ids, tag_weights, markets_counts
        ),
        win_holder_count=compute_tag_win_holder_count(
            wallet, tag_ids, tag_weights, win_holder_counts
        ),
        lose_holder_count=compute_tag_lose_holder_count(
            wallet, tag_ids, tag_weights, lose_holder_counts
        ),
    )
