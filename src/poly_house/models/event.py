from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.models.tag import Tag, parse_id


class Event(BaseModel):
    """Событие; camelCase-ключи payload'а маппятся на snake_case-поля через алиасы.

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

    slug: str
    title: str
    description: str | None = None
    icon: str | None = None
    closed: bool
    active: bool
    archived: bool
    neg_risk: bool = False
    start_date: str | None = None
    end_date: str | None = None
    closed_time: str | None = None
    updated_at: str
    created_at: str
    volume: float | None = None
    liquidity: float | None = None
    open_interest: float | None = None
    relevant: bool
    calendar: bool | None = None
    deadline: datetime | None = None
    tags: list[Tag] = []
    series: list[dict[str, Any]] = []

    @field_validator("neg_risk", mode="before")
    @classmethod
    def _null_neg_risk_to_false(cls, v: Any) -> bool:
        return False if v is None else v

    @field_validator("tags", "series", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return v if v is not None else []

    def to_doc(
        self,
        *,
        synced_at: datetime,
        market_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Документ события без вложенных tags/series."""
        doc = self.model_dump(exclude={"tags", "series", "id"})
        doc["_id"] = self.id
        doc["tag_ids"] = sorted(t.id for t in self.tags)
        doc["market_ids"] = market_ids or []
        doc["series_ids"] = [
            parse_id(s["id"])
            for s in self.series
            if isinstance(s, dict) and s.get("id") is not None
        ]
        doc["synced_at"] = synced_at
        return doc
