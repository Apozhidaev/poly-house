HOUR_SEC = 3_600
DAY_SEC = 86_400

def hour_timestamp(timestamp_ms: int) -> int:
    return (timestamp_ms // 1000 // HOUR_SEC) * HOUR_SEC

def day_timestamp(timestamp_ms: int) -> int:
    return (timestamp_ms // 1000 // DAY_SEC) * DAY_SEC