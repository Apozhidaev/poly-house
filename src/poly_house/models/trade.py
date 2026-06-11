from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Trade(BaseModel):
    """Сделка; camelCase-ключи payload'а маппятся на snake_case-поля через алиасы."""

    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    condition_id: str
    timestamp: int
    side: str | None = None
    size: float | None = None
    price: float | None = None
    outcome: str | None = None
    outcome_index: int | None = None
    transaction_hash: str | None = None
    name: str | None = None
    pseudonym: str | None = None
    bio: str | None = None
    profile_image: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
