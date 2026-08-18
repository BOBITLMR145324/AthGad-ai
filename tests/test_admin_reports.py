from datetime import date, datetime, timedelta

import pytest

from core import admin_reports


def test_range_where_none_none():
    sql, params = admin_reports._range_where("timestamp", None, None)
    assert sql == ""
    assert params == {}


def test_range_where_date_from():
    sql, params = admin_reports._range_where("timestamp", date(2026, 1, 1), None)
    assert sql == " AND timestamp >= :date_from"
    assert params == {"date_from": date(2026, 1, 1)}


def test_range_where_date_to_is_inclusive():
    sql, params = admin_reports._range_where("timestamp", None, date(2026, 1, 1))
    assert sql == " AND timestamp < :date_to_excl"
    assert params["date_to_excl"] == date(2026, 1, 2)


def test_range_where_allowed_column_prefixed():
    sql, params = admin_reports._range_where("u.unsubscribed_at", date(2026, 1, 1), None)
    assert "u.unsubscribed_at" in sql


def test_range_where_allowed_coalesce_expression():
    sql, _ = admin_reports._range_where("COALESCE(subscription_started_at, registered_at)", None, None)
    assert sql == ""


def test_range_where_rejects_injection():
    with pytest.raises(ValueError, match="Unsafe range column"):
        admin_reports._range_where("timestamp; DROP TABLE alerts; --", None, None)


def test_range_where_rejects_arbitrary_column():
    with pytest.raises(ValueError, match="Unsafe range column"):
        admin_reports._range_where("user_id", None, None)


def test_format_subscription_period_days():
    start = datetime(2026, 1, 1)
    assert admin_reports.format_subscription_period(start, start + timedelta(days=5)) == "5 day(s)"


def test_format_subscription_period_weeks():
    start = datetime(2026, 1, 1)
    assert admin_reports.format_subscription_period(start, start + timedelta(days=14)) == "2 week(s)"


def test_format_subscription_period_months():
    start = datetime(2026, 1, 1)
    assert admin_reports.format_subscription_period(start, start + timedelta(days=90)) == "3 month(s)"


def test_format_subscription_period_unknown():
    assert admin_reports.format_subscription_period(None) == "Unknown"
    assert admin_reports.format_subscription_period("garbage") == "Unknown"


def test_format_subscription_period_reversed_unknown():
    start = datetime(2026, 1, 1)
    assert admin_reports.format_subscription_period(start, start - timedelta(days=1)) == "Unknown"
