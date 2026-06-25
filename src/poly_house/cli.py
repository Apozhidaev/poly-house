from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
import typer
import uvicorn

from poly_house.config import Settings
from poly_house.sync.runner import (
    EventSyncOptions,
    SyncProfileOptions,
    TagProfileSyncOptions,
    run_sync_events,
    run_sync_profile,
    run_sync_home_profile,
    run_sync_holder_snapshots,
    run_sync_trades,
    run_update_tag_profiles,
)

app = typer.Typer(help="Polymarket warehouse: sync в MongoDB + REST API.", no_args_is_help=True)


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(),
        ]
    )


@app.command("sync-event")
def sync_event(
    open_only: Annotated[bool, typer.Option("--open", help="Только открытые events (closed=false).")] = False,
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Размер страницы /events/keyset.")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", help="Ограничить число страниц events (для теста).")] = None,
) -> None:
    """Синхронизация events + nested markets + tags из Gamma /events/keyset → MongoDB."""
    _configure_logging()
    settings = Settings()
    options = EventSyncOptions(open_only=open_only, page_limit=limit, max_pages=max_pages)
    asyncio.run(run_sync_events(settings, options, watch=watch))


@app.command("sync-trade")
def sync_trade(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
) -> None:
    """Синхронизация трейдов (+ пользователи) для маркетов, обновлённых после прошлого прогона."""
    _configure_logging()
    settings = Settings()
    asyncio.run(run_sync_trades(settings, watch=watch))


@app.command("sync-holder")
def sync_holder(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
) -> None:
    """Holder snapshots: ордербук и top holders для открытых маркетов."""
    _configure_logging()
    settings = Settings()
    asyncio.run(run_sync_holder_snapshots(settings, watch=watch))


@app.command("sync-profile")
def sync_profile(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число profiles (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Синхронизировать один profile по proxy_wallet.")] = None,
) -> None:
    """Синхронизация устаревших profiles (Polymarket API, synced_at > 24h)."""
    _configure_logging()
    settings = Settings()
    options = SyncProfileOptions(wallet=wallet, limit=limit)
    asyncio.run(run_sync_profile(settings, options, watch=watch))


@app.command("sync-home-profile")
def sync_home_profile(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число home_profiles (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Синхронизировать один home profile по proxy_wallet.")] = None,
) -> None:
    """Синхронизация устаревших home_profiles (activity cache → activities, synced_at > 6h)."""
    _configure_logging()
    settings = Settings()
    options = SyncProfileOptions(wallet=wallet, limit=limit)
    asyncio.run(run_sync_home_profile(settings, options, watch=watch))


@app.command("update-tag-profile")
def update_tag_profile(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число holders (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Обновить tag profiles одного holder по proxy_wallet.")] = None,
) -> None:
    """Агрегация tag profiles holders из activities + markets → tag_profiles."""
    _configure_logging()
    settings = Settings()
    options = TagProfileSyncOptions(wallet=wallet, limit=limit)
    asyncio.run(run_update_tag_profiles(settings, options, watch=watch))


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Хост (по умолчанию из конфига).")] = None,
    port: Annotated[int | None, typer.Option(help="Порт (по умолчанию из конфига).")] = None,
) -> None:
    """Запуск REST API поверх MongoDB."""
    settings = Settings()
    uvicorn.run(
        "poly_house.server.app:app",
        host=host or settings.server_host,
        port=port or settings.server_port,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
