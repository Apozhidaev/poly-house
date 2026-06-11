from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from poly_house.models.trade import Trade


class User(BaseModel):
    proxyWallet: str
    name: str | None = None
    pseudonym: str | None = None
    bio: str | None = None
    profileImage: str | None = None

    @classmethod
    def from_trade(cls, trade: Trade) -> User:
        return cls(
            proxyWallet=trade.proxyWallet,
            name=trade.name,
            pseudonym=trade.pseudonym,
            bio=trade.bio,
            profileImage=trade.profileImage,
        )

    def to_update(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxyWallet"})
