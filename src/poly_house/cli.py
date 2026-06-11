from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
import typer
import uvicorn

from poly_house.config import Settings
from poly_house.sync.runner import SyncOptions, run_sync

app = typer.Typer(help="Polymarket warehouse: sync в MongoDB + REST API.", no_args_is_help=True)


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(),
        ]
    )


@app.command()
def sync(
    watch: Annotated[bool, typer.Option("--watch", help="Бесконечный цикл прогонов с интервалом WATCH_INTERVAL (Ctrl+C для остановки).")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Размер страницы /markets/keyset.")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", help="Ограничить число страниц маркетов (для теста).")] = None,
    skip_trades: Annotated[bool, typer.Option("--skip-trades", help="Не выгружать трейды.")] = False,
) -> None:
    """Прогон синхронизации Polymarket → MongoDB."""
    _configure_logging()
    settings = Settings()
    options = SyncOptions(page_limit=limit, max_pages=max_pages, skip_trades=skip_trades)
    asyncio.run(run_sync(settings, options, watch=watch))


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
