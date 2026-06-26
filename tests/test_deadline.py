from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from poly_house.models import Market
from poly_house.models.deadline import (
    POLYMARKET_TZ,
    _deadline_from_group_item_title,
    _title_deadlines_from_markets,
)


def _market(
    *,
    market_id: int,
    group_item_title: str | None,
    group_item_threshold: int | None,
) -> Market:
    return Market.model_validate(
        {
            "id": market_id,
            "conditionId": f"0x{market_id:064x}",
            "slug": f"market-{market_id}",
            "question": "Q?",
            "closed": False,
            "active": True,
            "archived": False,
            "updatedAt": "2026-01-01T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
            "groupItemTitle": group_item_title,
            "groupItemThreshold": group_item_threshold,
        }
    )


@pytest.mark.parametrize(
    ("title", "threshold", "month", "day", "year"),
    [
        ("June 15", 1, 6, 15, None),
        ("June 30, 2026", 1, 6, 30, 2026),
        ("Monday, June 16", 1, 6, 16, None),
        ("Tuesday June 17", 2, 6, 17, None),
        ("Sunday, June 22, 2026", 7, 6, 22, 2026),
    ],
)
def test_deadline_from_group_item_title_parses_calendar_titles(
    title: str,
    threshold: int,
    month: int,
    day: int,
    year: int | None,
) -> None:
    deadline = _deadline_from_group_item_title(
        _market(
            market_id=1,
            group_item_title=title,
            group_item_threshold=threshold,
        )
    )

    assert deadline is not None
    assert deadline.group_item_title == title
    assert deadline.month == month
    assert deadline.day == day
    assert deadline.year == year


def test_deadline_from_group_item_title_returns_none_for_invalid_title() -> None:
    assert (
        _deadline_from_group_item_title(
            _market(
                market_id=1,
                group_item_title="Not a date",
                group_item_threshold=1,
            )
        )
        is None
    )


def test_title_deadlines_from_markets_weekday_series() -> None:
    titles = [
        "Monday, June 16",
        "Tuesday, June 17",
        "Wednesday, June 18",
        "Thursday, June 19",
        "Friday, June 20",
        "Saturday, June 21",
        "Sunday, June 22",
    ]
    markets = [
        _market(
            market_id=i + 1,
            group_item_title=title,
            group_item_threshold=i + 1,
        )
        for i, title in enumerate(titles)
    ]
    event_start = datetime(2026, 6, 1, tzinfo=ZoneInfo("UTC"))

    deadlines = _title_deadlines_from_markets(markets, event_start)

    assert len(deadlines) == 7
    dates = [deadline.to_date() for deadline in deadlines]
    assert all(date.tzinfo == POLYMARKET_TZ for date in dates)
    assert dates == sorted(dates)
    assert dates[0].month == 6
    assert dates[0].day == 16
    assert dates[-1].day == 22
