"""
core/time_utils.py
==================
Centralized datetime helpers for the AthGad AI application.

Convention: all trial / subscription / expiry logic compares NAIVE UTC
timestamps, which matches PostgreSQL TIMESTAMP columns (no tz) written via
NOW() (UTC). Never compare naive local `datetime.now()` values against DB
timestamps — on a non-UTC server that silently shifts every comparison by the
UTC offset.
"""

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    EAT = ZoneInfo("Africa/Nairobi")
except Exception:  # pragma: no cover - tzdata not installed
    EAT = None


def utc_now() -> datetime:
    """Current time as a naive UTC datetime (matches PostgreSQL NOW())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_dt(value):
    """
    Parses a datetime object or ISO-8601 string into a datetime, tolerating both
    naive and aware inputs. Returns None when the value cannot be parsed.
    """
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
