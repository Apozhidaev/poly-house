from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from poly_house.models.tag import parse_id


class Series(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    id: int

    @field_validator("id", mode="before")
    @classmethod
    def _parse_id(cls, v: Any) -> int:
        return parse_id(v)

    ticker: str
    slug: str
    title: str
    active: bool
    closed: bool
    archived: bool
    created_at: str
    updated_at: str
    series_type: str | None = None
    recurrence: str | None = None
    image: str | None = None
    icon: str | None = None
    volume: float | None = None
    volume_24hr: float | None = None
    liquidity: float | None = None
    comment_count: int | None = None

    def to_doc(self, *, synced_at: datetime) -> dict[str, Any]:
        doc = self.model_dump(exclude={"id"})
        doc["_id"] = self.id
        doc["synced_at"] = synced_at
        return doc
