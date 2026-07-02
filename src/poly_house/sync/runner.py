from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from poly_house.api.data import DataClient
from poly_house.api.gateway import GatewayClient
from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.config import Settings
from poly_house.db import Database, create_client, ensure_indexes
from poly_house.sync.add_user import add_user
from poly_house.sync.data_fix import run_data_fix as execute_data_fix
from poly_house.sync.events import add_event_by_slug, sync_events
from poly_house.sync.home_profiles import sync_home_profiles
from poly_house.sync.profiles import sync_profiles
from poly_house.sync.holders import sync_holder_profiles
from poly_house.sync.tag_profiles import update_tag_profiles
from poly_house.sync.trades import sync_trades

logger = structlog.get_logger()

RunOnce = Callable[[asyncio.Event | None], Awaitable[None]]
DbRunOnce = Callable[[Database, asyncio.Event | None], Awaitable[None]]
GatewayRunOnce = Callable[[Database, RateLimitedClient, GatewayClient], RunOnce]


@dataclass
class EventSyncOptions:
    open_only: bool = False
    page_limit: int | None = None
    max_pages: int | None = None


async def _run(
    settings: Settings,
    run_once: Callable[[Database, RateLimitedClient], RunOnce],
    *,
    watch: bool,
) -> None:
    """Общая обвязка: клиенты, индексы, одиночный прогон или watch-цикл."""
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    try:
        async with RateLimitedClient() as http:
            once = run_once(db, http)
            if watch:
                await _watch_loop(once, settings)
            else:
                await once(None)
    finally:
        await client.close()


async def _run_with_gateway(
    settings: Settings,
    run_once: GatewayRunOnce,
    *,
    watch: bool,
) -> None:
    """Обвязка для команд с Gateway + Data API."""
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    try:
        async with RateLimitedClient() as http:
            async with GatewayClient(settings) as gateway:
                once = run_once(db, http, gateway)
                if watch:
                    await _watch_loop(once, settings)
                else:
                    await once(None)
    finally:
        await client.close()


async def _run_db_only(
    settings: Settings,
    run_once: DbRunOnce,
    *,
    watch: bool,
) -> None:
    """Обвязка для команд без внешних API."""
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    try:
        if watch:
            await _watch_loop(lambda stop: run_once(db, stop), settings)
        else:
            await run_once(db, None)
    finally:
        await client.close()


async def _watch_loop(once: RunOnce, settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("watch_started", interval=settings.watch_interval)
    try:
        while not stop.is_set():
            await once(stop)
            if stop.is_set():
                break
            logger.info("watch_sleep", seconds=settings.watch_interval)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.watch_interval)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        logger.info("watch_stopped")


async def run_sync_events(
    settings: Settings, options: EventSyncOptions, *, watch: bool = False
) -> None:
    def make_once(
        db: Database, _http: RateLimitedClient, gateway: GatewayClient
    ) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            entries = await sync_events(
                db,
                gateway,
                settings,
                page_limit=options.page_limit,
                max_pages=options.max_pages,
                open_only=options.open_only,
                stop=stop,
            )
            logger.info(
                "event_sync_run_complete",
                events=len(entries),
                markets=sum(len(e.markets) for e in entries),
                open_only=options.open_only,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run_with_gateway(settings, make_once, watch=watch)


async def run_sync_trades(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        data = DataClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            n_trades, n_users = await sync_trades(db, data, settings, stop=stop)
            logger.info(
                "trade_sync_run_complete",
                trades=n_trades,
                users=n_users,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=watch)


@dataclass
class SyncProfileOptions:
    wallet: str | None = None
    limit: int | None = None


async def run_sync_profile(
    settings: Settings,
    options: SyncProfileOptions,
    *,
    watch: bool = False,
) -> None:
    def make_once(
        db: Database, http: RateLimitedClient, gateway: GatewayClient
    ) -> RunOnce:
        data = DataClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            await sync_profiles(
                db,
                data,
                gateway,
                settings,
                wallet=options.wallet,
                limit=options.limit,
                stop=stop,
            )
            logger.info(
                "profile_sync_run_complete",
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run_with_gateway(settings, make_once, watch=watch)


async def run_sync_home_profile(
    settings: Settings,
    options: SyncProfileOptions,
    *,
    watch: bool = False,
) -> None:
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        data = DataClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            await sync_home_profiles(
                db,
                data,
                settings,
                wallet=options.wallet,
                limit=options.limit,
                stop=stop,
            )
            logger.info(
                "home_profile_sync_run_complete",
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=watch)


async def run_sync_holder(settings: Settings, *, watch: bool = False) -> None:
    def make_once(
        db: Database, http: RateLimitedClient, gateway: GatewayClient
    ) -> RunOnce:
        data = DataClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            processed, skipped = await sync_holder_profiles(
                db, data, gateway, settings, stop=stop
            )
            logger.info(
                "holder_sync_run_complete",
                processed=processed,
                skipped=skipped,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run_with_gateway(settings, make_once, watch=watch)


@dataclass
class TagProfileSyncOptions:
    wallet: str | None = None
    limit: int | None = None


async def run_update_tag_profiles(
    settings: Settings,
    options: TagProfileSyncOptions,
    *,
    watch: bool = False,
) -> None:
    async def once(db: Database, stop: asyncio.Event | None) -> None:
        started = time.monotonic()
        profiles, tag_profiles = await update_tag_profiles(
            db,
            wallet=options.wallet,
            limit=options.limit,
            stop=stop,
        )
        logger.info(
            "tag_profile_update_run_complete",
            profiles=profiles,
            tag_profiles=tag_profiles,
            seconds=round(time.monotonic() - started, 1),
        )

    await _run_db_only(settings, once, watch=watch)


async def run_add_user(settings: Settings, wallet: str) -> None:
    def make_once(
        db: Database, http: RateLimitedClient, gateway: GatewayClient
    ) -> RunOnce:
        data = DataClient(http)

        async def once(_stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            result = await add_user(db, gateway, data, settings, wallet)
            logger.info(
                "add_user_run_complete",
                wallet=wallet,
                **result.__dict__,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run_with_gateway(settings, make_once, watch=False)


async def run_add_event(settings: Settings, slug: str) -> None:
    def make_once(
        db: Database, _http: RateLimitedClient, gateway: GatewayClient
    ) -> RunOnce:
        async def once(_stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            result = await add_event_by_slug(db, gateway, settings, slug)
            logger.info(
                "add_event_run_complete",
                slug=slug,
                **result.__dict__,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run_with_gateway(settings, make_once, watch=False)


async def run_data_fix(settings: Settings, *, apply: bool = False) -> None:
    async def once(db: Database, _stop: asyncio.Event | None) -> None:
        started = time.monotonic()
        result = await execute_data_fix(db, apply=apply)
        logger.info(
            "data_fix_run_complete",
            rows=len(result.rows),
            apply=apply,
            activities_deleted=result.activities_deleted,
            activity_caches_deleted=result.activity_caches_deleted,
            seconds=round(time.monotonic() - started, 1),
        )

    await _run_db_only(settings, once, watch=False)
