"""
core/admin_reports.py
=====================
Data-access helpers for the AthGad AI Admin Workspace.

Provides:
  - Live county risk/advisory aggregation for the "Predicted Calamities" report
  - Disease-outbreak aggregation for the "Disease Outbreaks" report
  - Subscribed / unsubscribed member queries (with subscription-period formatting)
  - System analytics aggregation for the admin dashboard & charts
"""

from datetime import datetime, timedelta
from sqlalchemy import text

from core.county_registry import get_county_advisory
from core.analytics import AthGadAnalyticsEngine

# The 8 Eastern Kenya counties covered by the early warning system.
COVERED_COUNTIES = [
    "Kitui", "Machakos", "Makueni", "Marsabit",
    "Isiolo", "Meru", "Embu", "Tharaka-Nithi",
]


def _parse_dt(value):
    """Best-effort conversion of a DB timestamp (str or datetime) to datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _fmt_dt(value, fmt="%Y-%m-%d %H:%M"):
    dt = _parse_dt(value)
    return dt.strftime(fmt) if dt else "—"


def format_subscription_period(start_dt, end_dt=None):
    """
    Returns a human-readable subscription duration string.
    Picks the largest whole unit (days/weeks/months/years) that divides
    cleanly into the elapsed duration, per the admin report requirement.
    """
    start = _parse_dt(start_dt)
    end = _parse_dt(end_dt) or datetime.now()
    if not start:
        return "Unknown"
    if end < start:
        return "Unknown"

    days = (end - start).days

    if days < 7:
        return f"{days} day(s)"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week(s)"
    if days < 365:
        months = days // 30
        return f"{months} month(s)"
    years = days // 365
    return f"{years} year(s)"


def get_latest_risk_records(engine):
    """Returns the latest persisted risk-alert record for each covered county."""
    query = text("""
        SELECT alert_code, timestamp, county, calculated_score, risk_level, notified
        FROM (
            SELECT alert_code, timestamp, county, calculated_score, risk_level, notified,
                   ROW_NUMBER() OVER (PARTITION BY county ORDER BY timestamp DESC) as rn
            FROM risk_alerts
        ) sub
        WHERE rn = 1;
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return {row.county: row for row in rows}


def get_predicted_calamities(engine):
    """
    Builds the "Predicted Calamities" dataset from live system output only.
    Only counties with a persisted risk-alert record (i.e. what the dashboard
    is showing) are included — no hardcoded fallback rows are added.
    """
    records = get_latest_risk_records(engine)
    items = []
    for county in COVERED_COUNTIES:
        record = records.get(county)
        if not record:
            continue  # Only include counties the system has actually processed

        raw_score = float(record.calculated_score)
        score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
        risk_level = record.risk_level
        timestamp = record.timestamp

        # Determine the primary calamity the way the app does
        if risk_level == "High" or county in ["Makueni", "Machakos", "Isiolo"]:
            calamity = "Flash Floods, Severe Landslides & Waterborne Outbreaks"
        else:
            calamity = "Severe Multi-Season Drought & Agricultural Deficits"

        advisory = get_county_advisory(county, calamity)

        items.append({
            "county": county,
            "calamity_type": advisory.get("primary_calamity", calamity),
            "risk_level": risk_level,
            "score_pct": score_pct,
            "mitigation_actions": advisory.get("proactive_solutions", []),
            "cascading_effects": advisory.get("cascading_effects", []),
            "predicted_at": _fmt_dt(timestamp),
        })
    return items


