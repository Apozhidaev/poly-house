from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_id(v: Any) -> int:
    return v if isinstance(v, int) else int(v)


class Tag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int

    @field_validator("id", mode="before")
    @classmethod
    def _parse_id(cls, v: Any) -> int:
        return parse_id(v)
    label: str
    slug: str
    markets_count: int = Field(default=0)

    def to_doc(self) -> dict[str, Any]:
        doc = self.model_dump(exclude={"id"})
        doc["_id"] = self.id
        return doc
