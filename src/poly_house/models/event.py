from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from poly_house.models.tag import Tag


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    slug: str | None = None
    title: str | None = None
    closed: bool | None = None
    updatedAt: datetime | None = None
    tags: list[Tag] = []
    series: list[dict] = []

    @field_validator("updatedAt", mode="before")
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
        """Документ события без _id (для $set) и без вложенных markets/tags/series."""
        doc = self.model_dump(exclude={"tags", "series"})
        doc.pop("markets", None)
        tags = self.tags or fallback_tags
        doc["tagIds"] = [t.id for t in tags]
        doc["seriesIds"] = [s["id"] for s in self.series if isinstance(s, dict) and s.get("id")]
        doc["syncedAt"] = synced_at
        return doc