def get_disease_outbreaks(engine):
    """
    Builds the "Disease Outbreaks" dataset by aggregating health_records per
    county with the latest reported cases and preventive measures.
    Preventive measures are derived from the county advisory registry.
    """
    query = text("""
        WITH latest AS (
            SELECT county, disease_type, reported_cases, reporting_rate, timestamp,
                   ROW_NUMBER() OVER (PARTITION BY county, disease_type
                                      ORDER BY timestamp DESC) as rn
            FROM health_records
        )
        SELECT county, disease_type, reported_cases, reporting_rate, timestamp
        FROM latest
        WHERE rn = 1
        ORDER BY county, disease_type;
    """)
    disease_map = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        for row in rows:
            disease_map.setdefault(row.county, []).append({
                "disease_type": row.disease_type,
                "reported_cases": row.reported_cases,
                "reporting_rate": row.reporting_rate,
                "timestamp": _fmt_dt(row.timestamp),
            })
    except Exception as e:
        print(f"admin_reports: disease query warning: {e}")
        disease_map = {}

    items = []
    for county in COVERED_COUNTIES:
        county_diseases = disease_map.get(county, [])
        if not county_diseases:
            continue  # Only include counties the system has actually processed
        # Derive preventive measures from the flood/drought advisory blueprint
        advisory = get_county_advisory(
            county,
            "Flash Floods, Severe Landslides & Waterborne Outbreaks",
        )
        items.append({
            "county": county,
            "diseases": county_diseases,
            "preventive_measures": advisory.get("proactive_solutions", []),
        })
    return items


