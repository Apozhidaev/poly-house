from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Trade(BaseModel):
    model_config = ConfigDict(extra="allow")

    proxyWallet: str
    conditionId: str
    timestamp: int
    side: str | None = None
    size: float | None = None
    price: float | None = None
    outcome: str | None = None
    outcomeIndex: int | None = None
    transactionHash: str | None = None
    name: str | None = None
    pseudonym: str | None = None
    bio: str | None = None
    profileImage: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
