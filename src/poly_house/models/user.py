from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from poly_house.models.trade import Trade


class User(BaseModel):
    proxy_wallet: str
    name: str | None = None
    pseudonym: str | None = None
    bio: str | None = None
    profile_image: str | None = None

    @classmethod
    def from_trade(cls, trade: Trade) -> User:
        return cls(
            proxy_wallet=trade.proxy_wallet,
            name=trade.name,
            pseudonym=trade.pseudonym,
            bio=trade.bio,
            profile_image=trade.profile_image,
        )

    def to_update(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
