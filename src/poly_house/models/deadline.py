"""Generic time series models."""

import re
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from pydantic import BaseModel

from zoneinfo import ZoneInfo

from poly_house.models import Market
from poly_house.models.contract_error import ContractError

POLYMARKET_TZ = ZoneInfo("America/New_York")

_GROUP_TITLE_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?$")

_WEEKDAY_PREFIX_RE = re.compile(
    r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+",
    re.IGNORECASE,
)

_MONTH_FULL: dict[str, int] = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


def _normalize_group_item_title(title: str) -> str:
    """Strip optional weekday prefix from calendar titles.

    Supports ``June 15``, ``June 30, 2026``, ``Monday, June 16``, ``Monday June 16``.
    """
    return _WEEKDAY_PREFIX_RE.sub("", title.strip())


def _to_polymarket_deadline(year: int, month: int, day: int) -> datetime:
    """Convert a date to a deadline in Polymarket timezone (ET)."""
    return datetime(year, month, day, 23, 59, 59, 999_999, tzinfo=POLYMARKET_TZ)


class Deadline(BaseModel):
    """A deadline for a calendar market."""

    market_id: int
    group_item_title: str
    group_item_threshold: int
    month: int
    day: int
    year: int | None = None

    def to_date(self) -> datetime:
        """Calendar date from ``group_item_title`` parts."""
        if self.year is None:
            raise ContractError(
                f"group_item_title {self.group_item_title!r}: year not set"
            )
        try:
            return _to_polymarket_deadline(self.year, self.month, self.day)
        except ValueError as exc:
            raise ContractError(
                f"group_item_title {self.group_item_title!r}: invalid date"
            ) from exc


def _deadline_from_group_item(market: Market) -> Deadline | None:
    market_id = market.id
    original_title = market.group_item_title
    threshold = market.group_item_threshold

    if not original_title:
        return None
    if threshold is None:
        return None

    title = _normalize_group_item_title(original_title)
    m = _GROUP_TITLE_RE.fullmatch(title)
    if not m:
        return None
    month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
    month = _MONTH_FULL.get(month_name) or _MONTH_FULL.get(month_name.title())
    if month is None:
        return None
    day = int(day_s)
    if not 1 <= day <= 31:
        return None
    return Deadline(
        market_id=market_id,
        group_item_title=original_title,
        group_item_threshold=threshold,
        month=month,
        day=day,
        year=int(year_s) if year_s is not None else None,
    )


def _is_deadlines_filled(deadlines: list[Deadline]) -> bool:
    return all(deadline.year is not None for deadline in deadlines)


def _is_deadlines_monotonic(
    deadlines: list[Deadline], *, permissible_deviation_days: int = 100
) -> bool:
    dates = (d.to_date() for d in deadlines)
    permissible_delta = timedelta(days=permissible_deviation_days)
    return all(a - permissible_delta < b for a, b in pairwise(dates))


def _calendar_from_group_items(
    markets: list[Market], event_start_date: str
) -> list[Deadline]:
    deadlines = []

    for market in markets:
        deadline = _deadline_from_group_item(market)
        if deadline is None:
            continue
        deadlines.append(deadline)

    if len(deadlines) == 0:
        return deadlines

    deadlines.sort(key=lambda x: (x.group_item_threshold, x.month, x.day))

    if _is_deadlines_filled(deadlines):
        return deadlines

    event_start = datetime.fromisoformat(event_start_date)

    first_deadline = deadlines[0]
    if first_deadline.year is None:
        if first_deadline.month >= event_start.month:
            first_deadline.year = event_start.year
        else:
            first_deadline.year = event_start.year + 1

    n = len(deadlines)

    for i in range(n - 1):
        pre_deadline = deadlines[i]
        deadline = deadlines[i + 1]
        if deadline.year is None:
            if deadline.month >= pre_deadline.month:
                deadline.year = pre_deadline.year
            else:
                deadline.year = pre_deadline.year + 1

    if not _is_deadlines_monotonic(deadlines):
        raise ContractError("deadlines are not monotonically increasing")

    return deadlines


def get_deadline_map_for_markets(
    markets: list[Market], event_start_date: str, event_end_date: str | None
) -> tuple[dict[int, datetime], bool]:
    if not markets:
        return {}, False

    deadlines = _calendar_from_group_items(markets, event_start_date)
    calendar_map = {
        deadline.market_id: deadline.to_date() for deadline in deadlines
    }
    deadline_map: dict[int, datetime] = {}
    for market in markets:
        deadline = calendar_map.get(market.id)
        if deadline is not None:
            deadline_map[market.id] = deadline
            continue
        end_date = market.end_date or event_end_date
        if end_date:
            deadline_map[market.id] = datetime.fromisoformat(end_date)

    return deadline_map, len(markets) == len(deadlines)
