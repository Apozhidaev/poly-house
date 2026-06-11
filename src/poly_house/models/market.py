from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from poly_house.models.event import Event
from poly_house.models.tag import Tag


class Market(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    question: str | None = None
    conditionId: str | None = None
    slug: str | None = None
    closed: bool | None = None
    updatedAt: datetime | None = None
    tags: list[Tag] = []
    events: list[Event] = []

    @field_validator("updatedAt", mode="before")
    @classmethod
    def _parse_dt(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

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
        # пустой conditionId убираем, чтобы не ломать sparse-unique индекс
        if not doc.get("conditionId"):
            doc.pop("conditionId", None)
        doc["eventId"] = self.event_id
        doc["tagIds"] = [t.id for t in self.tags]
        doc["syncedAt"] = synced_at
        return doc
