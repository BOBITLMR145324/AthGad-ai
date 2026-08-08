import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session, render_template_string
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

# --- Security hardening imports ---
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter

# Core security helpers
from core.security import (
    get_secret_key,
    apply_security_headers,
    audit,
    client_identifier,
)

# CORS hardening: restrict cross-origin access to an explicit whitelist rather
# than allowing all origins globally. Origins are read from CORS_ALLOWED_ORIGINS
# env var (comma-separated). Dev defaults cover localhost.
def _cors_origins():
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000")
    return [o.strip() for o in raw.split(",") if o.strip()]

# Core Modules & Pipeline Helpers
from core.analytics import AthGadAnalyticsEngine
from services.alert_service import AthGadAlertService
from services.mpesa_service import initiate_stk_push
from core.db_helper import get_db_engine
from core.county_registry import get_county_advisory

# Admin workspace modules
from core.admin_reports import get_analytics, prune_risk_alerts
from services.pdf_report_service import build_admin_report

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
# Restrict CORS to the explicit whitelist instead of allowing all origins.
CORS(app, resources={r"/api/*": {"origins": _cors_origins()}})

# --- Security hardening: secret key (never hardcoded) ---
app.secret_key = get_secret_key()

# --- Security hardening: CSRF protection for all POST forms ---
csrf = CSRFProtect(app)

