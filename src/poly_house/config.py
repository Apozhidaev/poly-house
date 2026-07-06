from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "poly_house"

    gateway_url: str = "http://localhost:8080"

    # watch-режим (--watch): интервал задаётся per-command SYNC_*_WATCH_INTERVAL
    sync_event_watch_interval: float = 1800.0  # sync-event --watch
    sync_kline_watch_interval: float = 60.0  # sync-kline --watch
    sync_trade_watch_interval: float = 86400.0  # sync-trade --watch
    sync_holder_watch_interval: float = 86400.0  # sync-holder --watch

    # первый прогон: глубина загрузки events по updatedAt
    updated_in_days: int = 30

    # первый прогон sync-home-profile: activity cache за последние N дней
    activity_cache_in_days: int = 365

    # sync-profile: считать profile свежим, если synced_at моложе N дней
    profile_sync_interval_days: int = 1

    # sync-home-profile: считать home profile свежим, если synced_at моложе N часов
    home_profile_sync_interval_hours: int = 6

    # необязательный фильтр events по тегам (tag_id=1&tag_id=2)
    events_tag_ids: list[int] = []

    # исключить events с этими тегами (exclude_tag_id в API)
    events_exclude_tag_ids: list[int] = []

    # пропускать event, если среди nested markets есть outcomes != ["Yes", "No"]
    markets_yes_no_only: bool = False

    # оставлять только events с neg_risk == значению (null/absent в API = false)
    events_neg_risk: bool | None = None

    # размеры страниц API
    events_page_limit: int = 100  # /gamma/events max 100
    trades_page_limit: int = 1000  # максимум Data API
    trades_max_offset: int = 3000  # потолок offset в Data API
    holders_limit: int = 500  # max Data API /holders
    activity_page_limit: int = 500
    activity_max_offset: int = 3000  # потолок offset в Data API
    activity_cache_page_limit: int = 50
    activity_cache_max_offset: int = 100000

    # REST API
    server_host: str = "127.0.0.1"
    server_port: int = 8000
