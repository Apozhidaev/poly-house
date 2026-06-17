from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "poly_house"

    gamma_base_url: str = "https://gamma-api.polymarket.com"
    data_base_url: str = "https://data-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"

    # watch-режим
    watch_interval: float = 60.0  # секунды между прогонами

    # первый прогон: глубина загрузки маркетов по updatedAt
    updated_in_days: int = 30

    # необязательный фильтр маркетов по тегам (tag_id=1&tag_id=2)
    markets_tag_ids: list[int] = []

    # исключить маркеты с этими тегами (фильтруем на нашей стороне, в API параметра нет)
    markets_exclude_tag_ids: list[int] = []

    # пропускать маркеты, у которых outcomes != ["Yes", "No"]
    markets_yes_no_only: bool = False

    # оставлять только маркеты с neg_risk == значению (null/absent в API = false)
    markets_neg_risk: bool | None = None

    # размеры страниц API
    markets_page_limit: int = 100
    trades_page_limit: int = 1000  # максимум Data API
    trades_max_offset: int = 3000  # потолок offset в Data API
    snapshot_holders_limit: int = 500  # max Data API /holders

    # REST API
    server_host: str = "127.0.0.1"
    server_port: int = 8000