# --- Security hardening: rate limiting (in-memory storage) ---
limiter = Limiter(
    key_func=client_identifier,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# Global security headers on every response (TLS/HSTS/CSP/clickjacking)
app.after_request(apply_security_headers)

# Initialize core microservice instances
analytics_engine = AthGadAnalyticsEngine()
alert_service = AthGadAlertService()

TRIAL_DAYS = 30

# The 8 Eastern Kenya counties covered by the early warning system.
COVERED_COUNTIES = ["Kitui", "Machakos", "Makueni", "Marsabit", "Isiolo", "Meru", "Embu", "Tharaka-Nithi"]


try:
    EAT = ZoneInfo("Africa/Nairobi")
except Exception:
    EAT = None


def _eat_now():
    """Returns the current time in East Africa Time (UTC+3, no DST)."""
    return datetime.now(tz=EAT) if EAT else datetime.now()


def _eat_str(fmt="%H:%M EAT"):
    """Formats the current time in East Africa Time with an 'EAT' label."""
    return _eat_now().strftime(fmt)


def _compute_live_county_risk(county):
    """
    Runs the analytics engine for a single county and returns (score_pct,
    risk_level) based on the freshly recomputed composite score.
    """
    try:
        live_data = analytics_engine.calculate_composite_risk(county)
        raw_score = float(live_data.get("composite_risk_score", 0.3))
        score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
        risk_level = live_data.get("risk_level", "Low")
        # Attach the 7-day forecast trend for richer live boards.
        forecast_trend = live_data.get("forecast", {}).get("trend", "stable")
        return score_pct, risk_level, forecast_trend
    except Exception as e:
        print(f"Live county recompute error ({county}): {e}")
        return 30.0, "Low", "stable"


def _build_status_board(force_recompute=False):
    """
    Computes the live composite-risk status board for all covered counties.

    - When force_recompute=False (default): pulls the latest persisted
      calculation from PostgreSQL when available and only falls back to the
      analytics engine for counties with no logged record yet. This keeps
      normal page loads fast.
    - When force_recompute=True: recomputes every county through the
      analytics engine (persisting fresh records) so the Refresh button always
      returns accurate live information for all counties.

    Returns a list of dicts ordered by the covered-counties registry.
    """
    engine = get_db_engine()

    query = """
        SELECT id, timestamp::text, county, calculated_score, risk_level, notified
        FROM (
            SELECT id, timestamp, county, calculated_score, risk_level, notified,
                   ROW_NUMBER() OVER (PARTITION BY county ORDER BY timestamp DESC) as rn
            FROM risk_alerts
        ) sub
        WHERE rn = 1;
    """

    status_board = []
    climate_scores = []
    health_scores = []

    try:
        if force_recompute:
            db_records = {}
        else:
            with engine.connect() as connection:
                result = connection.execute(text(query))
                db_records = {row.county: row for row in result}

        for county in COVERED_COUNTIES:
            has_record = (not force_recompute) and (county in db_records)
            record = db_records.get(county) if has_record else None

            if has_record:
                raw_score = float(record.calculated_score)
                score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                risk_level = record.risk_level
                forecast_trend = "stable"
            else:
                # Fresh recomputation for all counties on refresh, or for
                # counties with no persisted record on normal loads.
                score_pct, risk_level, forecast_trend = _compute_live_county_risk(county)

            if risk_level == "High" or county in ["Marsabit", "Isiolo"]:
                threat_category = "health"
                primary_threat = "Water Contamination & Vector Outbreak"
                health_scores.append(score_pct)
            else:
                threat_category = "climate"
                primary_threat = "Rainfall Deficit & Soil Moisture Loss"
                climate_scores.append(score_pct)

            status_board.append({
                "county": county,
                "score_pct": score_pct,
                "risk_level": risk_level,
                "threat_category": threat_category,
                "primary_threat": primary_threat,
                "trend": forecast_trend
            })

        # Full recomputes insert fresh rows for every county; cap the table so
        # risk_alerts does not grow without bound.
        if force_recompute:
            prune_risk_alerts(engine, keep_days=30)

    except Exception as e:
        print(f"Telemetry Data Load Warning: {e}")
        for county in COVERED_COUNTIES:
            status_board.append({
                "county": county,
                "score_pct": 0.0,
                "risk_level": "Low",
                "threat_category": "climate",
                "primary_threat": "Monitoring Sync Active"
            })

    avg_climate = round(sum(climate_scores) / len(climate_scores), 1) if climate_scores else 0.0
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else 0.0
    high_risk_count = sum(1 for c in status_board if c['risk_level'] == 'High')

    return {
        "status_board": status_board,
        "avg_climate": avg_climate,
        "avg_health": avg_health,
        "high_risk_count": high_risk_count,
    }


# =====================================================================
# ADMIN AUTHORIZATION HELPERS
# =====================================================================

from functools import wraps

def admin_required(view_func):
    """
    Decorator enforcing that the current session belongs to an admin user.

    The role is re-validated against the database on every request (not just
    trusted from the session cookie) so role changes — e.g. an admin demoted by
    another admin — take effect immediately. Non-admins / unauthenticated
    requests are redirected to the login page, and an audit log entry is
    recorded for the denied access attempt.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        email = session.get('user_email')

        if not email:
            flash("Please sign in to access the admin workspace.", "warning")
            return redirect(url_for('login_page'))

        # Re-validate the role against the DB so stale session roles cannot
        # grant (or persist) admin access after a role change.
        role = None
        try:
            engine = get_db_engine()
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT role FROM users WHERE email = :email"),
                    {"email": email},
                ).fetchone()
                role = row.role if row else None
        except Exception as e:
            print(f"Admin role verification warning ({email}): {e}")
            role = session.get('user_role')

        if role != 'admin':
            audit("admin_access_denied", actor=email,
                  outcome="denied", details={"reason": "not_admin"})
            session.pop('user_role', None)
            flash("You are not authorized to access the admin workspace.", "error")
            return redirect(url_for('dashboard'))

        # Keep the session role in sync with the DB.
        session['user_role'] = 'admin'
        return view_func(*args, **kwargs)
    return wrapper


def _generate_unsub_ref(connection, full_name):
    """
    Builds a unique primary key for the unsubscriptions table using the person's
    full name. If the name already appears in the table, a numeric suffix is
    appended (1, 2, ...) so every row remains unique.
    """
    existing = connection.execute(
        text("SELECT unsub_ref FROM unsubscriptions WHERE unsub_ref = :n OR unsub_ref LIKE :np"),
        {"n": full_name, "np": f"{full_name}%"}
    ).fetchall()
    count = len(existing)
    if count == 0:
        return full_name
    return f"{full_name}{count}"


@app.route('/')
def landing():
    """Serves the public welcoming landing page for non-authenticated visitors."""
    if 'user_email' in session:
        return redirect(url_for('dashboard'))

    # Pass a live summary snapshot so the risk card renders with real data
    # on first load (then JS keeps it fresh via /api/v1/live-summary).
    summary = _build_status_board()
    return render_template(
        'landing.html',
        avg_climate=summary["avg_climate"],
        avg_health=summary["avg_health"],
        high_risk_count=summary["high_risk_count"],
        last_sync=_eat_str(),
    )


@app.route('/api/v1/live-summary', methods=['GET'])
def live_summary():
    """
    Returns computed live risk signals for the landing page card.
    Public (no auth required). Recomputes the composite scores for all
    covered counties and returns averages/trends for the dashboard widget.
    """
    try:
        summary = _build_status_board()

        # Build a compact map of the risk levels for the landing widget
        risk_map = {c["county"]: c for c in summary["status_board"]}

        # Hidden-pattern / anomaly trend: derive from the highest-risk driver
        high_counties = [c for c in summary["status_board"] if c["risk_level"] == "High"]
        if high_counties:
            hidden_pattern_label = "Elevated"
            hidden_pattern_color = "text-rose-400"
        elif summary["avg_climate"] >= 60 or summary["avg_health"] >= 60:
            hidden_pattern_label = "Watch"
            hidden_pattern_color = "text-amber-400"
        else:
            hidden_pattern_label = "Normal"
            hidden_pattern_color = "text-emerald-400"

        return jsonify({
            "status": "ok",
            "avg_climate": summary["avg_climate"],
            "avg_health": summary["avg_health"],
            "high_risk_count": summary["high_risk_count"],
            "drought_risk": summary["avg_climate"],
            "disease_risk": summary["avg_health"],
            "hidden_pattern": hidden_pattern_label,
            "hidden_pattern_color": hidden_pattern_color,
            "system_status": "Connected",
            "risk_map": risk_map,
            "last_sync": _eat_str(),
            "counties": summary["status_board"],
        }), 200

    except Exception as e:
        print(f"Live summary error: {e}")
        return jsonify({
            "status": "error",
            "message": "Could not load live risk signals right now. Please try again."
        }), 500


@app.route('/dashboard')
def dashboard():
    """Serves the central AthGad AI operational telemetry board."""
    if 'user_email' in session:
        return render_template('index.html')

    flash("Please sign in to view your risk dashboard.", "warning")
    return redirect(url_for('login_page'))


@app.route('/api/v1/health', methods=['GET'])
def get_system_health():
    """Returns the operational status of the AthGad API layer."""
    return jsonify({
        "status": "online",
        "engine": "AthGad AI Engine v1.0",
        "region_scope": "Eastern Kenya (8 Counties)"
    }), 200


@app.route('/api/v1/risk-status', methods=['GET'])
def get_realtime_risk_status():
    """
    Executes live data fusion, appends localized county vulnerabilities,
    evaluates alert thresholds, and logs markers directly to PostgreSQL.
    """
    target_county = request.args.get('county', default='Kitui')

    try:
        # 1. Trigger AI Core calculations
        risk_data = analytics_engine.calculate_composite_risk(target_county)

        # 2. Extract the dynamic risk level status string cleanly
        current_severity = risk_data.get("risk_level", "Medium")

        # 3. Assign a dynamic hazard profile category based on severity/location
        if current_severity == "High" or target_county in ["Makueni", "Machakos", "Isiolo"]:
            live_calamity = "Flash Floods, Severe Landslides & Waterborne Outbreaks"
        else:
            live_calamity = "Severe Multi-Season Drought & Agricultural Deficits"

        # 4. Enrich payload using our safe multi-parameter function
        risk_data["county"] = target_county
        risk_data["calamity_type"] = live_calamity
        risk_data["advisory"] = get_county_advisory(target_county, live_calamity)

        # 5. Pass clean parameters to notification loops
        alert_service.engine = get_db_engine()
        alert_service.dispatch_critical_notification(target_county, risk_data)

        # 6. Format numerical score to percentage string safely for JSON response
        score_val = risk_data.get("composite_risk_score", 0.0)
        if isinstance(score_val, (int, float)) and score_val <= 1.0:
            risk_data["composite_risk_score"] = f"{round(score_val * 100, 1)}%"

        return jsonify(risk_data), 200

    except Exception as e:
        print(f"Backend route exception intercepted: {str(e)}")
        return jsonify({"status": "error", "message": "We could not update the risk information right now. Please try again in a moment."}), 500


@app.route('/api/v1/alerts/history', methods=['GET'])
def get_alert_history():
    """Queries PostgreSQL to pull the latest system calculation for each of the 8 covered counties."""
    engine = get_db_engine()
    covered_counties = ["Kitui", "Machakos", "Makueni", "Marsabit", "Isiolo", "Meru", "Embu", "Tharaka-Nithi"]

    query = """
        SELECT id, timestamp::text, county, calculated_score, risk_level, notified
        FROM (
            SELECT id, timestamp, county, calculated_score, risk_level, notified,
                   ROW_NUMBER() OVER (PARTITION BY county ORDER BY timestamp DESC) as rn
            FROM risk_alerts
        ) sub
        WHERE rn = 1;
    """

    try:
        with engine.connect() as connection:
            result = connection.execute(text(query))
            db_records = {row.county: row for row in result}

        status_board = []
        for county in covered_counties:
            has_record = county in db_records
            record = db_records[county] if has_record else None
            risk_level = record.risk_level if has_record else "Low"

            if risk_level == "High" or county in ["Makueni", "Machakos", "Isiolo"]:
                live_calamity = "Flash Floods, Severe Landslides & Waterborne Outbreaks"
            else:
                live_calamity = "Severe Multi-Season Drought & Agricultural Deficits"

            county_profile = get_county_advisory(county, live_calamity)
            calamity_label = county_profile.get("primary_calamity", live_calamity)

            if has_record:
                raw_score = float(record.calculated_score)
                formatted_score = f"{round(raw_score * 100, 1)}%" if raw_score <= 1.0 else f"{raw_score}%"

                status_board.append({
                    "id": record.id,
                    "timestamp": record.timestamp,
                    "county": record.county,
                    "calamity_type": calamity_label,
                    "composite_rating": formatted_score,
                    "risk_level": risk_level,
                    "notified": record.notified
                })
            else:
                status_board.append({
                    "id": 0,
                    "timestamp": "No Data Logged Yet",
                    "county": county,
                    "calamity_type": calamity_label,
                    "composite_rating": "0.0%",
                    "risk_level": "Low",
                    "notified": False
                })

        return jsonify(status_board), 200

    except Exception as e:
        return jsonify({"status": "error", "message": "We could not load the county status updates right now. Please try again later."}), 500


@app.route('/telemetry')
def public_telemetry():
    """
    Public telemetry route: queries PostgreSQL and analytics models
    to populate dynamic climate & health hazards and county cards.
    """
    summary = _build_status_board()

    return render_template(
        'telemetry.html',
        status_board=summary["status_board"],
        avg_climate=summary["avg_climate"],
        avg_health=summary["avg_health"],
        high_risk_count=summary["high_risk_count"],
        last_sync=_eat_str()
    )


@app.route('/api/v1/admin/risk-trend', methods=['GET'])
@admin_required
@limiter.limit("20 per minute")
def admin_risk_trend():
    """
    Returns per-county 7-day risk trajectory series for the admin overview
    line graph. Admin-only endpoint.

    The current score comes from the latest persisted risk record when one is
    available (cheap); only counties with no record yet are live-recomputed.
    The 7-day forecast is derived from the analytics engine per county.
    """
    try:
        labels = []
        now = _eat_now()
        for i in range(7):
            d = now + timedelta(days=i)
            labels.append(d.strftime("%b %d"))

        # Latest persisted record per county (used as the 'current' anchor).
        engine = get_db_engine()
        latest_query = """
            SELECT id, county, calculated_score, risk_level
            FROM (
                SELECT id, county, calculated_score, risk_level,
                       ROW_NUMBER() OVER (PARTITION BY county ORDER BY timestamp DESC) as rn
                FROM risk_alerts
            ) sub
            WHERE rn = 1;
        """
        try:
            with engine.connect() as connection:
                result = connection.execute(text(latest_query))
                records = {row.county: row for row in result}
        except Exception as e:
            print(f"Risk trend records warning: {e}")
            records = {}

        series = []
        for county in COVERED_COUNTIES:
            try:
                record = records.get(county)
                if record:
                    raw = float(record.calculated_score)
                    current = round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)
                else:
                    # No persisted record yet -> live recompute (also persists).
                    live = analytics_engine.calculate_composite_risk(county)
                    current = round(float(live.get("composite_risk_score", 0.3)) * 100, 1)

                forecast = analytics_engine.forecast_risk(county=county, horizon=7)
                scores = forecast.get("scores", [])
                trend = forecast.get("trend", "stable")
            except Exception as e:
                print(f"Risk trend error ({county}): {e}")
                current = 0.0
                scores = []
                trend = "stable"

            # Build a 7-point trajectory: current value followed by forecast.
            points = [current]
            if scores:
                points.extend([round(s * 100, 1) for s in scores])
            # Pad/truncate to exactly 7 points.
            if len(points) < 7:
                points.extend([points[-1]] * (7 - len(points)))
            points = points[:7]

            series.append({
                "county": county,
                "trend": trend,
                "points": points,
                "current": current,
            })

        # The recompute path may have inserted fresh rows for missing counties.
        prune_risk_alerts(engine, keep_days=30)

        return jsonify({
            "status": "ok",
            "labels": labels,
            "series": series,
            "last_sync": _eat_str(),
        }), 200

    except Exception as e:
        print(f"Risk trend endpoint error: {e}")
        return jsonify({
            "status": "error",
            "message": "Could not load risk trend data right now."
        }), 500


@app.route('/api/v1/telemetry/refresh', methods=['GET'])
@limiter.limit("10 per minute")
def telemetry_refresh():
    """
    Returns the latest processed risk status for all covered counties.

    Public endpoint (no login required) so the telemetry page Refresh button
    can pull current data for every county. This is deliberately LIGHTWEIGHT:
    it reuses the latest persisted risk records and only live-computes (and
    persists) counties with no record yet. The expensive forced full recompute
    is reserved for the admin-only /api/v1/admin/telemetry/refresh endpoint.
    """
    try:
        summary = _build_status_board(force_recompute=False)
        return jsonify({
            "status": "ok",
            "status_board": summary["status_board"],
            "avg_climate": summary["avg_climate"],
            "avg_health": summary["avg_health"],
            "high_risk_count": summary["high_risk_count"],
            "last_sync": _eat_str(),
        }), 200

    except Exception as e:
        print(f"Telemetry refresh error: {e}")
        return jsonify({
            "status": "error",
            "message": "We could not refresh the live telemetry data right now. Please try again."
        }), 500


@app.route('/api/v1/admin/telemetry/refresh', methods=['GET'])
@admin_required
@limiter.limit("30 per minute")
def admin_telemetry_refresh():
    """
    Force-recomputes the live composite risk for every covered county and
    returns fresh JSON. Admin-only endpoint used by the System Analytics
    page's Refresh button so expensive ML recomputation cannot be triggered
    by anonymous visitors. Old risk_alerts rows are pruned after each run.
    """
    try:
        summary = _build_status_board(force_recompute=True)
        return jsonify({
            "status": "ok",
            "status_board": summary["status_board"],
            "avg_climate": summary["avg_climate"],
            "avg_health": summary["avg_health"],
            "high_risk_count": summary["high_risk_count"],
            "last_sync": _eat_str(),
        }), 200

    except Exception as e:
        print(f"Admin telemetry refresh error: {e}")
        return jsonify({
            "status": "error",
            "message": "We could not refresh the live telemetry data right now. Please try again."
        }), 500


# =====================================================================
# CITIZEN REGISTRATION & AUTHENTICATION
# =====================================================================

@app.route('/register', methods=['GET'])
def register_page():
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/register', methods=['POST'])
@limiter.limit("10 per hour")
def handle_registration():
    engine = get_db_engine()

    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone_number = request.form.get('phone_number', '').strip()
    password = request.form.get('password', '')
    receive_email = 'receive_email' in request.form
    receive_sms = 'receive_sms' in request.form

    if not full_name or not email or not phone_number or not password:
        audit("register", actor=email, outcome="invalid", details={"reason": "missing_fields"})
        flash("Please fill in all the required fields to create your alert profile.", "error")
        return redirect(url_for('register_page'))

    try:
        with engine.connect() as connection:
            existing_user = connection.execute(text("""
                SELECT id FROM users WHERE email = :email OR phone_number = :phone;
            """), {"email": email, "phone": phone_number}).fetchone()

        if existing_user:
            flash("An account with this email or phone number already exists. Please sign in instead.", "warning")
            return redirect(url_for('register_page'))

        hashed_password = generate_password_hash(password)

        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO users (full_name, email, phone_number, password_hash,
                                   receive_email, is_subscribed, subscribe_sms, subscribe_email,
                                   dispatch_preference)
                VALUES (:name, :email, :phone, :hash,
                        :email_opt, :global_sub, :sms_opt, :email_opt,
                        'sms');
            """), {
                "name": full_name,
                "email": email,
                "phone": phone_number,
                "hash": hashed_password,
                "email_opt": receive_email,
                "sms_opt": receive_sms,
                "global_sub": receive_sms or receive_email
            })

        session['user_email'] = email
        session['user_name'] = full_name
        session['user_role'] = 'citizen'

        flash("Registration successful! Welcome to AthGad AI.", "success")
        return redirect(url_for('dashboard'))

    except Exception as e:
        print(f"Database error encountered during registration: {e}")
        flash("Something went wrong on our side. Please try again.", "error")
        return redirect(url_for('register_page'))


