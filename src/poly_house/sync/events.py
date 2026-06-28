from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from pymongo import ReplaceOne, UpdateOne

from poly_house.api.gamma import GammaClient
from poly_house.config import Settings
from poly_house.db import Database, Doc
from poly_house.models import Event, Market
from poly_house.models.deadline import get_deadline_map_for_markets
from poly_house.sync.sync_state import (
    get_events_watermark,
    set_events_watermark,
)

logger = structlog.get_logger()


@dataclass(frozen=True)
class EventWithMarkets:
    event: Event
    markets: list[Market]


def parse_event_with_markets(raw: dict[str, Any]) -> EventWithMarkets:
    event = Event.model_validate(raw)
    markets = [
        Market.model_validate(m)
        for m in raw.get("markets") or []
        if m.get("conditionId")
    ]
    return EventWithMarkets(event=event, markets=markets)


def _keep_event(entry: EventWithMarkets, settings: Settings) -> bool:
    if not entry.markets:
        return False
    if settings.events_neg_risk is not None:
        if entry.event.neg_risk != settings.events_neg_risk:
            return False
    if settings.markets_yes_no_only:
        if any(m.outcomes != ["Yes", "No"] for m in entry.markets):
            return False
    return True


async def _refresh_tag_events_count(db: Database) -> None:
    """Set `events_count` on each tag doc from cached `events` (distinct events per tag)."""
    tag_docs = await db.tags.find({}, projection={"_id": 1}).to_list(length=None)
    if not tag_docs:
        return

    pipeline = [
        {"$match": {"tag_ids.0": {"$exists": True}}},
        {"$unwind": "$tag_ids"},
        {"$group": {"_id": "$tag_ids", "events_count": {"$sum": 1}}},
    ]
    cursor = await db.events.aggregate(pipeline)
    docs = await cursor.to_list(length=None)
    counts: dict[int, int] = {doc["_id"]: doc["events_count"] for doc in docs}

    ops = [
        UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {"events_count": counts.get(doc["_id"], 0)}},
        )
        for doc in tag_docs
    ]
    await db.tags.bulk_write(ops)


async def _fetch_pages(
    gamma: GammaClient,
    watermark: str,
    settings: Settings,
    *,
    page_limit: int,
    max_pages: int | None,
    open_only: bool,
    stop: asyncio.Event | None,
) -> tuple[list[list[EventWithMarkets]], str | None]:
    """Качаем страницы по убыванию updatedAt, пока не дойдём до watermark."""
    pages: list[list[EventWithMarkets]] = []
    max_updated_at: str | None = None
    watermark_dt = datetime.fromisoformat(watermark)
    cursor: str | None = None
    closed = False if open_only else None
    while True:
        if stop is not None and stop.is_set():
            break
        raw, next_cursor = await gamma.events_page(
            limit=page_limit,
            cursor=cursor,
            tag_ids=settings.events_tag_ids,
            exclude_tag_ids=settings.events_exclude_tag_ids,
            closed=closed,
        )
        if not raw:
            break
        page = [parse_event_with_markets(item) for item in raw]
        fresh = [
            entry
            for entry in page
            if datetime.fromisoformat(entry.event.updated_at) > watermark_dt
        ]
        for entry in fresh:
            updated_at = entry.event.updated_at
            if max_updated_at is None or datetime.fromisoformat(
                updated_at
            ) > datetime.fromisoformat(max_updated_at):
                max_updated_at = updated_at
        kept = [entry for entry in fresh if _keep_event(entry, settings)]
        pages.append(kept)
        logger.info(
            "events_page_fetched",
            open_only=open_only,
            page=len(pages),
            events=len(page),
            fresh=len(fresh),
            excluded=len(fresh) - len(kept),
        )
        if len(fresh) < len(page):
            break
        if next_cursor is None or next_cursor == cursor:
            break
        if max_pages is not None and len(pages) >= max_pages:
            break
        cursor = next_cursor
    return pages, max_updated_at


