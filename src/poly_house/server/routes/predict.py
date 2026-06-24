from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from poly_house.models.tag_profile import TagProfileAggregate
from poly_house.server.deps import Db
from poly_house.utils.predict import compute_market_vote_prices
from poly_house.utils.tag_metrics import (
    aggregate_wallet_tag_metrics,
    load_tag_profile_tag_metrics,
    load_tag_weights,
)

router = APIRouter(prefix="/predict", tags=["predict"])

MARKET_VOTE = "MARKET_VOTE"


class PredictHolder(BaseModel):
    proxy_wallet: str
    outcome_index: int
    amount: float

    @field_validator("outcome_index")
    @classmethod
    def _validate_outcome_index(cls, value: int) -> int:
        if value not in (0, 1):
            raise ValueError("outcome_index must be 0 or 1")
        return value


class PredictByHolderRequest(BaseModel):
    holders: list[PredictHolder] = Field(min_length=1)
    tag_ids: list[int] = Field(min_length=1)


class Prediction(BaseModel):
    type: str
    predict_prices: list[float | None]


async def _load_tag_aggregates(
    db: Db, wallets: list[str], tag_ids: list[int]
) -> dict[str, TagProfileAggregate]:
    if not wallets:
        return {}
    tag_weights = await load_tag_weights(db, tag_ids)
    (
        win_rates,
        pnl_growths,
        pnl_stabilities,
        pnls,
        volumes,
        markets_counts,
        win_holder_counts,
        lose_holder_counts,
    ) = await load_tag_profile_tag_metrics(db, wallets, tag_ids)
    aggregates = {}
    for wallet in wallets:
        aggregates[wallet] = aggregate_wallet_tag_metrics(
            wallet,
            tag_ids,
            tag_weights,
            win_rates,
            pnl_growths,
            pnl_stabilities,
            pnls,
            volumes,
            markets_counts,
            win_holder_counts,
            lose_holder_counts,
        )
    return aggregates


@router.post("/by-holder")
async def predict_by_holder(
    body: PredictByHolderRequest, db: Db
) -> dict[str, Any]:
    wallets = list(dict.fromkeys(holder.proxy_wallet for holder in body.holders))
    aggregates = await _load_tag_aggregates(db, wallets, body.tag_ids)
    predict_prices = compute_market_vote_prices(body.holders, aggregates)
    return {
        "predictions": [
            Prediction(type=MARKET_VOTE, predict_prices=predict_prices).model_dump()
        ]
    }
