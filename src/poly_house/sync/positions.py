from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from poly_house.api.data import DataClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import Position

logger = structlog.get_logger()


async def _get_holder_watermark(db: Database, proxy_wallet: str) -> int | None:
    doc = await db.positions.find_one(
        {"proxy_wallet": proxy_wallet},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_positions(
    data: DataClient,
    settings: Settings,
    user: str,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /closed-positions (новые первыми) до watermark или max_offset."""
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.closed_positions_max_offset:
        raw = await data.closed_positions_page(
            user=user,
            limit=settings.closed_positions_page_limit,
            offset=offset,
        )
        if not raw:
            break
        fresh = [
            p for p in raw if watermark is None or p["timestamp"] > watermark
        ]
        pages.append(fresh)
        if len(fresh) < len(raw):
            break
        if len(raw) < settings.closed_positions_page_limit:
            break
        offset += settings.closed_positions_page_limit
    return [p for page in reversed(pages) for p in reversed(page)]


async def _write_positions(db: Database, raw_positions: list[dict[str, Any]]) -> int:
    if not raw_positions:
        return 0
    docs = [Position.model_validate(raw).to_doc() for raw in raw_positions]
    await db.positions.insert_many(docs)
    return len(docs)


async def sync_holder_positions(
    db: Database,
    data: DataClient,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Догружает closed positions для holders; returns (holders, positions)."""
    query: dict[str, Any] = {}
    if wallet is not None:
        query["_id"] = wallet

    cursor = db.holders.find(query, projection={"_id": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    holders = await cursor.to_list(length=None)

    logger.info(
        "holder_positions_sync_start",
        holders=len(holders),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    total_holders = 0
    total_positions = 0

    for holder in holders:
        if stop is not None and stop.is_set():
            break

        proxy_wallet = holder["_id"]
        watermark = await _get_holder_watermark(db, proxy_wallet)
        positions = await _fetch_new_positions(data, settings, proxy_wallet, watermark)
        if positions:
            n = await _write_positions(db, positions)
            total_positions += n
        total_holders += 1
        logger.info(
            "holder_positions_synced",
            wallet=proxy_wallet,
            positions=len(positions),
            watermark=watermark,
        )

    logger.info(
        "holder_positions_sync_done",
        holders=total_holders,
        positions=total_positions,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return total_holders, total_positions
