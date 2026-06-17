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
from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.config import Settings
from poly_house.db import Database, create_client, ensure_indexes
from poly_house.sync.markets import sync_markets
from poly_house.sync.snapshots import sync_snapshots
from poly_house.sync.trades import sync_trades

logger = structlog.get_logger()

RunOnce = Callable[[asyncio.Event | None], Awaitable[None]]


@dataclass
class MarketSyncOptions:
    page_limit: int | None = None
    max_pages: int | None = None
    skip_open: bool = False


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


async def run_sync_markets(
    settings: Settings, options: MarketSyncOptions, *, watch: bool = False
) -> None:
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        gamma = GammaClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            markets = await sync_markets(
                db,
                gamma,
                settings,
                page_limit=options.page_limit,
                max_pages=options.max_pages,
                closed_only=options.skip_open,
                stop=stop,
            )
            logger.info(
                "market_sync_run_complete",
                markets=len(markets),
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


async def run_sync_snapshots(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, http: RateLimitedClient) -> RunOnce:
        data = DataClient(http)
        clob = ClobClient(http)

        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            written, skipped = await sync_snapshots(
                db, data, clob, settings, stop=stop
            )
            logger.info(
                "snapshots_sync_run_complete",
                written=written,
                skipped=skipped,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=watch)
