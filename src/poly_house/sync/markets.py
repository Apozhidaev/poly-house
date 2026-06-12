from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from pymongo import ReplaceOne, UpdateOne

from poly_house.api.gamma import GammaClient
from poly_house.config import Settings
from poly_house.db import Database, Doc
from poly_house.models import Market
from poly_house.sync.sync_state import get_markets_watermark, parse_iso, set_markets_watermark

logger = structlog.get_logger()


async def _fetch_pages(
    gamma: GammaClient,
    watermark: str,
    *,
    page_limit: int,
    max_pages: int | None,
    tag_ids: list[int],
    exclude_tag_ids: list[int],
    closed_only: bool,
    stop: asyncio.Event | None,
) -> tuple[list[list[Market]], str | None]:
    """Качаем страницы по убыванию updatedAt, пока не дойдём до watermark."""
    excluded = {str(t) for t in exclude_tag_ids}
    pages: list[list[Market]] = []
    max_updated_at: str | None = None
    watermark_dt = parse_iso(watermark)
    cursor: str | None = None
    while True:
        if stop is not None and stop.is_set():
            break
        raw, next_cursor = await gamma.markets_page(
            limit=page_limit,
            cursor=cursor,
            tag_ids=tag_ids,
            closed=True if closed_only else None,
        )
        if not raw:
            break
        page = [Market.model_validate(m) for m in raw]
        fresh = [
            m
            for m in page
            if m.updated_at is None or parse_iso(m.updated_at) > watermark_dt
        ]
        for m in fresh:
            if m.updated_at is not None and (
                max_updated_at is None
                or parse_iso(m.updated_at) > parse_iso(max_updated_at)
            ):
                max_updated_at = m.updated_at
        kept = [m for m in fresh if not excluded & {t.id for t in m.tags}]
        pages.append(kept)
        logger.info(
            "markets_page_fetched",
            page=len(pages),
            markets=len(page),
            fresh=len(fresh),
            excluded=len(fresh) - len(kept),
        )
        if len(fresh) < len(page):
            break  # дошли до watermark
        if next_cursor is None or next_cursor == cursor:
            break  # конец потока
        if max_pages is not None and len(pages) >= max_pages:
            break
        cursor = next_cursor
    return pages, max_updated_at


async def _write_page(db: Database, markets: list[Market], synced_at: datetime) -> None:
    """Пишем страницу: tags → events → markets."""
    tag_ops: dict[str, ReplaceOne[Doc]] = {}
    event_ops: list[UpdateOne] = []
    market_ops: list[ReplaceOne[Doc]] = []

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
    db: Database,
    gamma: GammaClient,
    settings: Settings,
    *,
    page_limit: int | None = None,
    max_pages: int | None = None,
    closed_only: bool = False,
    stop: asyncio.Event | None = None,
) -> list[Market]:
    """Инкрементальный sync маркетов; возвращает записанные маркеты (по возрастанию updated_at)."""
    watermark = await get_markets_watermark(db, settings)
    logger.info("markets_sync_start", watermark=watermark)

    pages, max_updated_at = await _fetch_pages(
        gamma,
        watermark,
        page_limit=page_limit or settings.markets_page_limit,
        max_pages=max_pages,
        tag_ids=settings.markets_tag_ids,
        exclude_tag_ids=settings.markets_exclude_tag_ids,
        closed_only=closed_only,
        stop=stop,
    )

    written: list[Market] = []
    synced_at = datetime.now(UTC)
    for page in reversed(pages):
        markets = list(reversed(page))
        if not markets:
            continue
        await _write_page(db, markets, synced_at)
        written.extend(markets)

    interrupted = stop is not None and stop.is_set()
    if not interrupted and max_updated_at is not None:
        await set_markets_watermark(db, max_updated_at)
        logger.info("markets_watermark_saved", watermark=max_updated_at)

    logger.info("markets_sync_done", markets=len(written), interrupted=interrupted)
    return written
