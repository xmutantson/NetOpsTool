from datetime import datetime, timezone


def utc_naive(value: datetime) -> datetime:
    """Persist UTC consistently in the existing timezone-naive SQL columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
