from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.gateway import GatewayClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.utils import hour_timestamp_from_ms

logger = structlog.get_logger()

BULK_SIZE = 200


def _open_market_query(**filters: Any) -> dict[str, Any]:
    return {
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "clob_token_ids": {"$ne": []},
        **filters,
    }


def _book_is_empty(book: dict[str, Any]) -> bool:
    return not book.get("bids") and not book.get("asks")


def _best_from_book(book: dict[str, Any]) -> tuple[float, float, int]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid = float(bids[-1]["price"]) if bids else 0.0
    best_ask = float(asks[-1]["price"]) if asks else 1.0
    timestamp = int(book["timestamp"])
    return best_bid, best_ask, timestamp


def _kline_update_op(
    condition_id: str,
    outcome_index: int,
    timestamp_ms: int,
    best_bid: float,
    best_ask: float,
) -> UpdateOne:
    hour_ts = hour_timestamp_from_ms(timestamp_ms)
    return UpdateOne(
        {
            "condition_id": condition_id,
            "outcome_index": outcome_index,
            "time": hour_ts,
        },
        {
            "$min": {"min_bid": best_bid, "min_ask": best_ask},
            "$max": {"max_bid": best_bid, "max_ask": best_ask},
        },
        upsert=True,
    )


async def _bulk_write_klines(db: Database, ops: list[UpdateOne]) -> int:
    if not ops:
        return 0
    await db.klines.bulk_write(ops)
    written = len(ops)
    logger.info("klines_bulk_written", klines=written)
    return written


async def sync_klines(
    db: Database,
    gateway: GatewayClient,
    *,
    settings: Settings,
    stop: asyncio.Event | None = None,
) -> int:
    """Best bid/ask из CLOB orderbook для relevant open markets → prices + klines."""
    cursor = db.markets.find(
        _open_market_query(),
        projection={"condition_id": 1, "clob_token_ids": 1},
    )
    markets = await cursor.to_list(length=None)

    written = 0
    for start in range(0, len(markets), BULK_SIZE):
        if stop is not None and stop.is_set():
            break

        chunk = markets[start : start + BULK_SIZE]
        entries: list[tuple[str, int, str]] = []
        for market in chunk:
            condition_id = market["condition_id"]
            token_ids = market.get("clob_token_ids") or []
            for outcome_index, token_id in enumerate(token_ids):
                entries.append((condition_id, outcome_index, token_id))

        if not entries:
            continue

        books = await gateway.order_books([token_id for _, _, token_id in entries])
        books_by_token = {book["asset_id"]: book for book in books}

        kline_ops: list[UpdateOne] = []
        for condition_id, outcome_index, token_id in entries:
            book = books_by_token.get(token_id)
            if book is None or _book_is_empty(book):
                continue

            best_bid, best_ask, timestamp = _best_from_book(book)
            kline_ops.append(
                _kline_update_op(
                    condition_id, outcome_index, timestamp, best_bid, best_ask
                )
            )

        written += await _bulk_write_klines(db, kline_ops)

    logger.info(
        "klines_sync_done",
        markets=len(markets),
        written=written,
        interrupted=stop is not None and stop.is_set(),
    )
    return written