def get_subscribed_members(engine):
    """
    Returns currently subscribed members (is_subscribed = True) with their
    full name and subscription start time.
    """
    query = text("""
        SELECT full_name, email, subscription_started_at, registered_at
        FROM users
        WHERE is_subscribed = True
          AND role != 'admin'
        ORDER BY COALESCE(subscription_started_at, registered_at) DESC;
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    members = []
    for row in rows:
        members.append({
            "full_name": row.full_name,
            "email": row.email,
            "subscribed_at": _fmt_dt(
                row.subscription_started_at or row.registered_at,
                "%Y-%m-%d %H:%M",
            ),
            "subscribed_at_raw": row.subscription_started_at or row.registered_at,
        })
    return members


def get_report_snapshot(engine):
    """
    Lightweight LIVE snapshot of the current system state used by the reports
    hub page and embedded into every generated PDF. Querying the database at
    build/download time guarantees reports always reflect the latest data
    (e.g. a user who just subscribed is immediately included).
    """
    snapshot = {
        "total_users": 0,
        "subscribed_count": 0,
        "unsubscribed_count": 0,
        "prediction_counties": 0,
        "high_risk_counties": 0,
        "dispatch_count": 0,
        "dispatch_failed_count": 0,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    try:
        with engine.connect() as conn:
            snapshot["total_users"] = conn.execute(
                text("SELECT COUNT(*) FROM users")
            ).fetchone()[0]
            snapshot["subscribed_count"] = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE is_subscribed = True")
            ).fetchone()[0]
            snapshot["unsubscribed_count"] = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE is_subscribed = False "
                     "AND unsubscribed_at IS NOT NULL")
            ).fetchone()[0]
            try:
                snapshot["dispatch_count"] = conn.execute(
                    text("SELECT COUNT(*) FROM alert_dispatch_logs")
                ).fetchone()[0]
                snapshot["dispatch_failed_count"] = conn.execute(
                    text("SELECT COUNT(*) FROM alert_dispatch_logs "
                         "WHERE status = 'FAILED'")
                ).fetchone()[0]
            except Exception:
                pass
    except Exception as e:
        print(f"admin_reports: report snapshot counts warning: {e}")

    try:
        with engine.connect() as conn:
            prediction_counties = conn.execute(
                text("SELECT COUNT(DISTINCT county) FROM risk_alerts")
            ).fetchone()[0]
            high_risk = conn.execute(
                text("SELECT COUNT(DISTINCT county) FROM risk_alerts "
                     "WHERE risk_level = 'High'")
            ).fetchone()[0]
            snapshot["prediction_counties"] = prediction_counties
            snapshot["high_risk_counties"] = high_risk
    except Exception as e:
        print(f"admin_reports: report snapshot risk warning: {e}")

    return snapshot


def _ensure_unsubscriptions_email(engine):
    """
    Idempotently ensures the `unsubscriptions.email` column exists so feedback
    rows can be joined to users on the unique email address. Safe to run on
    every call; only applies the DDL when the column is missing.
    """
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE unsubscriptions ADD COLUMN IF NOT EXISTS email VARCHAR(120)"
            ))
    except Exception as e:
        print(f"admin_reports: ensure unsubscriptions.email warning: {e}")


def get_unsubscribed_members(engine):
    """
    Returns members who have unsubscribed, along with their reason for
    unsubscription and the subscription period (days/weeks/months/years).
    Joins the users table with the unsubscriptions feedback table.

    The join is keyed on the user's unique email address (not the display
    name), so reasons can never be attached to the wrong person and
    co-named users are all represented.
    """
    query = text("""
        SELECT u.full_name, u.email, u.subscription_started_at, u.registered_at,
               u.unsubscribed_at, un.reason, un.channel
        FROM users u
        LEFT JOIN unsubscriptions un ON un.email = u.email
        WHERE u.is_subscribed = False
          AND u.unsubscribed_at IS NOT NULL
        ORDER BY u.unsubscribed_at DESC;
    """)
    members = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
    except Exception as e:
        # Older databases may not have unsubscriptions.email yet. Apply the
        # idempotent migration and retry instead of failing the report.
        print(f"admin_reports: unsubscribed query warning ({e}); applying email migration")
        _ensure_unsubscriptions_email(engine)
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
    seen = set()
    for row in rows:
        # De-duplicate by email (a user may have multiple feedback rows)
        if row.email in seen:
            continue
        seen.add(row.email)

        start = row.subscription_started_at or row.registered_at
        members.append({
            "full_name": row.full_name,
            "email": row.email,
            "unsubscribed_at": _fmt_dt(row.unsubscribed_at),
            "reason": (row.reason or "No reason provided").strip(),
            "channel": (row.channel or "email").upper(),
            "subscription_period": format_subscription_period(start, row.unsubscribed_at),
        })
    return members


def get_sms_delivery_logs(engine, limit: int = 100):
    """
    Returns the most recent outbound SMS delivery attempts (newest first) from
    sms_delivery_logs so admins can debug SMS issues in realtime — status,
    Africa's Talking status, cost, message id, and error detail.
    """
    query = text("""
        SELECT logged_at, phone_number, name, message_type, tier, status,
               http_status, at_status, cost, message_id, error_detail
        FROM sms_delivery_logs
        ORDER BY logged_at DESC
        LIMIT :limit;
    """)
    logs = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"limit": limit}).fetchall()
        for row in rows:
            logs.append({
                "logged_at": _fmt_dt(row.logged_at, "%Y-%m-%d %H:%M:%S"),
                "phone_number": row.phone_number or "—",
                "name": row.name or "",
                "message_type": row.message_type or "notification",
                "tier": row.tier or "none",
                "status": row.status or "unknown",
                "http_status": row.http_status,
                "at_status": row.at_status or "",
                "cost": row.cost or "",
                "message_id": row.message_id or "",
                "error_detail": row.error_detail or "",
            })
    except Exception as e:
        print(f"admin_reports: sms delivery logs warning: {e}")
    return logs


def get_alert_dispatch_logs(engine, limit: int = 500):
    """
    Returns the most recent tracked SMS/email dispatches (newest first) from
    alert_dispatch_logs. Each row carries the recipient identifier (phone
    number for SMS, email address for email), the exact message that was sent,
    and the recipient's subscription status at dispatch time.
    """
    query = text("""
        SELECT dispatch_code, dispatched_at, channel, recipient, message_type,
               message_content, subscription_status, status, error_detail
        FROM alert_dispatch_logs
        ORDER BY dispatched_at DESC
        LIMIT :limit;
    """)
    logs = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"limit": limit}).fetchall()
        for row in rows:
            logs.append({
                "dispatch_code": row.dispatch_code or "",
                "dispatched_at": _fmt_dt(row.dispatched_at, "%Y-%m-%d %H:%M:%S"),
                "channel": row.channel or "sms",
                "recipient": row.recipient or "—",
                "message_type": row.message_type or "alert",
                "message": row.message_content or "",
                "subscription_status": row.subscription_status or "unknown",
                "status": row.status or "unknown",
                "error_detail": row.error_detail or "",
            })
    except Exception as e:
        print(f"admin_reports: alert dispatch logs warning: {e}")
    return logs


def prune_risk_alerts(engine, keep_days: int = 30):
    """
    Deletes risk_alerts rows older than `keep_days` days. Called after full
    recomputes (which insert a fresh row per county per run) so the table does
    not grow without bound.
    """
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                    DELETE FROM risk_alerts
                    WHERE timestamp < NOW() - make_interval(days => :days)
                """),
                {"days": keep_days},
            )
    except Exception as e:
        print(f"admin_reports: risk_alerts prune warning: {e}")


