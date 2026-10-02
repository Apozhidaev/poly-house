from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from poly_house.config import Settings
from poly_house.db import create_client
from poly_house.api.routes import (
    activity,
    events,
    home_profiles,
    klines,
    markets,
    profiles,
    profile_summary,
    tag_profiles,
    tags,
    trades,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    client = create_client(settings)
    app.state.db = client[settings.mongo_db]
    yield
    await client.close()


app = FastAPI(
    title="poly-house", description="Polymarket warehouse API", lifespan=lifespan
)
app.include_router(events.router)
app.include_router(markets.router)
app.include_router(tags.router)
app.include_router(trades.router)
app.include_router(profiles.router)
app.include_router(users.router)
app.include_router(home_profiles.router)
app.include_router(tag_profiles.router)
app.include_router(profile_summary.router)
app.include_router(activity.router)
app.include_router(klines.router)
