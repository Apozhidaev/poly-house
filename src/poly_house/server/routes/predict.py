from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from poly_house.clients.holder_boost import HolderBoostClient
from poly_house.config import Settings
from poly_house.models.tag_profile import TagProfileAggregate
from poly_house.server.deps import Db
from poly_house.utils import compute_days_left_until_deadline
from poly_house.utils.predict import compute_market_vote_prices
from poly_house.utils.tag_metrics import (
    aggregate_wallet_tag_metrics,
    load_tag_profile_tag_metrics,
    load_tag_weights,
)

router = APIRouter(prefix="/predict", tags=["predict"])

MARKET_VOTE = "MARKET_VOTE"
UKRAINE_MAP = "UKRAINE_MAP"
UKRAINE_MAP_TAG_ID = 102486
IRAN = "IRAN"
IRAN_TAG_ID = 78

HOLDER_BOOST_PREDICTIONS = (
    (UKRAINE_MAP, UKRAINE_MAP_TAG_ID),
    (IRAN, IRAN_TAG_ID),
)


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
    condition_id: str
    tag_ids: list[int] = Field(min_length=1)
    holders: list[PredictHolder] = Field(min_length=1)


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
        hit_rates,
        error_rates,
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
            hit_rates,
            error_rates,
        )
    return aggregates


@router.post("/by-holder")
async def predict_by_holder(
    body: PredictByHolderRequest, db: Db
) -> dict[str, Any]:
    wallets = list(dict.fromkeys(holder.proxy_wallet for holder in body.holders))
    aggregates = await _load_tag_aggregates(db, wallets, body.tag_ids)
    predict_prices = compute_market_vote_prices(body.holders, aggregates)
    predictions: list[dict[str, Any]] = [
        Prediction(type=MARKET_VOTE, predict_prices=predict_prices).model_dump()
    ]

    holder_boost_predictions = [
        (prediction_type, tag_id)
        for prediction_type, tag_id in HOLDER_BOOST_PREDICTIONS
        if tag_id in body.tag_ids
    ]
    if holder_boost_predictions:
        market = await db.markets.find_one({"condition_id": body.condition_id})
        days_left = compute_days_left_until_deadline(
            market.get("deadline") if market else None
        )
        if days_left is not None:
            settings = Settings()
            holder_payload = [
                {
                    "proxy_wallet": holder.proxy_wallet,
                    "outcome_index": holder.outcome_index,
                }
                for holder in body.holders
            ]
            async with HolderBoostClient(settings) as client:
                for prediction_type, tag_id in holder_boost_predictions:
                    probs = await client.predict(
                        tag_id=tag_id,
                        days_left=days_left,
                        holders=holder_payload,
                    )
                    if probs is not None:
                        predictions.append(
                            Prediction(
                                type=prediction_type,
                                predict_prices=cast(list[float | None], probs),
                            ).model_dump()
                        )

    return {"predictions": predictions}