@app.route('/login', methods=['GET'])
def login_page():
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/login', methods=['POST'])
@limiter.limit("10 per minute")
def handle_login():
    engine = get_db_engine()

    email = request.form.get('email', '').strip().lower()
    password_input = request.form.get('password', '')

    if not email or not password_input:
        audit("login", actor=email, outcome="invalid", details={"reason": "missing_fields"})
        flash("Please provide both your email address and password.", "error")
        return redirect(url_for('login_page'))

    try:
        with engine.connect() as connection:
            user = connection.execute(text("""
                SELECT full_name, email, password_hash, role
                FROM users
                WHERE email = :email;
            """), {"email": email}).fetchone()

        if user and check_password_hash(user.password_hash, password_input):
            role = getattr(user, 'role', None) or 'citizen'
            # Prevent session fixation: build a fresh session on login.
            session.clear()
            session['user_email'] = user.email
            session['user_name'] = user.full_name
            session['user_role'] = role
            audit("login", actor=email, outcome="success", details={"role": role})
            flash(f"Welcome back, {user.full_name}! Your risk dashboard is ready.", "success")
            if role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            audit("login", actor=email, outcome="failed", details={"reason": "bad_credentials"})
            flash("Incorrect email or password. Please try again.", "error")
            return redirect(url_for('login_page'))

    except Exception as e:
        print(f"Database error encountered during user authentication: {e}")
        audit("login", actor=email, outcome="error", details={"reason": str(e)})
        flash("Something went wrong on our side. Please try again.", "error")
        return redirect(url_for('login_page'))


