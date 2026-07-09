from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.db import Database
from poly_house.models.event import Event
from poly_house.models.tag import parse_id

def deadline_soon_market_query(
    *,
    tag_ids: list[int] | None = None,
    neg_risk: bool | None = None,
    q: str | None = None,
    future_only: bool = False,
) -> dict[str, Any]:
    deadline: dict[str, Any] = {"$ne": None}
    if future_only:
        deadline["$gte"] = datetime.now(UTC)
    query: dict[str, Any] = {"closed": False, "deadline": deadline}
    if neg_risk is not None:
        query["neg_risk"] = neg_risk
    if tag_ids:
        query["tag_ids"] = {"$in": tag_ids}
    if q:
        query["question"] = {"$regex": re.escape(q), "$options": "i"}
    return query


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

    condition_id: str
    slug: str
    question: str
    description: str | None = None
    icon: str | None = None
    group_item_title: str | None = None
    group_item_threshold: int | None = None
    outcomes: list[str] = []
    outcome_prices: list[float] = []
    clob_token_ids: list[str] = []
    uma_resolution_statuses: list[str] = []
    closed: bool
    active: bool
    archived: bool
    accepting_orders: bool | None = None
    neg_risk: bool = False
    enable_order_book: bool | None = None
    has_reviewed_dates: bool | None = None
    clear_book_on_start: bool | None = None
    start_date: str | None = None
    end_date: str | None = None
    closed_time: str | None = None
    updated_at: str
    created_at: str
    combo_status: str | None = None
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
    deadline: datetime | None = None
    duration_minutes: float | None = None

    @field_validator("neg_risk", mode="before")
    @classmethod
    def _null_neg_risk_to_false(cls, v: Any) -> bool:
        return False if v is None else v

    @field_validator(
        "outcomes", "clob_token_ids", "uma_resolution_statuses", mode="before"
    )
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
            if (self.closed or self.uma_resolution_status == "resolved")
            and self.outcome_prices
            else None
        )

    def to_doc(
        self, *, synced_at: datetime, event: Event
    ) -> dict[str, Any]:
        doc = self.model_dump(exclude={"id"})
        doc["_id"] = self.id
        doc["resolved_outcome_index"] = self.resolved_outcome_index
        doc["event_id"] = event.id
        doc["event_slug"] = event.slug
        doc["tag_ids"] = sorted(t.id for t in event.tags)
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
