from pydantic import BaseModel


class LeaderboardStats(BaseModel):
    """PNL and volume from leaderboard API (GET /v1/leaderboard)."""

    pnl: float
    vol: float