@app.route('/logout', methods=['GET'])
def handle_logout():
    """Wipes active session state tokens, signing out the user safely."""
    session.clear()
    flash("You have been signed out safely.", "info")
    return redirect(url_for('login_page'))


# =====================================================================
# SUBSCRIPTION & PAYMENT MANAGEMENT PIPELINE
# =====================================================================

@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe_portal():
    """
    Handles configuring user alert profiles.
    Payment is NOT charged immediately — 30-day free trial starts on first subscribe.
    After trial expires, user must pay 150 KES via M-PESA to continue.
    If user unsubscribes mid-trial and resubscribes, remaining trial days carry over.
    """
    if 'user_email' not in session:
        return redirect(url_for('login_page'))

    email = session['user_email']
    engine = get_db_engine()

    if request.method == 'POST':
        selected_mediums = request.form.getlist('dispatch_medium')

        sms_opted_in = 'sms' in selected_mediums
        email_opted_in = 'email' in selected_mediums
        global_active = (sms_opted_in or email_opted_in)

        try:
            with engine.connect() as connection:
                user = connection.execute(
                    text("SELECT phone_number, payment_status, trial_started_at, trial_ends_at, "
                         "unsubscribed_at, subscribe_sms, subscribe_email, full_name FROM users WHERE email = :email"),
                    {"email": email}
                ).fetchone()

            if not user:
                audit("subscribe", actor=email, outcome="failed", details={"reason": "invalid_session"})
                flash("User session invalid.", "error")
                return redirect(url_for('login_page'))

            now = datetime.now()
            phone_number = user.phone_number
            payment_status = user.payment_status

            # ── M-PESA STK Push: If trial has expired, initiate payment ──
            if payment_status == 'expired' or (user.trial_ends_at and user.trial_ends_at < now and payment_status != 'active'):
                # Initiate STK push for 150 KES
                try:
                    stk_result = initiate_stk_push(
                        phone_number=phone_number,
                        amount=150,
                        account_reference=email[:12]
                    )
                    checkout_id = stk_result.get("CheckoutRequestID", "")
                    if checkout_id:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                UPDATE users
                                SET mpesa_checkout_id = :cid,
                                    subscribe_sms = :sms,
                                    subscribe_email = :email_sub,
                                    is_subscribed = :global_sub
                                WHERE email = :email;
                            """), {
                                "cid": checkout_id,
                                "sms": sms_opted_in,
                                "email_sub": email_opted_in,
                                "global_sub": global_active,
                                "email": email,
                            })
                        flash("M-PESA STK Push sent! Please check your phone and enter your PIN to complete payment.", "info")
                        return redirect(url_for('subscribe_portal'))
                    else:
                        error_msg = stk_result.get("errorMessage", stk_result.get("error", "Unknown error"))
                        print(f"M-PESA STK Push failed: {error_msg}")
                        flash(f"Could not initiate M-PESA payment: {error_msg}. Please try again.", "error")
                        return redirect(url_for('subscribe_portal'))
                except Exception as mpesa_err:
                    print(f"M-PESA STK Push exception: {mpesa_err}")
                    flash("Could not initiate M-PESA payment. Please try again later.", "error")
                    return redirect(url_for('subscribe_portal'))

            # ── Normal trial/subscription flow (no payment required) ──
            # Determine trial dates (carry over remaining days)
            trial_started_at = user.trial_started_at
            trial_ends_at = user.trial_ends_at

            # Default: start a new trial
            new_trial_start = now
            new_trial_end = now + timedelta(days=TRIAL_DAYS)

            if trial_started_at and trial_ends_at:
                # Previously had a trial — check remaining days
                if isinstance(trial_ends_at, str):
                    trial_ends_at = datetime.fromisoformat(trial_ends_at)
                if isinstance(trial_started_at, str):
                    trial_started_at = datetime.fromisoformat(trial_started_at)

                if trial_ends_at > now:
                    # Trial still active — carry over remaining days
                    remaining = (trial_ends_at - now).days
                    if remaining > 0:
                        new_trial_end = now + timedelta(days=remaining)
                        new_trial_start = trial_started_at  # preserve original start
                    else:
                        new_trial_start = now
                        new_trial_end = now + timedelta(days=TRIAL_DAYS)
                else:
                    # Trial expired — start fresh if payment_status is not 'active'
                    if payment_status != 'active':
                        new_trial_start = now
                        new_trial_end = now + timedelta(days=TRIAL_DAYS)

            with engine.begin() as conn:
                # Track the subscription start date (only set when first subscribing)
                conn.execute(text("""
                    UPDATE users
                    SET is_subscribed = :global_sub,
                        subscribe_sms = :sms,
                        subscribe_email = :email_sub,
                        trial_started_at = :trial_start,
                        trial_ends_at = :trial_end,
                        unsubscribed_at = NULL,
                        subscription_started_at = CASE
                            WHEN subscription_started_at IS NULL THEN :now_ts
                            ELSE subscription_started_at
                        END
                    WHERE email = :email;
                """), {
                    "global_sub": global_active,
                    "sms": sms_opted_in,
                    "email_sub": email_opted_in,
                    "trial_start": new_trial_start,
                    "trial_end": new_trial_end,
                    "now_ts": datetime.now(),
                    "email": email
                })

            # Send trial welcome notification
            if global_active:
                alert_service.engine = engine
                try:
                    with engine.connect() as conn:
                        user_row = conn.execute(
                            text("SELECT full_name, phone_number, email, subscribe_sms, subscribe_email FROM users WHERE email = :e"),
                            {"e": email}
                        ).fetchone()
                    if user_row:
                        opt_sms = user_row.subscribe_sms
                        opt_email = user_row.subscribe_email
                        alert_service.send_trial_notice(
                            name=user_row.full_name,
                            phone_number=user_row.phone_number,
                            email=user_row.email,
                            trial_end_date=new_trial_end.strftime("%Y-%m-%d"),
                            opt_sms=opt_sms,
                            opt_email=opt_email
                        )
                except Exception as notify_err:
                    print(f"Trial notice send warning: {notify_err}")

            flash("Your alert preferences are saved. Your free trial has started!", "success")
            return redirect(url_for('dashboard'))

        except Exception as e:
            print(f"Subscription pipeline writing error: {e}")
            flash("We could not update your alert preferences. Please try again.", "error")
            return redirect(url_for('subscribe_portal'))

    # GET Logic: Extract status rows including trial info
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT is_subscribed, subscribe_sms, subscribe_email, payment_status, "
                     "trial_started_at, trial_ends_at, unsubscribed_at FROM users WHERE email = :e"),
                {"e": email}
            ).fetchone()
    except Exception as e:
        print(f"Routing Warning: Could not fetch initial state: {e}")
        row = None

    return render_template('subscribe.html', status=row)


@app.route('/api/v1/mpesa/callback', methods=['POST'])
@csrf.exempt
@limiter.limit("20 per minute")
def mpesa_callback():
    """
    Webhook endpoint triggered asynchronously by Safaricom Daraja API when user enters PIN.
    On success: set payment_status='active', send thank-you, clear trial.
    On failure: set payment_status='failed'.
    """
    data = request.get_json()
    print(f"[M-PESA CALLBACK] {data}")

    try:
        stk_callback = data.get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        checkout_id = stk_callback.get("CheckoutRequestID")

        engine = get_db_engine()

        if result_code == 0:
            # Payment SUCCESSFUL
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE users
                    SET payment_status = 'active',
                        is_subscribed = True,
                        trial_ends_at = NULL
                    WHERE mpesa_checkout_id = :cid;
                """), {"cid": checkout_id})
            audit("mpesa_callback", target=checkout_id or "", outcome="success")
            print(f"[PAYMENT SUCCESS] Account marked active for CheckoutID: {checkout_id}")

            # Send thank-you notification
            alert_service.engine = engine
            try:
                with engine.connect() as conn:
                    user_row = conn.execute(
                        text("SELECT full_name, phone_number, email, subscribe_sms, subscribe_email FROM users WHERE mpesa_checkout_id = :cid"),
                        {"cid": checkout_id}
                    ).fetchone()
                if user_row:
                    alert_service.send_payment_thank_you(
                        name=user_row.full_name,
                        phone_number=user_row.phone_number,
                        email=user_row.email,
                        opt_sms=user_row.subscribe_sms,
                        opt_email=user_row.subscribe_email
                    )
            except Exception as notify_err:
                print(f"Thank-you notification warning: {notify_err}")

            return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200
        else:
            # Payment cancelled or failed
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE users
                    SET payment_status = 'failed',
                        is_subscribed = False
                    WHERE mpesa_checkout_id = :cid;
                """), {"cid": checkout_id})
            print(f"[PAYMENT FAILED/CANCELLED] CheckoutID: {checkout_id}")
            return jsonify({"ResultCode": 0, "ResultDesc": "Acknowledged"}), 200

    except Exception as e:
        print(f"M-PESA Callback Execution Error: {e}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Error"}), 500


# =====================================================================
# UNSUBSCRIBE PIPELINES
# =====================================================================

@app.route('/unsubscribe', methods=['GET', 'POST'])
def web_unsubscribe():
    """
    Click-to-unsubscribe for email recipients.

    GET request (no mutation): shows a confirmation form so the user can
    confirm they want to disable email alerts without accidentally being
    unsubscribed by a plain link pre-fetch / crawler. The actual state
    change only happens on the POST handler below (which is CSRF-protected).

    POST request: performs the opt-out.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email and 'user_email' in session:
            email = session['user_email']
        if not email:
            flash("We could not determine your account. Please sign in.", "warning")
            return redirect(url_for('login_page'))

        return _perform_unsubscribe(email)

    # GET: resolve the target email and render a confirmation form.
    email = request.args.get('email')
    if not email:
        if 'user_email' in session:
            email = session['user_email']
        else:
            flash("Please sign in to change your alert preferences.", "warning")
            return redirect(url_for('login_page'))

    # Show a confirmation page (no mutation on GET).
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <body style="font-family: sans-serif; text-align: center; background-color: #020617; color: #f8fafc; padding-top: 80px; margin: 0;">
        <div style="max-width: 450px; margin: 0 auto; background-color: #0f172a; padding: 40px; border: 1px solid #1e293b; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
            <h2 style="color: #ef4444; margin-top: 0;">Unsubscribe from Email Alerts</h2>
            <p style="color: #94a3b8; line-height: 1.6; font-size: 14px;">
                You are about to stop receiving premium alert emails for
                <strong style="color: #38bdf8;">{{ email }}</strong>.
            </p>
            <p style="color: #eab308; line-height: 1.6; font-size: 13px;">
                Note: Important safety warnings for your county will still be sent when needed.
            </p>
            <form method="POST" action="/unsubscribe" style="margin-top: 24px;">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
                <input type="hidden" name="email" value="{{ email }}" />
                <button type="submit"
                        style="background-color: #ef4444; color: #ffffff; border: none; font-weight: bold;
                               padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 14px;">
                    Confirm Unsubscribe
                </button>
            </form>
            <p style="color: #64748b; font-size: 12px; margin-top: 20px;">
                <a href="/subscribe" style="color: #10b981;">Cancel — Keep my email alerts</a>
            </p>
        </div>
    </body>
    </html>
    """, email=email), 200


def _perform_unsubscribe(email: str):
    """
    Shared helper that mutates the user's email channel to disabled.
    Used by the POST handler of /unsubscribe (CSRF-protected).
    """
    engine = get_db_engine()

    try:
        with engine.begin() as connection:
            # Fetch user name before updating
            user = connection.execute(
                text("SELECT full_name, subscribe_sms, subscribe_email FROM users WHERE email = :email"),
                {"email": email}
            ).fetchone()

            if not user:
                flash("No account registered under that email address.", "error")
                return redirect(url_for('dashboard'))

            result = connection.execute(text("""
                UPDATE users
                SET subscribe_email = False,
                    is_subscribed = subscribe_sms,
                    unsubscribed_at = NOW()
                WHERE email = :email;
