from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Tag(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: str | None = None
    slug: str | None = None

    def to_doc(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_id"] = self.id
        return doc
