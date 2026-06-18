from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PositionMetrics(BaseModel):
    proxy_wallet: str
    win_rate: float | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
