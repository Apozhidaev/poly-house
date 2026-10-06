from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
import typer
import uvicorn

from poly_house.config import load_settings
from poly_house.commands.runner import (
    EventSyncOptions,
    SyncProfileOptions,
    TagProfileSyncOptions,
    run_sync_events,
    run_refresh_profile,
    run_refresh_home_profile,
    run_sync_holder,
    run_sync_trades,
    run_sync_orderbook,
    run_update_tag_profiles,
    run_update_event_view,
    run_add_user,
    run_refresh_event,
    run_sync_activity,
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
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом sync_event_watch_interval (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Размер страницы /gamma/events.")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", help="Ограничить число страниц events (для теста).")] = None,
) -> None:
    """Синхронизация events + nested markets + tags из Gateway /gamma/events → MongoDB."""
    _configure_logging()
    settings = load_settings()
    options = EventSyncOptions(open_only=open_only, page_limit=limit, max_pages=max_pages)
    asyncio.run(run_sync_events(settings, options, watch=watch))


@app.command("sync-trade")
def sync_trade(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом sync_trade_watch_interval (Ctrl+C для остановки).")] = False,
) -> None:
    """Синхронизация трейдов (+ пользователи) для маркетов, обновлённых после прошлого прогона."""
    _configure_logging()
    settings = load_settings()
    asyncio.run(run_sync_trades(settings, watch=watch))


@app.command("sync-orderbook")
def sync_orderbook(
    watch: Annotated[
        bool,
        typer.Option(
            "--watch",
            help="Бесконечный цикл прогонов с интервалом sync_orderbook_watch_interval (Ctrl+C для остановки).",
        ),
    ] = False,
) -> None:
    """CLOB orderbook open markets → hourly bid/ask klines + best_prices."""
    _configure_logging()
    settings = load_settings()
    asyncio.run(run_sync_orderbook(settings, watch=watch))


@app.command("sync-holder")
def sync_holder(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом sync_holder_watch_interval (Ctrl+C для остановки).")] = False,
) -> None:
    """Holders открытых маркетов → sync profiles, home_profiles, tag_profiles."""
    _configure_logging()
    settings = load_settings()
    asyncio.run(run_sync_holder(settings, watch=watch))


@app.command("refresh-profile")
def refresh_profile(
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число profiles (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Обновить один profile по proxy_wallet.")] = None,
) -> None:
    """Устаревшие profiles (Polymarket API, synced_at > profile_refresh_interval_days)."""
    _configure_logging()
    settings = load_settings()
    options = SyncProfileOptions(wallet=wallet, limit=limit)
    asyncio.run(run_refresh_profile(settings, options))


@app.command("refresh-home-profile")
def refresh_home_profile(
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число home_profiles (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Обновить один home profile по proxy_wallet.")] = None,
) -> None:
    """Устаревшие home_profiles (activity cache → activities, synced_at > home_profile_refresh_interval_days)."""
    _configure_logging()
    settings = load_settings()
    options = SyncProfileOptions(wallet=wallet, limit=limit)
    asyncio.run(run_refresh_home_profile(settings, options))


@app.command("update-tag-profile")
def update_tag_profile(
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число holders (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Обновить tag profiles одного holder по proxy_wallet.")] = None,
) -> None:
    """Агрегация tag profiles holders из activities + markets → tag_profiles."""
    _configure_logging()
    settings = load_settings()
    options = TagProfileSyncOptions(wallet=wallet, limit=limit)
    asyncio.run(run_update_tag_profiles(settings, options))


@app.command("update-event-view")
def update_event_view_cmd(
    watch: Annotated[
        bool,
        typer.Option(
            "--watch",
            help="Бесконечный цикл прогонов с интервалом update_event_view_watch_interval (Ctrl+C для остановки).",
        ),
    ] = False,
) -> None:
    """Открытые events → events_view с open markets, trade_stats и best_bids/best_asks."""
    _configure_logging()
    settings = load_settings()
    asyncio.run(run_update_event_view(settings, watch=watch))


@app.command("add-user")
def add_user_cmd(
    wallet: Annotated[str, typer.Argument(help="proxy_wallet (0x...)")],
) -> None:
    """Gateway /gamma/public-profile → users; принудительный sync profiles/home_profiles/tag_profiles."""
    _configure_logging()
    settings = load_settings()
    try:
        asyncio.run(run_add_user(settings, wallet))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("refresh-event")
def refresh_event_cmd(
    slug: Annotated[str | None, typer.Option("--slug", help="Event slug.")] = None,
) -> None:
    """Refresh one event from Gateway (--slug) or all events in DB."""
    _configure_logging()
    settings = load_settings()
    try:
        asyncio.run(run_refresh_event(settings, slug=slug))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("sync-activity")
def sync_activity_cmd(
    wallet: Annotated[str, typer.Option("--wallet", help="proxy_wallet (0x...)")],
    event: Annotated[str, typer.Option("--event", help="Event slug.")],
) -> None:
    """Удалить activities wallet+event и заново загрузить всю activity по event из Gateway."""
    _configure_logging()
    settings = load_settings()
    try:
        asyncio.run(run_sync_activity(settings, wallet, event))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Хост (по умолчанию из конфига).")] = None,
    port: Annotated[int | None, typer.Option(help="Порт (по умолчанию из конфига).")] = None,
) -> None:
    """Запуск REST API поверх MongoDB."""
    settings = load_settings()
    uvicorn.run(
        "poly_house.api.app:app",
        host=host or settings.server_host,
        port=port or settings.server_port,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