def _threat_for(county, risk_level):
    """
    Returns dynamic (threat_category, primary_threat) for a county based on its
    live risk level, mirroring the threat assignments used across the telemetry
    board so the admin analytics page stays consistent with the dashboard.
    """
    if risk_level == "High" or county in ["Marsabit", "Isiolo"]:
        return "health", "Water Contamination & Vector Outbreak"
    return "climate", "Rainfall Deficit & Soil Moisture Loss"


def _resolve_county_risk(engine, county, analytics_engine, records=None):
    """
    Resolves the most current risk snapshot for a county.

    Uses the latest persisted risk_alerts record when available, otherwise falls
    back to a live recomputation via the analytics engine so the admin analytics
    charts are never empty. Returns a dict with score, level, forecast and
    advisory enrichment.

    `records` is the optional latest-record-per-county map from
    `get_latest_risk_records`; when provided it is reused so the admin analytics
    aggregation does not re-run the per-county window query N times.
    """
    if records is None:
        records = get_latest_risk_records(engine)
    rec = records.get(county)

    if rec:
        raw = float(rec.calculated_score)
        score_pct = round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)
        level = rec.risk_level
        live = None
    else:
        # No persisted record yet -> compute live (also persists a fresh row).
        try:
            live = analytics_engine.calculate_composite_risk(county)
            raw = float(live.get("composite_risk_score", 0.3))
            score_pct = round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)
            level = live.get("risk_level", "Low")
        except Exception as e:
            print(f"admin_reports: live risk fallback warning ({county}): {e}")
            score_pct = round(0.0, 1)
            level = "Low"
            live = None

    # Dynamic threat profile (climate vs health) consistent with the dashboard.
    threat_category, primary_threat = _threat_for(county, level)

    # Forecast trajectory (7-day trend). When a live recomputation just ran it
    # already attached a forecast, so we avoid recomputing it a second time.
    try:
        if live is not None:
            forecast = live.get("forecast", {})
        else:
            forecast = analytics_engine.forecast_risk(county=county, horizon=7)
        trend = forecast.get("trend", "stable")
        forecast_scores = forecast.get("scores", [])
    except Exception as e:
        print(f"admin_reports: forecast warning ({county}): {e}")
        trend = "stable"
        forecast_scores = []

    # Advisory blueprint for the county's predicted calamity.
    if level == "High" or county in ["Makueni", "Machakos", "Isiolo"]:
        calamity = "Flash Floods, Severe Landslides & Waterborne Outbreaks"
    else:
        calamity = "Severe Multi-Season Drought & Agricultural Deficits"
    advisory = get_county_advisory(county, calamity)

    return {
        "county": county,
        "score_pct": score_pct,
        "risk_level": level,
        "threat_category": threat_category,
        "primary_threat": primary_threat,
        "trend": trend,
        "forecast_scores": forecast_scores,
        "calamity_type": advisory.get("primary_calamity", calamity),
        "vulnerability_drivers": advisory.get("vulnerability_drivers", ""),
        "proactive_solutions": advisory.get("proactive_solutions", []),
        "cascading_effects": advisory.get("cascading_effects", []),
    }


