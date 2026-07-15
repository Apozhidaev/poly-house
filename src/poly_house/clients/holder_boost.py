from __future__ import annotations

from typing import Any, Self

import httpx
import structlog

from poly_house.config import Settings

logger = structlog.get_logger()


class HolderBoostClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.holder_boost_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=None)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def predict(
        self,
        *,
        tag_id: int,
        days_left: int,
        holders: list[dict[str, Any]],
    ) -> list[float] | None:
        try:
            resp = await self._client.post(
                f"{self._base}/predict",
                json={
                    "tag_id": tag_id,
                    "days_left": days_left,
                    "holders": holders,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            probs = data.get("predict_probs")
            if not isinstance(probs, list):
                return None
            return [float(p) for p in probs]
        except (httpx.HTTPStatusError, httpx.RequestError, TypeError, ValueError) as exc:
            logger.warning(
                "holder_boost_predict_failed",
                tag_id=tag_id,
                days_left=days_left,
                error=str(exc),
            )
            return None

    async def features(self, tag_id: int) -> list[dict[str, Any]] | None:
        try:
            resp = await self._client.get(f"{self._base}/features/{tag_id}")
            resp.raise_for_status()
            data = resp.json()
            raw_features = data.get("features")
            if not isinstance(raw_features, list):
                return None
            features: list[dict[str, Any]] = []
            for item in raw_features:
                if not isinstance(item, dict):
                    continue
                feature = item.get("feature")
                importance = item.get("importance")
                if not isinstance(feature, str) or not isinstance(
                    importance, (int, float)
                ):
                    continue
                features.append(
                    {"feature": feature, "importance": float(importance)}
                )
            return features
        except (httpx.HTTPStatusError, httpx.RequestError, TypeError, ValueError) as exc:
            logger.warning(
                "holder_boost_features_failed",
                tag_id=tag_id,
                error=str(exc),
            )
            return None
