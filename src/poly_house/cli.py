from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
import typer
import uvicorn

from poly_house.config import Settings
from poly_house.sync.runner import (
    HolderPositionSyncOptions,
    MarketSyncOptions,
    ProfileSyncOptions,
    run_sync_holder_positions,
    run_sync_holder_profiles,
    run_sync_markets,
    run_sync_snapshots,
    run_sync_trades,
    run_sync_user_profiles,
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


@app.command("sync-market")
def sync_market(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Размер страницы /markets/keyset.")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", help="Ограничить число страниц маркетов (для теста).")] = None,
    skip_open: Annotated[bool, typer.Option("--skip-open", help="Синхронизировать только закрытые маркеты (closed=true).")] = False,
) -> None:
    """Синхронизация маркетов (+ события, теги) Polymarket → MongoDB."""
    _configure_logging()
    settings = Settings()
    options = MarketSyncOptions(page_limit=limit, max_pages=max_pages, skip_open=skip_open)
    asyncio.run(run_sync_markets(settings, options, watch=watch))


@app.command("sync-trade")
def sync_trade(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
) -> None:
    """Синхронизация трейдов (+ пользователи) для маркетов, обновлённых после прошлого прогона."""
    _configure_logging()
    settings = Settings()
    asyncio.run(run_sync_trades(settings, watch=watch))


@app.command("snapshot")
def snapshot(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
) -> None:
    """Снимки ордербука и top holders для открытых маркетов."""
    _configure_logging()
    settings = Settings()
    asyncio.run(run_sync_snapshots(settings, watch=watch))


@app.command("sync-user-profile")
def sync_user_profile(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число пользователей (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Синхронизировать одного пользователя по proxy_wallet.")] = None,
) -> None:
    """Синхронизация профилей пользователей из users → profiles."""
    _configure_logging()
    settings = Settings()
    options = ProfileSyncOptions(wallet=wallet, limit=limit)
    asyncio.run(run_sync_user_profiles(settings, options, watch=watch))


@app.command("sync-holder-profile")
def sync_holder_profile(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число holders (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Синхронизировать одного holder по proxy_wallet.")] = None,
) -> None:
    """Синхронизация профилей holders из holders → profiles."""
    _configure_logging()
    settings = Settings()
    options = ProfileSyncOptions(wallet=wallet, limit=limit)
    asyncio.run(run_sync_holder_profiles(settings, options, watch=watch))


@app.command("sync-holder-position")
def sync_holder_position(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Ограничить число holders (для теста).")] = None,
    wallet: Annotated[str | None, typer.Option("--wallet", help="Синхронизировать одного holder по proxy_wallet.")] = None,
) -> None:
    """Синхронизация closed positions holders из holders → positions."""
    _configure_logging()
    settings = Settings()
    options = HolderPositionSyncOptions(wallet=wallet, limit=limit)
    asyncio.run(run_sync_holder_positions(settings, options, watch=watch))


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
