from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "poly_house"

    gateway_url: str = "http://localhost:8080"

    # watch (--watch): период между стартами прогонов; пауза = interval − elapsed
    sync_event_watch_interval: float = 1800.0  # sync-event --watch
    sync_orderbook_watch_interval: float = 60.0  # sync-orderbook --watch
    sync_trade_watch_interval: float = 86400.0  # sync-trade --watch
    sync_holder_watch_interval: float = 86400.0  # sync-holder --watch

    # sync-orderbook: хранить best_prices не дольше N секунд
    best_prices_window: int = 86400

    # первый прогон: глубина загрузки events по updatedAt
    updated_in_days: int = 30

    # первый прогон refresh-home-profile: activity cache за последние N дней
    activity_cache_in_days: int = 365

    # refresh-profile: считать profile свежим, если synced_at моложе N дней
    profile_refresh_interval_days: int = 1

    # refresh-home-profile: считать home profile свежим, если synced_at моложе N дней
    home_profile_refresh_interval_days: int = 1

    # необязательный фильтр events по тегам (tag_id=1&tag_id=2)
    events_tag_ids: list[int] = []

    # исключить events с этими тегами (exclude_tag_id в API)
    events_exclude_tag_ids: list[int] = []

    # необязательный фильтр events по slug (slug=a&slug=b)
    events_slugs: list[str] = []

    # пропускать event, если среди nested markets есть outcomes != ["Yes", "No"]
    markets_yes_no_only: bool = False

    # оставлять только events с neg_risk == значению (null/absent в API = false)
    events_neg_risk: bool | None = None

    # размеры страниц API
    events_page_limit: int = 100  # /gamma/events max 100
    trades_page_limit: int = 1000  # максимум Data API
    trades_max_offset: int = 3000  # потолок fetched rows при cursor-пагинации
    holders_limit: int = 500  # max Data API /holders
    activity_page_limit: int = 500
    activity_max_offset: int = 3000  # потолок fetched rows при cursor-пагинации
    activity_cache_page_limit: int = 50
    activity_cache_max_offset: int = 100000  # потолок fetched rows при cursor-пагинации

    # REST API
    server_host: str = "127.0.0.1"
    server_port: int = 8000


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or Path("config.yaml")
    if not config_path.is_file():
        return Settings()
    raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data = raw if isinstance(raw, dict) else {}
    return Settings.model_validate(data)
