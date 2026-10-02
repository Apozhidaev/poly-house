from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/trades", tags=["trades"])


def _build_trades_query(
    *,
    condition_id: str | None,
    outcome_index: int | None,
    user: str | None,
    side: str | None,
    from_ts: int | None,
    to_ts: int | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if condition_id:
        query["condition_id"] = condition_id
    if outcome_index is not None:
        query["outcome_index"] = outcome_index
    if user:
        query["proxy_wallet"] = user
    if side:
        query["side"] = side.upper()
    timestamp: dict[str, int] = {}
    if from_ts is not None:
        timestamp["$gte"] = from_ts
    if to_ts is not None:
        timestamp["$lte"] = to_ts
    if timestamp:
        query["timestamp"] = timestamp
    return query


def _normalize_trade_to_outcome(
    doc: dict[str, Any], target_outcome_index: int
) -> dict[str, Any]:
    result = dict(doc)
    if result.get("outcome_index") != target_outcome_index:
        side = result.get("side")
        if side == "BUY":
            result["side"] = "SELL"
        elif side == "SELL":
            result["side"] = "BUY"
        price = result.get("price")
        if price is not None:
            result["price"] = 1 - price
    result.pop("outcome", None)
    result.pop("outcome_index", None)
    return result


def _mid_prices(
    rows: list[dict[str, Any]], *, outcome_index: int = 0
) -> list[dict[str, Any]]:
    normalized = [_normalize_trade_to_outcome(row, outcome_index) for row in rows]
    by_timestamp: dict[int, float] = {}
    last_buy: float | None = None
    last_sell: float | None = None
    for trade in normalized:
        price = trade.get("price")
        if price is None:
            continue
        side = trade.get("side")
        if side == "BUY":
            prev_opposite = last_sell
            last_buy = price
        elif side == "SELL":
            prev_opposite = last_buy
            last_sell = price
        else:
            raise HTTPException(status_code=500, detail=f"unexpected trade side: {side!r}")
        mid = (price + prev_opposite) / 2 if prev_opposite is not None else price
        by_timestamp[trade["timestamp"]] = mid
    return [
        {"timestamp": timestamp, "price": price}
        for timestamp, price in sorted(by_timestamp.items())
    ]


@router.get("/mid-prices")
async def mid_prices(
    db: Db, condition_id: str, outcome_index: int = 0
) -> dict[str, Any]:
    """Mid price по трейдам, нормализованным к outcome_index: (current + prev opposite side) / 2."""
    cursor = db.trades.find(
        {"condition_id": condition_id},
        {"timestamp": 1, "price": 1, "side": 1, "outcome_index": 1, "_id": 0},
    ).sort("timestamp", 1)
    rows = await cursor.to_list(length=None)
    return {"items": _mid_prices(rows, outcome_index=outcome_index)}


@router.get("/to-outcome")
async def trades_to_outcome(
    db: Db,
    page: Page,
    target_outcome_index: int = 0,
    condition_id: str | None = None,
    user: str | None = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Трейды, приведённые к target_outcome_index: инверсия side/price для другого исхода."""
    if target_outcome_index not in (0, 1):
        raise HTTPException(
            status_code=422, detail="target_outcome_index must be 0 or 1"
        )
    query = _build_trades_query(
        condition_id=condition_id,
        outcome_index=None,
        user=user,
        side=side,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    cursor = (
        db.trades.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    rows = await cursor.to_list(page.limit)
    return {
        "items": [
            _normalize_trade_to_outcome(row, target_outcome_index) for row in rows
        ]
    }


@router.get("")
async def list_trades(
    db: Db,
    page: Page,
    condition_id: str | None = None,
    outcome_index: int | None = None,
    user: str | None = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    query = _build_trades_query(
        condition_id=condition_id,
        outcome_index=outcome_index,
        user=user,
        side=side,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    cursor = (
        db.trades.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}
