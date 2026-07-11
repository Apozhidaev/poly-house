from datetime import UTC, datetime

HOUR_SEC = 3_600
DAY_SEC = 86_400


def hour_timestamp_from_ms(timestamp_ms: int) -> int:
    return (timestamp_ms // 1000 // HOUR_SEC) * HOUR_SEC


def day_timestamp(timestamp: int) -> int:
    return (timestamp // DAY_SEC) * DAY_SEC


def compute_days_left_until_deadline(deadline: datetime | None) -> int | None:
    if deadline is None:
        return None
    now_ts = int(datetime.now(UTC).timestamp())
    today_day = day_timestamp(now_ts)
    deadline_day = day_timestamp(int(deadline.timestamp()))
    days_left = (deadline_day - today_day) // DAY_SEC
    return days_left if days_left >= 0 else 0