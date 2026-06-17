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
    side: str
    size: float
    price: float
    outcome: str
    outcome_index: int
    transaction_hash: str

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
