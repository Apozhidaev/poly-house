from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.clients.poly_gate import PolyGateClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models.best_price import BestPrice
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


def _best_price_update_op(
    condition_id: str,
    outcome_index: int,
    timestamp: int,
    best_bid: float,
    best_ask: float,
    synced_at: datetime,
) -> UpdateOne:
    doc = BestPrice(
        condition_id=condition_id,
        outcome_index=outcome_index,
        timestamp=timestamp,
        best_bid=best_bid,
        best_ask=best_ask,
        synced_at=synced_at,
    ).to_doc()
    return UpdateOne(
        {
            "condition_id": condition_id,
            "outcome_index": outcome_index,
            "timestamp": timestamp,
        },
        {"$set": doc},
        upsert=True,
    )


async def _bulk_write_klines(db: Database, ops: list[UpdateOne]) -> int:
    if not ops:
        return 0
    await db.klines.bulk_write(ops)
    written = len(ops)
    logger.info("klines_bulk_written", klines=written)
    return written


async def _bulk_write_best_prices(db: Database, ops: list[UpdateOne]) -> int:
    if not ops:
        return 0
    await db.best_prices.bulk_write(ops)
    written = len(ops)
    logger.info("best_prices_bulk_written", best_prices=written)
    return written


async def _prune_best_prices(db: Database, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.best_prices_window)
    result = await db.best_prices.delete_many({"synced_at": {"$lt": cutoff}})
    deleted = result.deleted_count
    logger.info(
        "best_prices_pruned",
        deleted=deleted,
        cutoff=cutoff.isoformat(),
        window_sec=settings.best_prices_window,
    )
    return deleted


async def sync_orderbook(
    db: Database,
    gateway: PolyGateClient,
    *,
    settings: Settings,
    stop: asyncio.Event | None = None,
) -> int:
    """Best bid/ask из CLOB orderbook для relevant open markets → best_prices + klines."""
    cursor = db.markets.find(
        _open_market_query(),
        projection={"condition_id": 1, "clob_token_ids": 1},
    )
    markets = await cursor.to_list(length=None)

    klines_written = 0
    best_prices_written = 0
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

        synced_at = datetime.now(UTC)
        kline_ops: list[UpdateOne] = []
        best_price_ops: list[UpdateOne] = []
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
            best_price_ops.append(
                _best_price_update_op(
                    condition_id,
                    outcome_index,
                    timestamp,
                    best_bid,
                    best_ask,
                    synced_at,
                )
            )

        klines_written += await _bulk_write_klines(db, kline_ops)
        best_prices_written += await _bulk_write_best_prices(db, best_price_ops)

    await _prune_best_prices(db, settings)

    written = klines_written + best_prices_written
    logger.info(
        "klines_sync_done",
        markets=len(markets),
        klines=klines_written,
        best_prices=best_prices_written,
        written=written,
        interrupted=stop is not None and stop.is_set(),
    )
    return written
