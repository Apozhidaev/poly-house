from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.clob import ClobClient
from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import Holder, Snapshot, SnapshotHolder
from poly_house.sync.profiles import upsert_profiles_for_holders

logger = structlog.get_logger()

OPEN_MARKETS_QUERY = {
    "closed": False,
    "accepting_orders": True,
    "clob_token_ids.0": {"$exists": True},
    "clob_token_ids.1": {"$exists": True},
    "condition_id": {"$ne": None},
}
OPEN_MARKETS_PROJECTION = {"_id": 1, "condition_id": 1, "clob_token_ids": 1}


def _book_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _book_top_price(levels: list[dict[str, Any]] | None) -> float | None:
    if not levels:
        return None
    return _book_float(levels[-1].get("price"))


def parse_top_holders(
    raw: list[dict[str, Any]], clob_token_ids: list[str]
) -> list[list[SnapshotHolder]]:
    token_to_outcome = {token: i for i, token in enumerate(clob_token_ids)}
    result: list[list[SnapshotHolder]] = [[], []]
    for entry in raw:
        outcome_index = token_to_outcome.get(entry["token"])
        if outcome_index is None:
            continue
        for holder in entry["holders"]:
            if not holder.get("displayUsernamePublic"):
                continue
            result[outcome_index].append(
                SnapshotHolder(
                    proxy_wallet=holder["proxyWallet"],
                    amount=holder["amount"],
                )
            )
    return result


def collect_holders(holders_raw: list[dict[str, Any]]) -> dict[str, Holder]:
    holders: dict[str, Holder] = {}
    for entry in holders_raw:
        for raw in entry.get("holders") or []:
            if not raw.get("displayUsernamePublic"):
                continue
            holder = Holder.from_payload(raw)
            holders[holder.proxy_wallet] = holder
    return holders


async def upsert_holders(db: Database, holders: dict[str, Holder]) -> None:
    if not holders:
        return
    await db.holders.bulk_write(
        [
            UpdateOne(
                {"_id": h.proxy_wallet},
                {"$set": h.to_doc()},
                upsert=True,
            )
            for h in holders.values()
        ]
    )


def snapshot_from_book_and_holders(
    market: dict[str, Any],
    book: dict[str, Any],
    holders_raw: list[dict[str, Any]],
    *,
    captured_at: datetime,
) -> Snapshot:
    last_trade = book.get("last_trade_price")
    return Snapshot(
        market_id=market["_id"],
        condition_id=market["condition_id"],
        captured_at=captured_at,
        min_order_size=float(book["min_order_size"]),
        tick_size=float(book["tick_size"]),
        best_bid=_book_top_price(book.get("bids")),
        best_ask=_book_top_price(book.get("asks")),
        last_trade_price=_book_float(last_trade),
        top_holders=parse_top_holders(holders_raw, market["clob_token_ids"]),
    )


async def sync_snapshots(
    db: Database,
    data: DataClient,
    clob: ClobClient,
    pnl: PnlClient,
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Снимки ордербука + holders для открытых маркетов; returns (written, skipped)."""
    logger.info("snapshots_sync_start")
    cursor = db.markets.find(OPEN_MARKETS_QUERY, OPEN_MARKETS_PROJECTION)
    markets = await cursor.to_list(length=None)

    written = 0
    skipped = 0
    captured_at = datetime.now(UTC)
    for market in markets:
        if stop is not None and stop.is_set():
            break
        book = await clob.order_book(market["clob_token_ids"][0])
        if book is None:
            skipped += 1
            continue
        holders_raw = await data.top_holders(
            condition_id=market["condition_id"],
            limit=settings.snapshot_holders_limit,
        )
        if holders_raw is None:
            skipped += 1
            continue
        holders = collect_holders(holders_raw)
        if holders:
            existing = await db.holders.find(
                {"_id": {"$in": list(holders.keys())}},
                projection={"_id": 1},
            ).to_list(length=None)
            existing_ids = {doc["_id"] for doc in existing}
            new_holders = {
                wallet: holder
                for wallet, holder in holders.items()
                if wallet not in existing_ids
            }
        else:
            new_holders = {}
        await upsert_holders(db, holders)
        if new_holders:
            await upsert_profiles_for_holders(db, data, pnl, new_holders, stop=stop)
        snapshot = snapshot_from_book_and_holders(
            market, book, holders_raw, captured_at=captured_at
        )
        await db.snapshots.insert_one(snapshot.to_doc())
        written += 1
        logger.info(
            "snapshot_inserted",
            market_id=market["_id"],
            condition_id=market["condition_id"],
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
        )

    logger.info("snapshots_sync_done", written=written, skipped=skipped, markets=len(markets))
    return written, skipped
