from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from poly_house.models.market import deadline_soon_market_query
from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/markets", tags=["markets"])

OptionalTagIds = Annotated[list[int] | None, Query()]


class CheckHomeMarketsRequest(BaseModel):
    condition_ids: list[str]
    tag_ids: list[int] | None = None


@router.get("")
async def list_markets(
    db: Db,
    page: Page,
    closed: bool | None = None,
    neg_risk: bool | None = None,
    event_id: int | None = None,
    tag_ids: OptionalTagIds = None,
    exclude_tag_ids: OptionalTagIds = None,
    slug: str | None = None,
    condition_id: str | None = None,
    resolved: bool | None = None,
    temporal: bool | None = None,
    duration_from: int | None = None,
    duration_to: int | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if closed is not None:
        query["closed"] = closed
    if neg_risk is not None:
        query["neg_risk"] = neg_risk
    if event_id:
        query["event_id"] = event_id
    if tag_ids:
        query["tag_ids"] = {"$in": tag_ids}
    if exclude_tag_ids:
        query["tag_ids"] = {
            **query.get("tag_ids", {}),
            "$nin": exclude_tag_ids,
        }
    if slug:
        query["slug"] = slug
    if condition_id:
        query["condition_id"] = condition_id
    if resolved is True:
        query["resolved_outcome_index"] = {"$ne": None}
    elif resolved is False:
        query["resolved_outcome_index"] = None
    if temporal is True:
        query["duration_minutes"] = {"$ne": None}
    elif temporal is False:
        query["duration_minutes"] = None
    duration_minutes: dict[str, int] = {}
    if duration_from is not None:
        duration_minutes["$gte"] = duration_from
    if duration_to is not None:
        duration_minutes["$lte"] = duration_to
    if duration_minutes:
        query["duration_minutes"] = duration_minutes
    cursor = db.markets.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.post("/check-home")
async def check_home_markets(body: CheckHomeMarketsRequest, db: Db) -> dict[str, Any]:
    if not body.condition_ids:
        return {"condition_ids": []}

    query: dict[str, Any] = {"condition_id": {"$in": body.condition_ids}}
    if body.tag_ids:
        query["tag_ids"] = {"$in": body.tag_ids}

    docs = await db.markets.find(query, projection={"condition_id": 1}).to_list(
        length=None
    )
    found = {doc["condition_id"] for doc in docs}
    return {
        "condition_ids": [cid for cid in body.condition_ids if cid in found],
    }


@router.get("/deadline-soon")
async def list_deadline_soon_markets(
    db: Db,
    page: Page,
    neg_risk: bool | None = None,
    tag_ids: OptionalTagIds = None,
    q: str | None = None,
    future_only: bool = False,
) -> dict[str, Any]:
    query = deadline_soon_market_query(
        tag_ids=tag_ids, neg_risk=neg_risk, q=q, future_only=future_only
    )
    total = await db.markets.count_documents(query)
    cursor = (
        db.markets.find(query)
        .sort("deadline", 1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit), "total": total}


@router.get("/tag-distribution")
async def list_market_tag_distribution(
    db: Db,
    page: Page,
    tag_id: int,
) -> dict[str, Any]:
    pipeline: list[dict[str, Any]] = [
        {"$match": {"tag_ids": tag_id}},
        {"$unwind": "$tag_ids"},
        {"$group": {"_id": "$tag_ids", "markets_count": {"$sum": 1}}},
        {"$sort": {"markets_count": -1, "_id": 1}},
        {
            "$facet": {
                "metadata": [{"$count": "total"}],
                "items": [
                    {"$skip": page.offset},
                    {"$limit": page.limit},
                    {
                        "$lookup": {
                            "from": "tags",
                            "localField": "_id",
                            "foreignField": "_id",
                            "as": "tag",
                        }
                    },
                    {"$unwind": "$tag"},
                    {
                        "$project": {
                            "_id": 0,
                            "id": "$_id",
                            "slug": "$tag.slug",
                            "label": "$tag.label",
                            "markets_count": 1,
                        }
                    },
                ],
            }
        },
    ]
    cursor = await db.markets.aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        return {"items": [], "total": 0}
    result = docs[0]
    metadata = result.get("metadata") or []
    total = metadata[0]["total"] if metadata else 0
    return {"items": result.get("items") or [], "total": total}


@router.get("/{market_id}")
async def get_market(market_id: int, db: Db) -> dict[str, Any]:
    doc = await db.markets.find_one({"_id": market_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return doc
