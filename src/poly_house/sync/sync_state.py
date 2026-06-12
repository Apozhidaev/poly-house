from __future__ import annotations

from datetime import UTC, datetime, timedelta

from poly_house.config import Settings
from poly_house.db import Database

MARKETS_WATERMARK_ID = "markets_watermark"
TRADES_WATERMARK_ID = "trades_watermark"


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def default_markets_watermark(settings: Settings) -> str:
    dt = datetime.now(UTC) - timedelta(days=settings.updated_in_days)
    return dt.isoformat().replace("+00:00", "Z")


async def get_markets_watermark(db: Database, settings: Settings) -> str:
    doc = await db.sync_state.find_one({"_id": MARKETS_WATERMARK_ID})
    if doc and doc.get("updated_at"):
        return doc["updated_at"]
    return default_markets_watermark(settings)


async def set_markets_watermark(db: Database, updated_at: str) -> None:
    await db.sync_state.replace_one(
        {"_id": MARKETS_WATERMARK_ID},
        {"_id": MARKETS_WATERMARK_ID, "updated_at": updated_at},
        upsert=True,
    )


async def get_trades_watermark(db: Database) -> datetime | None:
    doc = await db.sync_state.find_one({"_id": TRADES_WATERMARK_ID})
    return doc["synced_at"] if doc else None


async def set_trades_watermark(db: Database, synced_at: datetime) -> None:
    await db.sync_state.replace_one(
        {"_id": TRADES_WATERMARK_ID},
        {"_id": TRADES_WATERMARK_ID, "synced_at": synced_at},
        upsert=True,
    )