"""), {"email": email})

            rows_affected = result.rowcount

        if rows_affected > 0:
            print(f"[EMAIL OPT-OUT] Email channel disabled for: {email}")
            audit("unsubscribe", actor=email, outcome="success", details={"channel": "email"})

            # Send polite confirmation with reason request
            alert_service.engine = engine
            alert_service.send_unsubscribe_confirmation(
                name=user.full_name,
                phone_number="",
                email=email,
                channel="email",
                opt_sms=False,
                opt_email=True
            )

            return render_template_string("""
            <!DOCTYPE html>
            <html>
            <body style="font-family: sans-serif; text-align: center; background-color: #020617; color: #f8fafc; padding-top: 80px; margin: 0;">
                <div style="max-width: 450px; margin: 0 auto; background-color: #0f172a; padding: 40px; border: 1px solid #1e293b; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
                    <h2 style="color: #ef4444; margin-top: 0;">You Have Unsubscribed</h2>
                    <p style="color: #94a3b8; line-height: 1.6; font-size: 14px;">You will no longer receive premium alert emails for <strong>{{ email }}</strong>.</p>
                    <p style="color: #eab308; line-height: 1.6; font-size: 13px;">Note: Important safety warnings for your county will still be sent when needed.</p>
                    <p style="color: #94a3b8; font-size: 12px; margin-top: 20px;">If you're willing, please tell us why:<br>
                    <a href="http://127.0.0.1:5000/unsubscribe/reason?email={{ email }}" style="color: #38bdf8;">Share Your Feedback</a></p>
                </div>
            </body>
            </html>
            """, email=email), 200
        else:
            flash("No account registered under that email address.", "error")
            return redirect(url_for('dashboard'))

    except Exception as e:
        print(f"Web portal processing error for unsubscribe vector: {e}")
        flash("An error occurred processing your request. Please try again.", "error")
        return redirect(url_for('dashboard'))


@app.route('/unsubscribe/reason', methods=['GET', 'POST'])
def unsubscribe_reason():
    """
    Allows users to optionally provide a reason for unsubscribing.
    On GET: show a form with suggested answers and free-text field.
    On POST: store the reason in the unsubscriptions table.
    """
    engine = get_db_engine()
    email = request.args.get('email') or session.get('user_email')

    if not email:
        flash("We need your email address to continue.", "warning")
        return redirect(url_for('login_page'))

    if request.method == 'POST':
        suggested_answer = request.form.get('suggested_answer', '').strip()
        free_text = request.form.get('free_text', '').strip()

        # Combine: prefer free_text if provided, else suggested_answer
        # Truncate to fit unsubscriptions.reason VARCHAR(255) to prevent overflow errors
        reason = (free_text if free_text else suggested_answer)[:255]

        try:
            with engine.begin() as connection:
                # Fetch the user's full name to build a readable primary key
                user_row = connection.execute(
                    text("SELECT full_name FROM users WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                if not user_row:
                    flash("No account registered under that email address.", "error")
                    return redirect(url_for('login_page'))

                unsub_ref = _generate_unsub_ref(connection, user_row.full_name)

                connection.execute(text("""
                    INSERT INTO unsubscriptions (unsub_ref, channel, reason, email)
                    VALUES (:unsub_ref, 'email', :reason, :email)
                """), {
                    "unsub_ref": unsub_ref,
                    "reason": reason,
                    "email": email,
                })
            print(f"[UNSUBSCRIBE REASON] Recorded for {email}: {reason}")
            flash("Thank you for your feedback! We appreciate your input.", "success")
            return redirect(url_for('dashboard'))
        except Exception as e:
            print(f"Unsubscribe reason recording error: {e}")
            flash("Could not save your feedback. Please try again.", "error")
            return redirect(url_for('unsubscribe_reason', email=email))

    return render_template('unsubscribe_reason.html', email=email)


@app.route('/api/v1/sms/callback', methods=['POST'])
@csrf.exempt
@limiter.limit("30 per minute")
def incoming_sms_callback():
    """
    Listens for webhook payloads from Africa's Talking SMS gateway.
    STOP: disables SMS channel, records unsubscribed_at, sends polite confirmation.
    Also handles reason replies (1, 2, 3, or free text).
    """
    from_number = request.form.get("from", "").strip()
    text_content = request.form.get("text", "").strip().upper()

    print(f"[WEBHOOK SIGNAL] Incoming SMS -> From: {from_number} Content: '{text_content}'")
    audit("sms_callback", target=from_number, outcome="received", details={"text": text_content})

    if not from_number:
        return jsonify({"status": "ignored", "reason": "No sender phone parameter found."}), 400

    engine = get_db_engine()

    # Normalize phone number
    norm_number = from_number
    if norm_number.startswith('+254'):
        norm_number = norm_number[4:]
    elif norm_number.startswith('254'):
        norm_number = norm_number[3:]
    elif norm_number.startswith('0'):
        norm_number = norm_number[1:]

    search_query = f"%{norm_number}"

    if text_content == "STOP":
        try:
            with engine.begin() as connection:
                user = connection.execute(text("""
                    SELECT full_name, subscribe_email, subscribe_sms
                    FROM users
                    WHERE phone_number LIKE :phone_pattern;
                """), {"phone_pattern": search_query}).fetchone()

                if not user:
                    print(f"[PIPELINE ALERT] STOP keyword matched but no user record found for {from_number}")
                    return jsonify({"status": "not_found", "message": "Phone vector does not exist in registry index."}), 200

                # Disable SMS channel, record unsubscribed_at
                result = connection.execute(text("""
                    UPDATE users
                    SET subscribe_sms = False,
                        is_subscribed = CASE WHEN subscribe_email = True THEN True ELSE False END,
                        unsubscribed_at = NOW()
                    WHERE phone_number LIKE :phone_pattern;
                """), {"phone_pattern": search_query})

                rows_affected = result.rowcount

            if rows_affected > 0:
                print(f"[PIPELINE SUCCESS] SMS channel disabled for {from_number}")

                # Send polite confirmation SMS with reason request
                alert_service.engine = engine
                alert_service.send_unsubscribe_confirmation(
                    name=user.full_name,
                    phone_number=from_number,
                    email="",
                    channel="sms",
                    opt_sms=True,
                    opt_email=False
                )

                return jsonify({"status": "success", "message": "SMS channel disabled successfully."}), 200

        except Exception as e:
            print(f"Webhook subscriber execution failed: {e}")
            return jsonify({"status": "database_error", "message": str(e)}), 500

    # Handle reason responses (1, 2, 3, or free text)
    reason_map = {
        "1": "Too many messages",
        "2": "Not useful",
        "3": "Too expensive"
    }
    if text_content in reason_map or len(text_content) > 1:
        suggested_answer = reason_map.get(text_content, "")
        reason_text = reason_map.get(text_content, text_content)
        # Only record if we have a matching user
        try:
            with engine.begin() as connection:
                user = connection.execute(
                    text("SELECT email FROM users WHERE phone_number LIKE :phone_pattern"),
                    {"phone_pattern": search_query}
                ).fetchone()
                if user:
                    user_row = connection.execute(
                        text("SELECT full_name FROM users WHERE email = :email"),
                        {"email": user.email}
                    ).fetchone()
                    unsub_ref = _generate_unsub_ref(connection, user_row.full_name)
                    connection.execute(text("""
                        INSERT INTO unsubscriptions (unsub_ref, channel, reason, email)
                        VALUES (:unsub_ref, 'sms', :reason, :email)
                    """), {
                        "unsub_ref": unsub_ref,
                        "reason": reason_text,
                        "email": user.email,
                    })
                    print(f"[UNSUBSCRIBE REASON] SMS reply from {from_number}: {reason_text}")
        except Exception as e:
            print(f"Reason recording error: {e}")

    return jsonify({"status": "received"}), 200


# =====================================================================
# TRIAL EXPIRY CHECKER (callable via cron or on demand)
# =====================================================================

@app.route('/api/v1/check-trial-expiry', methods=['GET'])
def check_trial_expiry():
    """
    Scans all users whose trial has ended and payment_status is not 'active'.
    Unsubscribes them (removes premium access, notifies).
    Called periodically (e.g., via cron or scheduler).
    """
    engine = get_db_engine()
    alert_service.engine = engine
    now = datetime.now()
    expired_count = 0

    try:
        with engine.connect() as connection:
            expired_users = connection.execute(text("""
                SELECT full_name, email, phone_number, subscribe_sms, subscribe_email
                FROM users
                WHERE trial_ends_at IS NOT NULL
                  AND trial_ends_at < NOW()
                  AND payment_status != 'active'
                  AND is_subscribed = True;
            """)).fetchall()

        for user in expired_users:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE users
                    SET is_subscribed = False,
                        subscribe_sms = False,
                        subscribe_email = False,
                        unsubscribed_at = NOW()
                    WHERE email = :email;
                """), {"email": user.email})

            expired_count += 1
            print(f"[TRIAL EXPIRED] {user.email}")

            # Send trial expired notice
            alert_service.send_trial_expired_notice(
                name=user.full_name,
                phone_number=user.phone_number,
                email=user.email,
                opt_sms=user.subscribe_sms,
                opt_email=user.subscribe_email
            )

        return jsonify({
            "status": "success",
            "expired_count": expired_count,
            "message": f"Checked trial expiry. {expired_count} user(s) expired."
        }), 200

    except Exception as e:
        print(f"Trial expiry check error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# ADMIN WORKSPACE
# =====================================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin workspace landing page with system overview cards."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "overview"})
    engine = get_db_engine()
    analytics = get_analytics(engine)
    return render_template(
        'admin/dashboard.html',
        analytics=analytics,
        admin_name=session.get('user_name', 'Admin'),
    )


@app.route('/admin/reports')
@admin_required
def admin_reports():
    """Admin report generation hub — lists all available PDF reports."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "reports"})
    return render_template('admin/reports.html')


@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    """Admin system analytics dashboard with charts and risk analysis."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "analytics"})
    engine = get_db_engine()
    analytics = get_analytics(engine)
    return render_template(
        'admin/analytics.html',
        analytics=analytics,
        admin_name=session.get('user_name', 'Admin'),
    )


@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin user management page — list members and their roles."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "users"})
    engine = get_db_engine()
    users = []
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, full_name, email, role, is_subscribed, payment_status,
                       COALESCE(subscription_started_at, registered_at) AS sub_at
                FROM users
                ORDER BY id;
            """)).fetchall()
        for row in rows:
            users.append({
                "id": row.id,
                "full_name": row.full_name,
                "email": row.email,
                "role": row.role,
                "is_subscribed": bool(row.is_subscribed),
                "payment_status": row.payment_status,
                "subscribed_at": row.sub_at.strftime("%Y-%m-%d %H:%M") if row.sub_at else "—",
            })
    except Exception as e:
        print(f"Admin users load error: {e}")
        flash("Could not load the user list.", "error")

    return render_template(
        'admin/users.html',
        users=users,
        admin_name=session.get('user_name', 'Admin'),
    )


@app.route('/admin/users/<int:user_id>/promote', methods=['POST'])
@admin_required
def admin_promote_user(user_id):
    """Promotes a citizen account to the admin role. Audited."""
    engine = get_db_engine()
    email = session.get('user_email')
    try:
        with engine.begin() as connection:
            target = connection.execute(
                text("SELECT email, role FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).fetchone()
            if not target:
                flash("User not found.", "error")
                return redirect(url_for('admin_users'))
            if target.email == email:
                flash("You cannot change your own role.", "error")
                return redirect(url_for('admin_users'))
            if target.role == 'admin':
                flash(f"{target.email} is already an admin.", "info")
                return redirect(url_for('admin_users'))
            connection.execute(
                text("UPDATE users SET role = 'admin' WHERE id = :uid"),
                {"uid": user_id},
            )
        audit("admin_role_change", actor=email,
              target=target.email, outcome="promote")
        flash(f"{target.email} promoted to Admin.", "success")
    except Exception as e:
        print(f"Admin promote error: {e}")
        flash("Could not update the user role.", "error")
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/demote', methods=['POST'])
@admin_required
def admin_demote_user(user_id):
    """Demotes an admin back to the citizen role. Audited."""
    engine = get_db_engine()
    email = session.get('user_email')
    try:
        with engine.begin() as connection:
            target = connection.execute(
                text("SELECT email, role FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).fetchone()
            if not target:
                flash("User not found.", "error")
                return redirect(url_for('admin_users'))
            if target.email == email:
                flash("You cannot change your own role.", "error")
                return redirect(url_for('admin_users'))
            if target.role != 'admin':
                flash(f"{target.email} is not an admin.", "info")
                return redirect(url_for('admin_users'))
            connection.execute(
                text("UPDATE users SET role = 'citizen' WHERE id = :uid"),
                {"uid": user_id},
            )
        audit("admin_role_change", actor=email,
              target=target.email, outcome="demote")
        flash(f"{target.email} demoted to Citizen.", "success")
    except Exception as e:
        print(f"Admin demote error: {e}")
        flash("Could not update the user role.", "error")
    return redirect(url_for('admin_users'))


@app.route('/admin/reports/<report_type>/pdf')
@admin_required
@limiter.limit("30 per minute")
def admin_report_pdf(report_type):
    """
    Generates and streams a PDF report for the given report type.
    Only admins may access this endpoint (enforced by the decorator),
    and every download is recorded in the audit log. Rate-limited to
    prevent unbounded report generation.
    """
    allowed = {
        "predicted_calamities",
        "disease_outbreaks",
        "subscribed_members",
        "unsubscribed_members",
    }
    if report_type not in allowed:
        audit("admin_report", actor=session.get('user_email'),
              outcome="invalid", details={"report": report_type})
        flash("Unknown report type requested.", "error")
        return redirect(url_for('admin_reports'))

    try:
        engine = get_db_engine()
        pdf_bytes = build_admin_report(engine, report_type)

        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="generated")

        filenames = {
            "predicted_calamities": "predicted_calamities_report.pdf",
            "disease_outbreaks": "disease_outbreaks_report.pdf",
            "subscribed_members": "subscribed_members_report.pdf",
            "unsubscribed_members": "unsubscribed_members_report.pdf",
        }
        response = app.response_class(
            pdf_bytes,
            mimetype='application/pdf',
        )
        response.headers['Content-Disposition'] = (
            f'attachment; filename={filenames[report_type]}'
        )
        return response

    except ImportError:
        flash("PDF generation library (reportlab) is not installed. "
              "Run: pip install reportlab", "error")
        return redirect(url_for('admin_reports'))
    except Exception as e:
        print(f"PDF generation error: {e}")
        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="error", details={"error": str(e)})
        flash("Could not generate the PDF report. Please try again.", "error")
        return redirect(url_for('admin_reports'))


if __name__ == '__main__':
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting AthGad AI REST Gateway Server on {host}:{port} "
          f"(debug={debug})...")
    app.run(host=host, port=port, debug=debug)
