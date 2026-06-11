from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.models.tag import Tag


class Event(BaseModel):
    """Событие; camelCase-ключи payload'а маппятся на snake_case-поля через алиасы.

    В базу сохраняются только описанные здесь поля.
    """

    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    id: str
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    closed: bool | None = None
    active: bool | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    closed_time: datetime | None = None
    updated_at: datetime | None = None
    volume: float | None = None
    liquidity: float | None = None
    image: str | None = None
    icon: str | None = None
    tags: list[Tag] = []
    series: list[dict] = []

    @field_validator(
        "start_date", "end_date", "closed_time", "updated_at", mode="before"
    )
    @classmethod
    def _parse_dt(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("tags", "series", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return v if v is not None else []

    def to_doc(self, *, fallback_tags: list[Tag], synced_at: datetime) -> dict[str, Any]:
        """Документ события без _id (для $set) и без вложенных tags/series."""
        doc = self.model_dump(exclude={"tags", "series"})
        tags = self.tags or fallback_tags
        doc["tag_ids"] = [t.id for t in tags]
        doc["series_ids"] = [s["id"] for s in self.series if isinstance(s, dict) and s.get("id")]
        doc["synced_at"] = synced_at
        return doc
