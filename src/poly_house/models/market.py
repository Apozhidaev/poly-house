from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.db import Database
from poly_house.models.event import Event
from poly_house.models.tag import parse_id


class Market(BaseModel):
    """Маркет; camelCase-ключи payload'а маппятся на snake_case-поля через алиасы.

    В базу сохраняются только описанные здесь поля.
    """

    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    id: int

    @field_validator("id", mode="before")
    @classmethod
    def _parse_id(cls, v: Any) -> int:
        return parse_id(v)

    question: str | None = None
    condition_id: str
    slug: str
    description: str | None = None
    icon: str | None = None
    group_item_title: str | None = None
    group_item_threshold: int | None = None
    outcomes: list[str] = []
    outcome_prices: list[float] = []
    clob_token_ids: list[str] = []
    closed: bool
    active: bool | None = None
    accepting_orders: bool | None = None
    neg_risk: bool = False
    enable_order_book: bool | None = None
    has_reviewed_dates: bool | None = None
    clear_book_on_start: bool | None = None
    start_date: str | None = None
    end_date: str | None = None
    closed_time: str | None = None
    updated_at: str | None = None
    created_at: str | None = None
    uma_resolution_status: str | None = None
    uma_end_date: str | None = None
    uma_bond: str | None = None
    uma_reward: str | None = None
    volume_num: float | None = None
    liquidity_num: float | None = None
    spread: float | None = None
    last_trade_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    order_price_min_tick_size: float | None = None
    order_min_size: float | None = None

    @field_validator("neg_risk", mode="before")
    @classmethod
    def _null_neg_risk_to_false(cls, v: Any) -> bool:
        return False if v is None else v

    @field_validator("outcomes", "clob_token_ids", mode="before")
    @classmethod
    def _parse_outcomes(cls, v: Any) -> Any:
        # Gamma отдаёт списки строками: '["Yes", "No"]'
        if isinstance(v, str):
            return json.loads(v) if v else []
        return v if v is not None else []

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def _parse_outcome_prices(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = json.loads(v) if v else []
        if v is None:
            return []
        return [float(x) for x in v]

    @property
    def resolved_outcome_index(self) -> int | None:
        return (
            self.outcome_prices.index(max(self.outcome_prices))
            if self.closed and self.outcome_prices
            else None
        )

    def to_doc(self, *, synced_at: datetime, event: Event) -> dict[str, Any]:
        doc = self.model_dump(exclude={"id"})
        doc["_id"] = self.id
        doc["event_id"] = event.id
        doc["event_slug"] = event.slug
        doc["resolved_outcome_index"] = self.resolved_outcome_index
        doc["tag_ids"] = [t.id for t in event.tags]
        doc["synced_at"] = synced_at
        return doc


async def load_resolved_outcomes(
    db: Database, condition_ids: list[str]
) -> dict[str, int]:
    if not condition_ids:
        return {}
    cursor = db.markets.find(
        {
            "condition_id": {"$in": condition_ids},
            "resolved_outcome_index": {"$ne": None},
        },
        projection={"condition_id": 1, "resolved_outcome_index": 1},
    )
    docs = await cursor.to_list(length=None)
    return {
        doc["condition_id"]: doc["resolved_outcome_index"]
        for doc in docs
        if doc.get("resolved_outcome_index") is not None
    }
