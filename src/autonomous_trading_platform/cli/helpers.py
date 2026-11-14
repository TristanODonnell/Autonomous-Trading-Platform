from datetime import UTC, date, datetime, time


def parse_datetime(value: str) -> datetime:
    """
    Parse a CLI datetime string into a UTC datetime.

    Accepted formats:
    - ISO8601 with Z:     2024-01-01T10:00:00Z
    - ISO8601 without Z:  2024-01-01T10:00:00
    - Date only:          2024-01-01  (interpreted as midnight UTC)
    """
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(UTC)

    except Exception as exc:
        raise ValueError(f"Invalid datetime format: {value}") from exc


def parse_end_datetime(value: str) -> datetime:
    """
    Parse the inclusive end of a CLI date window into a UTC datetime.

    A date-only value (2024-01-31) means the whole of that day, so it resolves to
    23:59:59.999999 UTC and `.date()` is still the day the caller typed. A value with
    a time component is taken as the exact instant, like `parse_datetime`.
    """
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return parse_datetime(value)
    return datetime.combine(day, time.max, tzinfo=UTC)
