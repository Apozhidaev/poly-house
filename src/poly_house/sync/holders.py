from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import User
from poly_house.models.market import RELEVANT_MARKET_FILTER
from poly_house.sync.profiles import (
    WalletIdentity,
    sync_home_profiles_for_wallets,
    sync_profiles_for_wallets,
    wallets_needing_home_profile_sync,
    wallets_needing_profile_sync,
)
from poly_house.sync.tag_profiles import (
    load_condition_tag_ids,
    update_tag_profiles_for_wallets,
)

logger = structlog.get_logger()

OPEN_MARKETS_QUERY = {
    **RELEVANT_MARKET_FILTER,
    "closed": False,
    "accepting_orders": True,
    "clob_token_ids.0": {"$exists": True},
    "clob_token_ids.1": {"$exists": True},
}
OPEN_MARKETS_PROJECTION = {
    "_id": 1,
    "condition_id": 1,
}


def collect_holders(holders_raw: list[dict[str, Any]]) -> dict[str, WalletIdentity]:
    holders: dict[str, WalletIdentity] = {}
    for entry in holders_raw:
        for raw in entry.get("holders") or []:
            if not raw.get("displayUsernamePublic"):
                continue
            holders[raw["proxyWallet"]] = WalletIdentity(
                name=raw["name"],
                bio=raw.get("bio") or "",
                profile_image=raw.get("profileImage") or "",
            )
    return holders


async def _upsert_users(db: Database, holders: dict[str, WalletIdentity]) -> None:
    if not holders:
        return
    await db.users.bulk_write(
        [
            UpdateOne(
                {"_id": wallet},
                {
                    "$set": User(
                        proxy_wallet=wallet,
                        name=identity.name,
                        bio=identity.bio,
                        profile_image=identity.profile_image,
                    ).to_update()
                },
                upsert=True,
            )
            for wallet, identity in holders.items()
        ]
    )


async def sync_holder_profiles(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Holders открытых маркетов → inline sync profiles/home_profiles/tag_profiles."""
    logger.info("holder_sync_start")
    cursor = db.markets.find(OPEN_MARKETS_QUERY, OPEN_MARKETS_PROJECTION)
    markets = await cursor.to_list(length=None)
    condition_tags = await load_condition_tag_ids(db)

    processed = 0
    skipped = 0
    for market in markets:
        if stop is not None and stop.is_set():
            break
        holders_raw = await data.top_holders(
            condition_id=market["condition_id"],
            limit=settings.holders_limit,
        )
        if holders_raw is None:
            skipped += 1
            continue
        holders = collect_holders(holders_raw)
        await _upsert_users(db, holders)
        if holders:
            wallet_list = list(holders.keys())
            profile_stale = await wallets_needing_profile_sync(
                db, settings, wallet_list
            )
            if profile_stale:
                await sync_profiles_for_wallets(
                    db,
                    data,
                    pnl,
                    settings,
                    profile_stale,
                    stop=stop,
                )
            home_stale = await wallets_needing_home_profile_sync(
                db, settings, wallet_list
            )
            if home_stale:
                await sync_home_profiles_for_wallets(
                    db, data, settings, home_stale, stop=stop
                )
            if condition_tags and home_stale:
                await update_tag_profiles_for_wallets(
                    db,
                    list(home_stale),
                    condition_tags,
                    stop=stop,
                )
        processed += 1
        logger.info(
            "holder_sync_market_done",
            market_id=market["_id"],
            condition_id=market["condition_id"],
            holders=len(holders),
        )

    logger.info(
        "holder_sync_done",
        processed=processed,
        skipped=skipped,
        markets=len(markets),
    )
    return processed, skipped
