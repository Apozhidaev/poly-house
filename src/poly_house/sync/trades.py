from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.data import DataClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import Trade, User
from poly_house.sync.sync_state import get_trades_watermark, set_trades_watermark

logger = structlog.get_logger()


async def _get_market_watermark(db: Database, condition_id: str) -> int | None:
    doc = await db.trades.find_one(
        {"condition_id": condition_id},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_trades(
    data: DataClient, settings: Settings, condition_id: str, watermark: int | None
) -> list[dict[str, Any]]:
    """Качаем страницы (новые первыми) до watermark или потолка offset, возвращаем по возрастанию timestamp."""
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.trades_max_offset:
        raw = await data.trades_page(
            condition_id=condition_id, limit=settings.trades_page_limit, offset=offset
        )
        if not raw:
            break
        fresh = [
            t for t in raw if watermark is None or t["timestamp"] > watermark # тут мы можем потерять новый трейд, если timestamp == watermark (пока что не критично)
        ]
        pages.append(fresh)
        if len(fresh) < len(raw):
            break  # дошли до watermark
        if len(raw) < settings.trades_page_limit:
            break  # конец истории
        offset += settings.trades_page_limit
    return [t for page in reversed(pages) for t in reversed(page)]


async def _write_trades(db: Database, raw_trades: list[dict[str, Any]]) -> int:
    """Пишем users, затем трейды (трейды двигают watermark — последними)."""
    users: dict[str, User] = {}
    trade_docs: list[dict[str, Any]] = []
    for raw in raw_trades:
        trade = Trade.model_validate(raw)
        users[trade.proxy_wallet] = User.from_payload(raw)
        trade_docs.append(trade.to_doc())
    if users:
        await db.users.bulk_write(
            [
                UpdateOne(
                    {"_id": u.proxy_wallet},
                    {
                        "$set": {
                            k: v
                            for k, v in u.to_update().items()
                            if k != "last_trade_time"
                        },
                        "$max": {"last_trade_time": u.last_trade_time},
                    },
                    upsert=True,
                )
                for u in users.values()
            ]
        )
    await db.trades.insert_many(trade_docs)
    return len(users)


async def _select_markets(
    db: Database, watermark: datetime | None
) -> list[dict[str, Any]]:
    """Маркеты, синхронизированные позже watermark'а трейдов."""
    query: dict[str, Any] = {}
    if watermark is not None:
        query["synced_at"] = {"$gt": watermark}
    cursor = db.markets.find(
        query, projection={"condition_id": 1, "slug": 1, "synced_at": 1}
    )
    return await cursor.to_list(length=None)


async def sync_trades(
    db: Database,
    data: DataClient,
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Догружает трейды по маркетам, обновлённым после watermark'а; возвращает (трейдов, пользователей)."""
    watermark = await get_trades_watermark(db)
    markets = await _select_markets(db, watermark)
    logger.info(
        "trades_sync_start",
        watermark=watermark.isoformat() if watermark else None,
        markets=len(markets),
    )

    total_trades = 0
    total_users = 0
    max_synced_at: datetime | None = None
    for market in markets:
        if stop is not None and stop.is_set():
            break
        market_watermark = await _get_market_watermark(db, market["condition_id"])
        trades = await _fetch_new_trades(
            data, settings, market["condition_id"], market_watermark
        )
        if trades:
            n_users = await _write_trades(db, trades)
            total_trades += len(trades)
            total_users += n_users
        logger.info(
            "market_trades_synced",
            market=market.get("slug") or market["_id"],
            trades=len(trades),
        )
        synced_at = market.get("synced_at")
        if synced_at is not None and (max_synced_at is None or synced_at > max_synced_at):
            max_synced_at = synced_at

    interrupted = stop is not None and stop.is_set()
    if not interrupted and max_synced_at is not None:
        await set_trades_watermark(db, max_synced_at)
        logger.info("trades_watermark_saved", watermark=max_synced_at.isoformat())

    logger.info(
        "trades_sync_done",
        trades=total_trades,
        users=total_users,
        interrupted=interrupted,
    )
    return total_trades, total_users
