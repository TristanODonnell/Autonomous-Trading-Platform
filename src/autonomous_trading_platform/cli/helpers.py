from datetime import UTC, datetime


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
