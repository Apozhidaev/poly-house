from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ActivityCache(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    event_slug: str
    timestamp: int
    activity_synced: bool = False
    sync_fail_count: int | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
