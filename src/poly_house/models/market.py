from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.models.event import Event
from poly_house.models.tag import Tag


class Market(BaseModel):
    """Маркет; camelCase-ключи payload'а маппятся на snake_case-поля через алиасы.

    В базу сохраняются только описанные здесь поля.
    """

    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    id: str
    question: str | None = None
    condition_id: str | None = None
    slug: str | None = None
    description: str | None = None
    outcomes: list[str] = []
    outcome_prices: list[float] = []
    closed: bool | None = None
    active: bool | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    closed_time: datetime | None = None
    updated_at: datetime | None = None
    volume_num: float | None = None
    liquidity_num: float | None = None
    tags: list[Tag] = []
    events: list[Event] = []

    @field_validator(
        "start_date", "end_date", "closed_time", "updated_at", mode="before"
    )
    @classmethod
    def _parse_dt(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("outcomes", mode="before")
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

    @field_validator("tags", "events", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return v if v is not None else []

    @property
    def event_id(self) -> str | None:
        return self.events[0].id if self.events else None

    def to_doc(self, *, synced_at: datetime) -> dict[str, Any]:
        doc = self.model_dump(exclude={"tags", "events"})
        doc["_id"] = self.id
        # пустой condition_id убираем, чтобы не ломать sparse-unique индекс
        if not doc.get("condition_id"):
            doc.pop("condition_id", None)
        doc["event_id"] = self.event_id
        doc["tag_ids"] = [t.id for t in self.tags]
        doc["synced_at"] = synced_at
        return doc
