from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from pymongo import ReplaceOne, UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.api.gamma import GammaClient
from poly_house.config import Settings
from poly_house.models import Market

logger = structlog.get_logger()


async def get_watermark(db: AsyncDatabase, settings: Settings) -> datetime:
    doc = await db.markets.find_one(
        {"updated_at": {"$ne": None}},
        sort=[("updated_at", -1)],
        projection={"updated_at": 1},
    )
    if doc and doc.get("updated_at"):
        return doc["updated_at"]
    return datetime.now(UTC) - timedelta(days=settings.updated_in_days)


async def _fetch_pages(
    gamma: GammaClient,
    watermark: datetime,
    *,
    page_limit: int,
    max_pages: int | None,
    stop: asyncio.Event | None,
) -> list[list[Market]]:
    """Качаем страницы по убыванию updatedAt, пока не дойдём до watermark."""
    pages: list[list[Market]] = []
    cursor: str | None = None
    while True:
        if stop is not None and stop.is_set():
            break
        raw, next_cursor = await gamma.markets_page(limit=page_limit, cursor=cursor)
        if not raw:
            break
        page = [Market.model_validate(m) for m in raw]
        fresh = [m for m in page if m.updated_at is None or m.updated_at >= watermark]
        pages.append(fresh)
        logger.info(
            "markets_page_fetched",
            page=len(pages),
            markets=len(page),
            fresh=len(fresh),
        )
        if len(fresh) < len(page):
            break  # дошли до watermark
        if next_cursor is None or next_cursor == cursor:
            break  # конец потока
        if max_pages is not None and len(pages) >= max_pages:
            break
        cursor = next_cursor
    return pages


async def _write_page(db: AsyncDatabase, markets: list[Market], synced_at: datetime) -> None:
    """Пишем страницу: tags → events → markets.

    Маркеты — последними: их updated_at двигает watermark, поэтому всё
    связанное должно оказаться в базе раньше.
    """
    tag_ops: dict[str, ReplaceOne] = {}
    event_ops: list[UpdateOne] = []
    market_ops: list[ReplaceOne] = []

    for market in markets:
        for tag in market.tags:
            tag_ops[tag.id] = ReplaceOne({"_id": tag.id}, tag.to_doc(), upsert=True)
        for event in market.events:
            for tag in event.tags:
                tag_ops[tag.id] = ReplaceOne({"_id": tag.id}, tag.to_doc(), upsert=True)
            event_doc = event.to_doc(fallback_tags=market.tags, synced_at=synced_at)
            event_ops.append(
                UpdateOne(
                    {"_id": event.id},
                    {"$set": event_doc, "$addToSet": {"market_ids": market.id}},
                    upsert=True,
                )
            )
        market_ops.append(
            ReplaceOne({"_id": market.id}, market.to_doc(synced_at=synced_at), upsert=True)
        )

    if tag_ops:
        await db.tags.bulk_write(list(tag_ops.values()))
    if event_ops:
        await db.events.bulk_write(event_ops)
    if market_ops:
        await db.markets.bulk_write(market_ops)


async def sync_markets(
    db: AsyncDatabase,
    gamma: GammaClient,
    settings: Settings,
    *,
    page_limit: int | None = None,
    max_pages: int | None = None,
    stop: asyncio.Event | None = None,
) -> list[Market]:
    """Инкрементальный sync маркетов; возвращает записанные маркеты (по возрастанию updated_at)."""
    watermark = await get_watermark(db, settings)
    logger.info("markets_sync_start", watermark=watermark.isoformat())

    pages = await _fetch_pages(
        gamma,
        watermark,
        page_limit=page_limit or settings.markets_page_limit,
        max_pages=max_pages,
        stop=stop,
    )

    written: list[Market] = []
    synced_at = datetime.now(UTC)
    # запись в порядке возрастания updated_at: страницы и маркеты в обратном порядке
    for page in reversed(pages):
        markets = list(reversed(page))
        if not markets:
            continue
        await _write_page(db, markets, synced_at)
        written.extend(markets)

    logger.info("markets_sync_done", markets=len(written))
    return written
