from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from poly_house.api.clob import ClobClient
from poly_house.api.data import DataClient
from poly_house.api.gamma import GammaClient
from poly_house.api.pnl import PnlClient
from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.config import Settings
from poly_house.db import Database, create_client, ensure_indexes
from poly_house.sync.events import sync_events
from poly_house.sync.profiles import sync_home_profiles, sync_profiles
from poly_house.sync.holder_snapshots import sync_holder_snapshots
from poly_house.sync.tag_profiles import update_tag_profiles
from poly_house.sync.trades import sync_trades

logger = structlog.get_logger()

RunOnce = Callable[[asyncio.Event | None], Awaitable[None]]
DbRunOnce = Callable[[Database, asyncio.Event | None], Awaitable[None]]


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
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        gamma = GammaClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            entries = await sync_events(
                db,
                gamma,
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

    await _run(settings, make_once, watch=watch)


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
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        data = DataClient(http)
        pnl = PnlClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            await sync_profiles(
                db,
                data,
                pnl,
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

    await _run(settings, make_once, watch=watch)


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


async def run_sync_holder_snapshots(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        data = DataClient(http)
        clob = ClobClient(http)
        pnl = PnlClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            written, skipped = await sync_holder_snapshots(
                db, data, clob, pnl, settings, stop=stop
            )
            logger.info(
                "holder_snapshots_sync_run_complete",
                written=written,
                skipped=skipped,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=watch)


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
