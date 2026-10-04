from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from poly_house.clients.poly_gate import PolyGateClient
from poly_house.config import Settings
from poly_house.db import Database, create_client, ensure_indexes
from poly_house.commands.add_user import add_user
from poly_house.commands.events import (
    RefreshEventsBulkResult,
    refresh_events,
    sync_events,
)
from poly_house.commands.home_profiles import (
    sync_activity_for_wallet_event,
    sync_home_profiles,
)
from poly_house.commands.profiles import sync_profiles
from poly_house.commands.holders import sync_holder_profiles
from poly_house.commands.klines import sync_orderbook
from poly_house.commands.tag_profiles import update_tag_profiles
from poly_house.commands.trades import sync_trades

logger = structlog.get_logger()

RunOnce = Callable[[asyncio.Event | None], Awaitable[None]]
DbRunOnce = Callable[[Database, asyncio.Event | None], Awaitable[None]]
GatewayRunOnce = Callable[[Database, PolyGateClient], RunOnce]


@dataclass
class EventSyncOptions:
    open_only: bool = False
    page_limit: int | None = None
    max_pages: int | None = None


async def _run(
    settings: Settings,
    run_once: GatewayRunOnce,
    *,
    watch: bool,
    watch_interval: float | None = None,
) -> None:
    """Обвязка для команд с Gateway."""
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    try:
        async with PolyGateClient(settings) as gateway:
            once = run_once(db, gateway)
            if watch:
                if watch_interval is None:
                    raise ValueError("watch_interval is required when watch=True")
                await _watch_loop(once, interval=watch_interval)
            else:
                await once(None)
    finally:
        await client.close()


async def _run_db_only(settings: Settings, run_once: DbRunOnce) -> None:
    """Обвязка для команд без внешних API."""
    client = create_client(settings)
    db = client[settings.mongo_db]
    await ensure_indexes(db)

    try:
        await run_once(db, None)
    finally:
        await client.close()


async def _watch_loop(once: RunOnce, *, interval: float) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # add_signal_handler не реализован на Windows — регистрируем best-effort,
    # на Windows остановка идёт через KeyboardInterrupt (Ctrl+C).
    registered: list[int] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            registered.append(sig)
        except NotImplementedError:
            pass
    logger.info("watch_started", interval=interval)
    try:
        while not stop.is_set():
            started = time.monotonic()
            await once(stop)
            if stop.is_set():
                break
            elapsed = time.monotonic() - started
            sleep = max(0.0, interval - elapsed)
            logger.info(
                "watch_sleep",
                interval=interval,
                elapsed=round(elapsed, 1),
                seconds=round(sleep, 1),
            )
            if sleep > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=sleep)
    except KeyboardInterrupt:
        pass
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)
        logger.info("watch_stopped")


async def run_sync_events(
    settings: Settings, options: EventSyncOptions, *, watch: bool = False
) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
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

    await _run(
        settings,
        make_once,
        watch=watch,
        watch_interval=settings.sync_event_watch_interval,
    )


async def run_sync_trades(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            n_trades, n_users = await sync_trades(db, gateway, settings, stop=stop)
            logger.info(
                "trade_sync_run_complete",
                trades=n_trades,
                users=n_users,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(
        settings,
        make_once,
        watch=watch,
        watch_interval=settings.sync_trade_watch_interval,
    )


async def run_sync_orderbook(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            written = await sync_orderbook(db, gateway, settings=settings, stop=stop)
            logger.info(
                "orderbook_sync_run_complete",
                written=written,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(
        settings,
        make_once,
        watch=watch,
        watch_interval=settings.sync_orderbook_watch_interval,
    )


@dataclass
class SyncProfileOptions:
    wallet: str | None = None
    limit: int | None = None


async def run_refresh_profile(
    settings: Settings,
    options: SyncProfileOptions,
) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            await sync_profiles(
                db,
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

    await _run(settings, make_once, watch=False)


async def run_refresh_home_profile(
    settings: Settings,
    options: SyncProfileOptions,
) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            await sync_home_profiles(
                db,
                gateway,
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

    await _run(settings, make_once, watch=False)


async def run_sync_holder(settings: Settings, *, watch: bool = False) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            processed, skipped = await sync_holder_profiles(
                db, gateway, settings, stop=stop
            )
            logger.info(
                "holder_sync_run_complete",
                processed=processed,
                skipped=skipped,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(
        settings,
        make_once,
        watch=watch,
        watch_interval=settings.sync_holder_watch_interval,
    )


@dataclass
class TagProfileSyncOptions:
    wallet: str | None = None
    limit: int | None = None


async def run_update_tag_profiles(
    settings: Settings,
    options: TagProfileSyncOptions,
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

    await _run_db_only(settings, once)


async def run_add_user(settings: Settings, wallet: str) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(_stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            result = await add_user(db, gateway, settings, wallet)
            logger.info(
                "add_user_run_complete",
                wallet=wallet,
                **result.__dict__,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=False)


async def run_refresh_event(settings: Settings, slug: str | None = None) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(_stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            result = await refresh_events(db, gateway, settings, slug=slug)
            elapsed = round(time.monotonic() - started, 1)
            if isinstance(result, RefreshEventsBulkResult):
                logger.info(
                    "refresh_event_run_complete",
                    total=result.total,
                    ok=result.ok,
                    failed=result.failed,
                    seconds=elapsed,
                )
            else:
                logger.info(
                    "refresh_event_run_complete",
                    slug=slug,
                    **result.__dict__,
                    seconds=elapsed,
                )

        return once

    await _run(settings, make_once, watch=False)


async def run_sync_activity(settings: Settings, wallet: str, event_slug: str) -> None:
    def make_once(db: Database, gateway: PolyGateClient) -> RunOnce:
        async def once(_stop: asyncio.Event | None) -> None:
            started = time.monotonic()
            result = await sync_activity_for_wallet_event(
                db, gateway, settings, wallet=wallet, event_slug=event_slug
            )
            logger.info(
                "sync_activity_run_complete",
                wallet=wallet,
                event_slug=result.event_slug,
                event_id=result.event_id,
                deleted=result.deleted,
                fetched=result.fetched,
                seconds=round(time.monotonic() - started, 1),
            )

        return once

    await _run(settings, make_once, watch=False)
