from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class User(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    name: str | None = None
    pseudonym: str | None = None
    bio: str | None = None
    profile_image: str | None = None
    last_trade_time: datetime

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> User:
        return cls.model_validate(
            {
                **raw,
                "last_trade_time": datetime.fromtimestamp(raw["timestamp"], tz=UTC),
            }
        )

    def to_update(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