async def _write_page(
    db: Database, entries: list[EventWithMarkets], synced_at: datetime
) -> None:
    """Пишем страницу: tags → events → markets."""
    tag_ops: dict[int, ReplaceOne[Doc]] = {}
    event_ops: list[ReplaceOne[Doc]] = []
    market_ops: list[ReplaceOne[Doc]] = []

    for entry in entries:
        for tag in entry.event.tags:
            tag_ops[tag.id] = ReplaceOne(
                {"_id": tag.id}, tag.to_doc(synced_at=synced_at), upsert=True
            )
        relevant_markets = [market for market in entry.markets if market.relevant]
        deadline_map, calendar_filled = get_deadline_map_for_markets(
            relevant_markets,
            entry.event.start_date or entry.event.created_at,
            entry.event.end_date,
        )

        market_ids: list[int] = [market.id for market in entry.markets]
        relevant_market_ids = [market.id for market in entry.markets if market.relevant]
        event_doc = entry.event.to_doc(
            synced_at=synced_at,
            market_ids=market_ids,
            relevant_market_ids=relevant_market_ids,
            calendar=calendar_filled,
        )
        event_ops.append(ReplaceOne({"_id": entry.event.id}, event_doc, upsert=True))
        for market in entry.markets:
            market_ops.append(
                ReplaceOne(
                    {"_id": market.id},
                    market.to_doc(
                        synced_at=synced_at,
                        event=entry.event,
                        deadline=deadline_map.get(market.id),
                    ),
                    upsert=True,
                )
            )

    if tag_ops:
        await db.tags.bulk_write(list(tag_ops.values()))
    if market_ops:
        await db.markets.bulk_write(market_ops)
    if event_ops:
        await db.events.bulk_write(event_ops)
        await _refresh_tag_events_count(db)


async def sync_events(
    db: Database,
    gamma: GammaClient,
    settings: Settings,
    *,
    page_limit: int | None = None,
    max_pages: int | None = None,
    open_only: bool = False,
    stop: asyncio.Event | None = None,
) -> list[EventWithMarkets]:
    """Инкрементальный sync events + nested markets; возвращает записанные events."""
    watermark = await get_events_watermark(db, settings)
    logger.info("events_sync_start", watermark=watermark, open_only=open_only)

    pages, max_updated_at = await _fetch_pages(
        gamma,
        watermark,
        settings,
        page_limit=page_limit or settings.events_page_limit,
        max_pages=max_pages,
        open_only=open_only,
        stop=stop,
    )

    written: list[EventWithMarkets] = []
    synced_at = datetime.now(UTC)
    for page in reversed(pages):
        entries = list(reversed(page))
        if not entries:
            continue
        await _write_page(db, entries, synced_at)
        written.extend(entries)

    interrupted = stop is not None and stop.is_set()
    if not interrupted and max_updated_at is not None:
        await set_events_watermark(db, max_updated_at)
        logger.info("events_watermark_saved", watermark=max_updated_at)

    logger.info(
        "events_sync_done",
        events=len(written),
        markets=sum(len(e.markets) for e in written),
        interrupted=interrupted,
        open_only=open_only,
    )
    return written


@dataclass
class AddEventResult:
    event_id: int
    markets: int
    tags: int


async def add_event_by_slug(
    db: Database,
    gamma: GammaClient,
    settings: Settings,
    slug: str,
) -> AddEventResult:
    raw = await gamma.event_by_slug(slug=slug)
    if raw is None:
        raise ValueError(f"event not found: {slug}")

    entry = parse_event_with_markets(raw)
    synced_at = datetime.now(UTC)
    await _write_page(db, [entry], synced_at)

    result = AddEventResult(
        event_id=entry.event.id,
        markets=len(entry.markets),
        tags=len(entry.event.tags),
    )
    logger.info("add_event_complete", slug=slug, **result.__dict__)
    return result