def get_analytics(engine):
    """
    Aggregates system-wide analytics used by the admin dashboard and charts:
      - user & subscription counts
      - risk-level distribution across covered counties
      - average risk scores
      - per-county live risk board with forecast & advisory enrichment
      - report-download/audit counts
    """
    result = {
        "total_users": 0,
        "subscribed_count": 0,
        "unsubscribed_count": 0,
        "admin_count": 0,
        "alert_count": 0,
        "risk_distribution": {"Low": 0, "Medium": 0, "High": 0},
        "risk_distribution_pct": {"Low": 0, "Medium": 0, "High": 0},
        "avg_climate": round(0.0, 1),
        "avg_health": round(0.0, 1),
        "highest_risk_county": "—",
        "total_risk_index": 0.0,
        "high_risk_count": 0,
        "status_board": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    try:
        with engine.connect() as conn:
            total_users = conn.execute(
                text("SELECT COUNT(*) AS c FROM users")
            ).fetchone()
            subscribed = conn.execute(
                text("SELECT COUNT(*) AS c FROM users WHERE is_subscribed = True")
            ).fetchone()
            unsubscribed = conn.execute(
                text("SELECT COUNT(*) AS c FROM users WHERE is_subscribed = False "
                     "AND unsubscribed_at IS NOT NULL")
            ).fetchone()
            admins = conn.execute(
                text("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'")
            ).fetchone()
            alerts = conn.execute(
                text("SELECT COUNT(*) AS c FROM risk_alerts")
            ).fetchone()

            result["total_users"] = total_users.c
            result["subscribed_count"] = subscribed.c
            result["unsubscribed_count"] = unsubscribed.c
            result["admin_count"] = admins.c
            result["alert_count"] = alerts.c
    except Exception as e:
        print(f"admin_reports: analytics count warning: {e}")

    # Build the live risk board with forecast + advisory enrichment.
    # Fetch the latest persisted record per county ONCE and reuse it across all
    # counties to avoid re-running the window query for every county.
    analytics_engine = AthGadAnalyticsEngine()
    latest_records = get_latest_risk_records(engine)
    highest = None
    climate_scores = []
    health_scores = []
    total_risk = 0.0

    for county in COVERED_COUNTIES:
        item = _resolve_county_risk(engine, county, analytics_engine, records=latest_records)

        result["risk_distribution"][item["risk_level"]] = (
            result["risk_distribution"].get(item["risk_level"], 0) + 1
        )
        result["status_board"].append(item)

        if item["risk_level"] == "High":
            result["high_risk_count"] += 1

        if item["threat_category"] == "health":
            health_scores.append(item["score_pct"])
        else:
            climate_scores.append(item["score_pct"])

        total_risk += item["score_pct"]

        if highest is None or item["score_pct"] > highest[1]:
            highest = (item["county"], item["score_pct"])

    if highest:
        result["highest_risk_county"] = highest[0]

    if climate_scores:
        result["avg_climate"] = round(sum(climate_scores) / len(climate_scores), 1)
    if health_scores:
        result["avg_health"] = round(sum(health_scores) / len(health_scores), 1)

    if result["status_board"]:
        result["total_risk_index"] = round(
            sum(c["score_pct"] for c in result["status_board"]) / len(result["status_board"]),
            1,
        )

    # Compute percentage for each risk tier (used for the progress bars).
    total_dist = (
        result["risk_distribution"].get("Low", 0)
        + result["risk_distribution"].get("Medium", 0)
        + result["risk_distribution"].get("High", 0)
    )
    for tier in ("Low", "Medium", "High"):
        count = result["risk_distribution"].get(tier, 0)
        result["risk_distribution_pct"][tier] = (
            round((count / total_dist) * 100, 1) if total_dist > 0 else 0
        )

    return result
