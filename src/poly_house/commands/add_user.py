from __future__ import annotations

from dataclasses import dataclass

import structlog

from poly_house.clients.gateway import GatewayClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import User
from poly_house.commands.home_profiles import sync_home_profiles_for_wallets
from poly_house.commands.profiles import sync_profiles_for_wallets
from poly_house.commands.tag_profiles import (
    load_condition_tag_ids,
    update_tag_profiles_for_wallets,
)

logger = structlog.get_logger()


@dataclass
class AddUserResult:
    user_upserted: bool
    profile_synced: bool
    home_synced: bool
    tag_profiles_written: int


async def add_user(
    db: Database,
    gateway: GatewayClient,
    settings: Settings,
    wallet: str,
) -> AddUserResult:
    raw = await gateway.public_profile(address=wallet)
    if raw is None:
        raise ValueError(f"public profile not found for {wallet}")

    user = User.from_public_profile(raw)
    await db.users.update_one(
        {"_id": user.proxy_wallet},
        {"$set": user.to_update()},
        upsert=True,
    )
    logger.info("add_user_upserted", wallet=user.proxy_wallet)

    wallet_list = [user.proxy_wallet]
    await sync_profiles_for_wallets(
        db, gateway, settings, wallet_list
    )
    await sync_home_profiles_for_wallets(
        db, gateway, settings, wallet_list
    )

    tag_profiles_written = 0
    condition_tags = await load_condition_tag_ids(db)
    if condition_tags:
        _, tag_profiles_written = await update_tag_profiles_for_wallets(
            db, wallet_list, condition_tags
        )

    result = AddUserResult(
        user_upserted=True,
        profile_synced=True,
        home_synced=True,
        tag_profiles_written=tag_profiles_written,
    )
    logger.info("add_user_complete", **result.__dict__)
    return result
