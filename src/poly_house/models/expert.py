from __future__ import annotations

from pydantic import BaseModel


class Expert(BaseModel):
    proxy_wallet: str
    importance: float
    name: str
    profile_image: str
    pnl: float | None = None
    total_balance: float | None = None
    home_pnl: float | None = None
    home_win_rate: float | None = None
    home_markets_count: int | None = None
