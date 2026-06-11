from __future__ import annotations

import asyncio

import structlog
from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.api.data import DataClient
from poly_house.config import Settings
from poly_house.models import Market, Trade, User

logger = structlog.get_logger()


async def _get_watermark(db: AsyncDatabase, condition_id: str) -> int | None:
    doc = await db.trades.find_one(
        {"conditionId": condition_id},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_trades(
    data: DataClient, settings: Settings, condition_id: str, watermark: int | None
) -> list[Trade]:
    """Качаем страницы (новые первыми) до watermark или потолка offset, возвращаем по возрастанию timestamp."""
    pages: list[list[Trade]] = []
    offset = 0
    while offset <= settings.trades_max_offset:
        raw = await data.trades_page(
            condition_id=condition_id, limit=settings.trades_page_limit, offset=offset
        )
        if not raw:
            break
        trades = [Trade.model_validate(t) for t in raw]
        fresh = [t for t in trades if watermark is None or t.timestamp > watermark]
        pages.append(fresh)
        if len(fresh) < len(trades):
            break  # дошли до watermark
        if len(raw) < settings.trades_page_limit:
            break  # конец истории
        offset += settings.trades_page_limit
    return [t for page in reversed(pages) for t in reversed(page)]


async def _write_trades(db: AsyncDatabase, trades: list[Trade]) -> int:
    """Пишем users, затем трейды (трейды двигают watermark — последними)."""
    users: dict[str, User] = {}
    for trade in trades:
        users[trade.proxyWallet] = User.from_trade(trade)
    if users:
        await db.users.bulk_write(
            [
                UpdateOne({"_id": u.proxyWallet}, {"$set": u.to_update()}, upsert=True)
                for u in users.values()
            ]
        )
    await db.trades.insert_many([t.to_doc() for t in trades])
    return len(users)


async def sync_trades(
    db: AsyncDatabase,
    data: DataClient,
    settings: Settings,
    markets: list[Market],
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Догружает трейды по маркетам прогона; возвращает (трейдов, пользователей)."""
    total_trades = 0
    total_users = 0
    for market in markets:
        if stop is not None and stop.is_set():
            break
        if not market.conditionId:
            continue
        watermark = await _get_watermark(db, market.conditionId)
        trades = await _fetch_new_trades(data, settings, market.conditionId, watermark)
        if not trades:
            continue
        n_users = await _write_trades(db, trades)
        total_trades += len(trades)
        total_users += n_users
        logger.info(
            "market_trades_synced",
            market=market.slug or market.id,
            trades=len(trades),
        )
    logger.info("trades_sync_done", trades=total_trades, users=total_users)
    return total_trades, total_users
