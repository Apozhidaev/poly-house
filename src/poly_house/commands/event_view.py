from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog
from pymongo import ReplaceOne

from poly_house.db import Database, Doc
from poly_house.utils.trade_stats import (
    compute_market_trade_stats,
    load_latest_best_prices,
)

logger = structlog.get_logger()

BULK_SIZE = 100


def _as_datetime(value: Any) -> Any:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


async def update_event_view(
    db: Database,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int, int]:
    """Rebuild events_view: open events with open markets + trade_stats + best prices."""
    events = await db.events.find({"closed": False}).to_list(length=None)
    if stop is not None and stop.is_set():
        return 0, 0, 0

    market_ids: list[int] = []
    for event in events:
        market_ids.extend(event.get("market_ids") or [])

    markets_by_id: dict[int, dict[str, Any]] = {}
    if market_ids:
        market_docs = await db.markets.find(
            {"_id": {"$in": market_ids}, "closed": False}
        ).to_list(length=None)
        markets_by_id = {doc["_id"]: doc for doc in market_docs}

    condition_ids = list(
        {
            doc["condition_id"]
            for doc in markets_by_id.values()
            if doc.get("condition_id")
        }
    )
    latest_prices = await load_latest_best_prices(db, condition_ids)

    trade_stats_by_condition: dict[str, dict[str, Any]] = {}
    for condition_id in condition_ids:
        if stop is not None and stop.is_set():
            break
        trade_stats_by_condition[condition_id] = await compute_market_trade_stats(
            db, condition_id
        )

    ops: list[ReplaceOne[Doc]] = []
    open_event_ids: list[int] = []
    markets_written = 0
    for event in events:
        if stop is not None and stop.is_set():
            break
        event_id = event["_id"]
        open_event_ids.append(event_id)
        nested_markets: list[dict[str, Any]] = []
        for market_id in event.get("market_ids") or []:
            market = markets_by_id.get(market_id)
            if market is None:
                continue
            condition_id = market["condition_id"]
            trade_stats = trade_stats_by_condition.get(condition_id)
            if trade_stats is None:
                continue
            bids, asks = latest_prices.get(condition_id, ([None, None], [None, None]))
            market_doc = dict(market)
            market_doc["trade_stats"] = trade_stats
            market_doc["best_bids"] = list(bids)
            market_doc["best_asks"] = list(asks)
            market_doc["created_at"] = _as_datetime(market_doc.get("created_at"))
            market_doc["updated_at"] = _as_datetime(market_doc.get("updated_at"))
            nested_markets.append(market_doc)
        markets_written += len(nested_markets)
        doc = dict(event)
        doc["created_at"] = _as_datetime(doc.get("created_at"))
        doc["updated_at"] = _as_datetime(doc.get("updated_at"))
        doc["markets"] = nested_markets
        ops.append(ReplaceOne({"_id": event_id}, doc, upsert=True))

    written_events = 0
    for start in range(0, len(ops), BULK_SIZE):
        if stop is not None and stop.is_set():
            break
        chunk = ops[start : start + BULK_SIZE]
        if chunk:
            await db.events_view.bulk_write(chunk)
            written_events += len(chunk)

    deleted = 0
    if stop is None or not stop.is_set():
        result = await db.events_view.delete_many({"_id": {"$nin": open_event_ids}})
        deleted = result.deleted_count

    logger.info(
        "event_view_updated",
        events=written_events,
        markets=markets_written,
        deleted=deleted,
        interrupted=stop is not None and stop.is_set(),
    )
    return written_events, markets_written, deleted
