from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass

import structlog
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.api.data import DataClient
from poly_house.api.gamma import GammaClient
from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter
from poly_house.config import Settings
from poly_house.db import create_client, ensure_indexes
from poly_house.sync.markets import sync_markets
from poly_house.sync.trades import sync_trades

logger = structlog.get_logger()


@dataclass
class SyncOptions:
    page_limit: int | None = None
    max_pages: int | None = None
    skip_trades: bool = False
    skip_open: bool = False


async def run_once(
    db: AsyncDatabase,
    gamma: GammaClient,
    data: DataClient,
    settings: Settings,
    options: SyncOptions,
    *,
    stop: asyncio.Event | None = None,
) -> None:
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
    n_trades = n_users = 0
    if not options.skip_trades and not (stop is not None and stop.is_set()):
        n_trades, n_users = await sync_trades(db, data, settings, markets, stop=stop)
    logger.info(
        "sync_run_complete",
        markets=len(markets),
        trades=n_trades,
        users=n_users,
        seconds=round(time.monotonic() - started, 1),
    )


async def run_sync(settings: Settings, options: SyncOptions, *, watch: bool = False) -> None:
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    rate_limiter = RateLimiter(
        interval=settings.rate_limit_interval, count=settings.rate_limit_count
    )
    try:
        async with RateLimitedClient(rate_limiter) as http:
            gamma = GammaClient(http, settings.gamma_base_url)
            data = DataClient(http, settings.data_base_url)
            if watch:
                await _watch_loop(db, gamma, data, settings, options)
            else:
                await run_once(db, gamma, data, settings, options)
    finally:
        await client.close()


async def _watch_loop(
    db: AsyncDatabase,
    gamma: GammaClient,
    data: DataClient,
    settings: Settings,
    options: SyncOptions,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("watch_started", interval=settings.watch_interval)
    try:
        while not stop.is_set():
            await run_once(db, gamma, data, settings, options, stop=stop)
            if stop.is_set():
                break
            logger.info("watch_sleep", seconds=settings.watch_interval)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.watch_interval)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        logger.info("watch_stopped")
