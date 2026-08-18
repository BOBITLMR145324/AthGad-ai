from datetime import datetime, timezone, timedelta

import pytest

from core.time_utils import utc_now, parse_dt, EAT


def test_utc_now_is_utc_naive():
    now = utc_now()
    assert isinstance(now, datetime)
    assert now.tzinfo is None


def test_utc_now_close_to_clock():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    now = utc_now()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert before <= now <= after


def test_parse_dt_from_iso():
    value = "2026-08-01T12:34:56"
    parsed = parse_dt(value)
    assert parsed == datetime(2026, 8, 1, 12, 34, 56)


def test_parse_dt_with_timezone():
    value = "2026-08-01T12:34:56+00:00"
    parsed = parse_dt(value)
    assert parsed.tzinfo is not None


def test_parse_dt_none():
    assert parse_dt(None) is None


def test_parse_dt_invalid():
    assert parse_dt("not-a-date") is None


def test_eat_offset():
    assert EAT.utcoffset(datetime(2026, 1, 1)) == timedelta(hours=3)
