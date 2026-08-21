import os
import re
import sys
import ipaddress
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, exc as sa_exc
from sqlalchemy.pool import QueuePool

# --- Standardized logging (console + rotating file in logs/) ---
from core.logging_setup import configure_logging, get_logger
configure_logging()
logger = get_logger(__name__)

# --- Security hardening imports ---
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Core security helpers
from core.security import (
    get_secret_key,
    apply_security_headers,
    audit,
    client_identifier,
)

# Centralized county risk-profile classification (single source of truth)
from core.county_registry import (
    get_county_advisory,
    COVERED_COUNTIES,
    threat_category_for,
    calamity_for,
)

# Core Modules & Pipeline Helpers
from core.analytics import AthGadAnalyticsEngine
from services.alert_service import AthGadAlertService
from services.mpesa_service import initiate_stk_push
from core.db_helper import get_db_engine  # Removed init_db_pool import
from core.id_codes import new_user_code
from core.time_utils import utc_now, parse_dt, EAT

# Admin workspace modules
from core.admin_reports import get_analytics, prune_risk_alerts, get_report_snapshot
from core.admin_reports import get_sms_delivery_logs, get_alert_dispatch_logs
from services.pdf_report_service import build_admin_report
from services.ingestion_runner import run_all_ingestion
from services.notification_queue import notification_queue

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# =====================================================================
# CONFIGURATION
# =====================================================================

class Config:
    """Application configuration with sensible defaults and environment override."""
    
    # Environment
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
    IS_PRODUCTION = ENVIRONMENT == "production"
    
    # Server
    FLASK_HOST = os.environ.get("FLASK_HOST", "127.0.0.1")
    FLASK_PORT = int(os.environ.get("FLASK_PORT", "5000"))
    FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    # Security
    SECRET_KEY = get_secret_key()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Strict")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    ENFORCE_HTTPS = os.environ.get("ENFORCE_HTTPS", "false").lower() == "true"
    if ENFORCE_HTTPS or SESSION_COOKIE_SECURE:
        SESSION_COOKIE_SECURE = True
    
    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
    DB_POOL_PRE_PING = os.environ.get("DB_POOL_PRE_PING", "true").lower() == "true"
    
    # CORS
    CORS_ALLOWED_ORIGINS = os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:5000,http://localhost:5000"
    )
    
    # M-PESA
    MPESA_CALLBACK_IP_ALLOWLIST = os.environ.get("MPESA_CALLBACK_IP_ALLOWLIST", "")
    MPESA_ENABLED = os.environ.get("MPESA_ENABLED", "true").lower() == "true"
    
    # Rate Limiting
    RATE_LIMIT_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")
    DEFAULT_RATE_LIMIT = os.environ.get("DEFAULT_RATE_LIMIT", "200 per hour")
    
    # Trial
    TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "30"))
    
    # Proxy
    PROXY_FORWARDED_COUNT = int(os.environ.get("PROXY_FORWARDED_COUNT", "1"))
    
    # Unsubscribe Token Expiry (in seconds, default 7 days)
    UNSUBSCRIBE_TOKEN_EXPIRY = int(os.environ.get("UNSUBSCRIBE_TOKEN_EXPIRY", 604800))
    
    # Static Asset Versioning
    STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    
    # Password Policy
    MIN_PASSWORD_LEN = int(os.environ.get("MIN_PASSWORD_LEN", "8"))
    REQUIRE_PASSWORD_COMPLEXITY = os.environ.get("REQUIRE_PASSWORD_COMPLEXITY", "true").lower() == "true"


# =====================================================================
# APPLICATION INITIALIZATION
# =====================================================================

app = Flask(__name__)

# --- Configuration ---
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
)

# --- Proxy Fix ---
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=Config.PROXY_FORWARDED_COUNT,
    x_proto=Config.PROXY_FORWARDED_COUNT,
    x_host=Config.PROXY_FORWARDED_COUNT,
    x_port=Config.PROXY_FORWARDED_COUNT,
)

# --- Database Connection Pool (configure if DATABASE_URL is set) ---
if Config.DATABASE_URL:
    try:
        # Configure pool settings on the engine
        from sqlalchemy import create_engine
        from core.db_helper import get_db_engine
        
        # Get the engine and configure pool
        engine = get_db_engine()
        # The pool is already configured in db_helper, but we can override settings
        # by recreating the engine with pool settings
        logger.info("Database connection pool configured.")
    except Exception as e:
        logger.critical("Failed to initialize database connection: %s", e)
        # Don't raise - allow app to start with warnings

# --- CORS ---
def _cors_origins():
    """Parse CORS allowed origins from environment."""
    return [o.strip() for o in Config.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

CORS(app, resources={r"/api/*": {"origins": _cors_origins()}})

# --- Security ---
csrf = CSRFProtect(app)

def _static_assets_exempt() -> bool:
    """Exempt static files from rate limiting."""
    try:
        return request.endpoint == "static"
    except Exception:
        return False

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[Config.DEFAULT_RATE_LIMIT],
    default_limits_exempt_when=_static_assets_exempt,
    storage_uri=Config.RATE_LIMIT_STORAGE_URI,
)

app.after_request(apply_security_headers)

# --- Asset Versioning ---
def _compute_asset_version():
    """Compute a hash of static assets for cache busting."""
    hasher = hashlib.sha256()
    if os.path.isdir(Config.STATIC_ROOT):
        try:
            for dirpath, _, files in os.walk(Config.STATIC_ROOT):
                for filename in sorted(files):
                    path = os.path.join(dirpath, filename)
                    hasher.update(filename.encode("utf-8"))
                    try:
                        hasher.update(str(os.path.getmtime(path)).encode("utf-8"))
                    except (OSError, FileNotFoundError):
                        pass
        except (PermissionError, OSError) as e:
            logger.warning("Could not walk static directory: %s", e)
    return hasher.hexdigest()[:10]

ASSET_VERSION = _compute_asset_version()

@app.context_processor
def _inject_asset_version():
    return {"asset_version": ASSET_VERSION}

# --- Services ---
analytics_engine = AthGadAnalyticsEngine()
alert_service = AthGadAlertService()

# --- Unsubscribe Token Serializer ---
unsubscribe_serializer = URLSafeTimedSerializer(Config.SECRET_KEY)


# =====================================================================
# UTILITY FUNCTIONS
# =====================================================================

def _eat_now():
    """Return current time in East Africa Time (UTC+3)."""
    if EAT is None:
        logger.warning("EAT timezone not available, using UTC")
        return datetime.now(timezone.utc)
    return datetime.now(EAT)


def _eat_str(fmt="%H:%M EAT"):
    """Format current time in East Africa Time."""
    return _eat_now().strftime(fmt)


def _normalize_phone_digits(phone: str) -> str:
    """Extract only digits from a phone number string."""
    return "".join(ch for ch in (phone or "") if ch.isdigit())


# Fixed phone number validation with comprehensive format support
_PHONE_NUMBER_RE = re.compile(r"^254[17]\d{8}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_phone(phone: str):
    """
    Validate a Kenyan mobile number and return canonical E.164 form (+2547XXXXXXXX).
    Returns None for invalid numbers.
    """
    if not phone:
        return None
    
    digits = _normalize_phone_digits(phone)
    if not digits:
        return None
    
    # Normalize to 254XXXXXXXX format
    if digits.startswith("254"):
        pass  # Already has country code
    elif digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9:  # 7XXXXXXXX
        digits = "254" + digits
    else:
        return None
    
    # Validate against pattern
    if not _PHONE_NUMBER_RE.match(digits):
        return None
    
    return "+" + digits


def _validate_password(password: str) -> tuple:
    """
    Validate password against security policy.
    Returns (is_valid, error_message).
    """
    if len(password) < Config.MIN_PASSWORD_LEN:
        return False, f"Password must be at least {Config.MIN_PASSWORD_LEN} characters long."
    
    if Config.REQUIRE_PASSWORD_COMPLEXITY:
        if not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter."
        if not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter."
        if not re.search(r'\d', password):
            return False, "Password must contain at least one number."
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False, "Password must contain at least one special character."
    
    return True, ""


def _validate_registration_input(full_name, email, phone_number, password, county=None):
    """Validate registration input. Returns (normalized_phone, error_message)."""
    if not full_name or not email or not phone_number or not password:
        return None, "Please fill in all required fields to create your alert profile."
    
    if not _EMAIL_RE.match(email):
        return None, "Please enter a valid email address."
    
    is_valid, error = _validate_password(password)
    if not is_valid:
        return None, error
    
    normalized_phone = _normalize_phone(phone_number)
    if not normalized_phone:
        return None, "Please enter a valid Kenyan mobile number (e.g. 0712 345 678 or +254712345678)."
    
    if county and county not in COVERED_COUNTIES:
        return None, "Please choose a valid county from the list."
    
    return normalized_phone, None


def _ensure_db_connection():
    """Ensure database connection is healthy."""
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("Database connection check failed: %s", e)
        return False


def _generate_unsub_token(email: str) -> str:
    """Generate a signed token for unsubscribe links."""
    return unsubscribe_serializer.dumps(email, salt='unsubscribe')


def _verify_unsub_token(token: str) -> tuple:
    """Verify and decode an unsubscribe token. Returns (is_valid, email)."""
    try:
        email = unsubscribe_serializer.loads(
            token,
            salt='unsubscribe',
            max_age=Config.UNSUBSCRIBE_TOKEN_EXPIRY
        )
        return True, email
    except SignatureExpired:
        return False, "The unsubscribe link has expired. Please request a new one."
    except BadSignature:
        return False, "Invalid unsubscribe link."
    except Exception as e:
        logger.error("Unsubscribe token verification error: %s", e)
        return False, "An error occurred. Please try again."


def _mpesa_callback_ip_allowed(remote_addr: str) -> bool:
    """
    Check if callback source IP is in the allowlist.
    In production, fail closed if allowlist is not configured.
    """
    raw = Config.MPESA_CALLBACK_IP_ALLOWLIST.strip()
    if not raw:
        if Config.IS_PRODUCTION:
            logger.critical("MPESA_CALLBACK_IP_ALLOWLIST not set in production!")
            return False
        logger.warning("MPESA_CALLBACK_IP_ALLOWLIST not set - accepting all IPs (development mode)")
        return True
    
    try:
        client = ipaddress.ip_address((remote_addr or "").split(",")[0].strip())
    except ValueError:
        logger.warning("Invalid remote address: %s", remote_addr)
        return False
    
    for cidr in raw.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if client in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            logger.warning("Invalid CIDR in MPESA_CALLBACK_IP_ALLOWLIST: %r", cidr)
    
    logger.warning("M-PESA callback from IP %s not in allowlist", remote_addr)
    return False


def _compute_live_county_risk(county):
    """
    Run analytics engine for a single county.
    Returns (score_pct, risk_level, forecast_trend).
    """
    try:
        live_data = analytics_engine.calculate_composite_risk(county)
        # FIX: Queue the alert broadcast on every risk recompute path.
        # The dispatch dedup gate (6-hour cooldown) inside dispatch_critical_notification
        # suppresses duplicates so this is safe to call from telemetry refresh,
        # landing page, and admin analytics alike.
        _dispatch_for_risk_data(county, live_data)
        raw_score = float(live_data.get("composite_risk_score", 0.3))
        score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
        risk_level = live_data.get("risk_level", "Low")
        forecast_trend = live_data.get("forecast", {}).get("trend", "stable")
        return score_pct, risk_level, forecast_trend
    except Exception as e:
        logger.error("Live county recompute error (%s): %s", county, e)
        return None, "Offline", "stable"


def _build_status_board(force_recompute=False):
    """
    Build live composite-risk status board for all covered counties.
    
    Args:
        force_recompute: If True, recompute all counties through analytics engine.
    
    Returns:
        dict with status_board, avg_climate, avg_health, high_risk_count
    """
    engine = get_db_engine()
    
    query = """
        SELECT alert_code, timestamp::text, county, calculated_score, risk_level, notified
        FROM (
            SELECT alert_code, timestamp, county, calculated_score, risk_level, notified,
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
                score_pct, risk_level, forecast_trend = _compute_live_county_risk(county)
            
            threat_category, primary_threat = threat_category_for(county, risk_level)
            if threat_category == "health":
                health_scores.append(score_pct if score_pct is not None else 0.0)
            else:
                climate_scores.append(score_pct if score_pct is not None else 0.0)
            
            status_board.append({
                "county": county,
                "score_pct": score_pct if score_pct is not None else 0.0,
                "risk_level": risk_level,
                "threat_category": threat_category,
                "primary_threat": primary_threat,
                "trend": forecast_trend
            })
        
        if force_recompute:
            prune_risk_alerts(engine, keep_days=30)
        
    except Exception as e:
        logger.warning("Telemetry Data Load Warning: %s", e)
        for county in COVERED_COUNTIES:
            status_board.append({
                "county": county,
                "score_pct": 0.0,
                "risk_level": "Offline",
                "threat_category": "climate",
                "primary_threat": "Monitoring Sync Active",
                "trend": "stable"
            })
    
    valid_climate = [s for s in climate_scores if s is not None]
    valid_health = [s for s in health_scores if s is not None]
    
    avg_climate = round(sum(valid_climate) / len(valid_climate), 1) if valid_climate else 0.0
    avg_health = round(sum(valid_health) / len(valid_health), 1) if valid_health else 0.0
    high_risk_count = sum(1 for c in status_board if c['risk_level'] == 'High')
    
    return {
        "status_board": status_board,
        "avg_climate": avg_climate,
        "avg_health": avg_health,
        "high_risk_count": high_risk_count,
    }


# =====================================================================
# ADMIN AUTHORIZATION HELPER
# =====================================================================

def admin_required(view_func):
    """
    Decorator enforcing admin role with database re-validation.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        email = session.get('user_email')
        
        if not email:
            flash("Please sign in to access the admin workspace.", "warning")
            return redirect(url_for('login_page'))
        
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
            logger.warning("Admin role verification warning (%s): %s", email, e)
            role = session.get('user_role')
        
        if role != 'admin':
            audit("admin_access_denied", actor=email,
                  outcome="denied", details={"reason": "not_admin"})
            session.pop('user_role', None)
            flash("You are not authorized to access the admin workspace.", "error")
            return redirect(url_for('dashboard'))
        
        session['user_role'] = 'admin'
        return view_func(*args, **kwargs)
    return wrapper


# =====================================================================
# ROUTES: PUBLIC
# =====================================================================

@app.route('/favicon.ico')
def favicon():
    """Serve the AthGad AI favicon."""
    from flask import send_from_directory
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
        'favicon.svg',
        mimetype='image/svg+xml'
    )


@app.route('/')
def landing():
    """Serve the public landing page."""
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    
    summary = _build_status_board()
    return render_template(
        'landing.html',
        avg_climate=summary["avg_climate"],
        avg_health=summary["avg_health"],
        high_risk_count=summary["high_risk_count"],
        last_sync=_eat_str(),
    )


@app.route('/api/v1/health', methods=['GET'])
def get_system_health():
    """Return operational status including database connectivity."""
    db_healthy = _ensure_db_connection()
    status_code = 200 if db_healthy else 503
    
    return jsonify({
        "status": "online" if db_healthy else "degraded",
        "engine": "AthGad AI Engine v1.0",
        "region_scope": "Eastern Kenya (8 Counties)",
        "database": "connected" if db_healthy else "disconnected",
        "environment": Config.ENVIRONMENT,
        "timestamp": _eat_now().isoformat(),
    }), status_code


@app.route('/api/v1/live-summary', methods=['GET'])
def live_summary():
    """
    Return computed live risk signals for the landing page.
    Public endpoint, no auth required.
    """
    try:
        summary = _build_status_board(force_recompute=False)
        risk_map = {c["county"]: c for c in summary["status_board"]}
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
            "system_status": "Connected" if _ensure_db_connection() else "Degraded",
            "risk_map": risk_map,
            "last_sync": _eat_str(),
            "counties": summary["status_board"],
        }), 200
    
    except Exception as e:
        logger.error("Live summary error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load live risk signals right now. Please try again."
        }), 500


@app.route('/telemetry')
def public_telemetry():
    """Public telemetry page with county risk cards."""
    summary = _build_status_board(force_recompute=False)
    
    return render_template(
        'telemetry.html',
        status_board=summary["status_board"],
        avg_climate=summary["avg_climate"],
        avg_health=summary["avg_health"],
        high_risk_count=summary["high_risk_count"],
        last_sync=_eat_str()
    )


@app.route('/api/v1/telemetry/refresh', methods=['GET'])
@limiter.limit("10 per minute")
def telemetry_refresh():
    """
    Public refresh for telemetry page.
    Force-recomputes risk scores without full ingestion.
    """
    try:
        summary = _build_status_board(force_recompute=True)
        return jsonify({
            "status": "ok",
            "status_board": summary["status_board"],
            "avg_climate": summary["avg_climate"],
            "avg_health": summary["avg_health"],
            "high_risk_count": summary["high_risk_count"],
            "ingestion": {"skipped": "full external ingestion is admin-only; see /api/v1/admin/telemetry/refresh"},
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("Telemetry refresh error: %s", e)
        return jsonify({
            "status": "error",
            "message": "We could not refresh the live telemetry data right now. Please try again."
        }), 500


# =====================================================================
# ROUTES: AUTHENTICATION
# =====================================================================

@app.route('/dashboard')
def dashboard():
    """Serve the main dashboard."""
    if 'user_email' in session:
        return render_template('index.html')
    
    flash("Please sign in to view your risk dashboard.", "warning")
    return redirect(url_for('login_page'))


@app.route('/register', methods=['GET'])
def register_page():
    """Show registration form."""
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/register', methods=['POST'])
@limiter.limit("10 per hour")
def handle_registration():
    """Handle user registration."""
    engine = get_db_engine()
    
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone_number = request.form.get('phone_number', '').strip()
    password = request.form.get('password', '')
    receive_email = 'receive_email' in request.form
    receive_sms = 'receive_sms' in request.form
    
    normalized_phone, validation_error = _validate_registration_input(
        full_name, email, phone_number, password)
    
    if validation_error:
        audit("register", actor=email, outcome="invalid", details={"reason": validation_error})
        flash(validation_error, "error")
        return redirect(url_for('register_page'))
    
    try:
        with engine.connect() as connection:
            existing_user = connection.execute(text("""
                SELECT user_code FROM users WHERE email = :email OR phone_number = :phone;
            """), {"email": email, "phone": normalized_phone}).fetchone()
        
        if existing_user:
            flash("An account with this email or phone number already exists. Please sign in instead.", "warning")
            return redirect(url_for('register_page'))
        
        hashed_password = generate_password_hash(password)
        
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO users (user_code, full_name, email, phone_number, password_hash,
                                   receive_email, is_subscribed, subscribe_sms, subscribe_email,
                                   dispatch_preference)
                VALUES (:user_code, :name, :email, :phone, :hash,
                        :email_opt, :global_sub, :sms_opt, :email_opt,
                        'sms');
            """), {
                "user_code": new_user_code(),
                "name": full_name,
                "email": email,
                "phone": normalized_phone,
                "hash": hashed_password,
                "email_opt": receive_email,
                "sms_opt": receive_sms,
                "global_sub": receive_sms or receive_email
            })
        
        session.clear()
        session['user_email'] = email
        session['user_name'] = full_name
        session['user_role'] = 'citizen'
        
        flash("Registration successful! Welcome to AthGad AI. Activate your 30-day free premium trial for detailed risk reports via Alert Preferences.", "success")
        return redirect(url_for('dashboard'))
    
    except sa_exc.IntegrityError as e:
        logger.warning("Registration integrity error: %s", e)
        flash("An account with this email or phone number already exists.", "warning")
        return redirect(url_for('register_page'))
    except Exception as e:
        logger.error("Database error during registration: %s", e)
        flash("Something went wrong on our side. Please try again.", "error")
        return redirect(url_for('register_page'))


@app.route('/login', methods=['GET'])
def login_page():
    """Show login form."""
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/login', methods=['POST'])
@limiter.limit("10 per minute")
def handle_login():
    """Handle user login."""
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
        logger.error("Database error during authentication: %s", e)
        audit("login", actor=email, outcome="error", details={"reason": str(e)})
        flash("Something went wrong on our side. Please try again.", "error")
        return redirect(url_for('login_page'))


@app.route('/logout', methods=['GET'])
def handle_logout():
    """Log out user safely."""
    session.clear()
    flash("You have been signed out safely.", "info")
    return redirect(url_for('login_page'))


# =====================================================================
# ALERT DISPATCH HELPER
# =====================================================================

def _queue_alert_dispatch(county: str, risk_data: dict):
    """
    Queues a tiered SMS/email alert broadcast for a county+risk payload.
    Runs on the background notification worker thread. The dedup gate inside
    `dispatch_critical_notification` (6-hour cooldown) prevents repeat
    dispatches when many pages/endpoints compute the same county+level.
    """
    try:
        engine = get_db_engine()
        notification_queue.enqueue(
            alert_service.dispatch_critical_notification,
            county,
            dict(risk_data),
            engine,
        )
    except Exception as e:
        logger.warning("Alert dispatch queue warning (%s): %s", county, e)


def _dispatch_for_risk_data(county: str, risk_data: dict):
    """
    Enriches a risk payload with the county/calamity/advisory context and
    queues the alert dispatch. Called from every risk-computation path
    (risk-status, telemetry refresh, ingestion runner) so real Medium/High
    alerts are always broadcast — not only when a logged-in user happens to
    poll /api/v1/risk-status.
    """
    current_severity = risk_data.get("risk_level", "Medium")
    live_calamity = calamity_for(county, current_severity)
    risk_data["county"] = county
    risk_data["calamity_type"] = live_calamity
    risk_data["advisory"] = get_county_advisory(county, live_calamity)
    _queue_alert_dispatch(county, risk_data)
    return risk_data


# =====================================================================
# ROUTES: API (Protected)
# =====================================================================

@app.route('/api/v1/risk-status', methods=['GET'])
def get_realtime_risk_status():
    """
    Get real-time risk status for a specific county.
    Public endpoint; alert dispatch is queued for Medium/High risk regardless
    of whether the caller is authenticated (the dispatch dedup gate prevents
    duplicate broadcasts across repeated polls).
    """
    target_county = request.args.get('county', default='Kitui')
    
    try:
        risk_data = analytics_engine.calculate_composite_risk(target_county)
        risk_data = _dispatch_for_risk_data(target_county, risk_data)
        
        score_val = risk_data.get("composite_risk_score", 0.0)
        if isinstance(score_val, (int, float)) and score_val <= 1.0:
            risk_data["composite_risk_score"] = f"{round(score_val * 100, 1)}%"
        
        return jsonify(risk_data), 200
    
    except Exception as e:
        logger.error("Risk status error: %s", e)
        return jsonify({
            "status": "error",
            "message": "We could not update the risk information right now. Please try again in a moment."
        }), 500


@app.route('/api/v1/alerts/history', methods=['GET'])
def get_alert_history():
    """Get alert history for all covered counties."""
    engine = get_db_engine()
    covered_counties = COVERED_COUNTIES
    
    query = """
        SELECT alert_code, timestamp::text, county, calculated_score, risk_level, notified
        FROM (
            SELECT alert_code, timestamp, county, calculated_score, risk_level, notified,
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
            
            live_calamity = calamity_for(county, risk_level)
            county_profile = get_county_advisory(county, live_calamity)
            calamity_label = county_profile.get("primary_calamity", live_calamity)
            
            if has_record:
                raw_score = float(record.calculated_score)
                formatted_score = f"{round(raw_score * 100, 1)}%" if raw_score <= 1.0 else f"{raw_score}%"
                
                status_board.append({
                    "alert_code": record.alert_code,
                    "timestamp": record.timestamp,
                    "county": record.county,
                    "calamity_type": calamity_label,
                    "composite_rating": formatted_score,
                    "risk_level": risk_level,
                    "notified": record.notified
                })
            else:
                status_board.append({
                    "alert_code": "",
                    "timestamp": "No Data Logged Yet",
                    "county": county,
                    "calamity_type": calamity_label,
                    "composite_rating": "0.0%",
                    "risk_level": "Low",
                    "notified": False
                })
        
        return jsonify(status_board), 200
    
    except Exception as e:
        logger.error("Alert history error: %s", e)
        return jsonify({
            "status": "error",
            "message": "We could not load the county status updates right now. Please try again later."
        }), 500


@app.route('/api/v1/ingest/all', methods=['POST'])
@admin_required
@limiter.limit("10 per hour")
def ingest_all_data():
    """Admin-only endpoint for data ingestion."""
    try:
        results = run_all_ingestion()
        return jsonify({"status": "ok", "ingestion": results}), 200
    except Exception as e:
        logger.error("Ingestion endpoint error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Ingestion failed. See server logs for details."
        }), 500


# =====================================================================
# ROUTES: PROFILE
# =====================================================================

def _load_profile(email):
    """Load user profile from database."""
    engine = get_db_engine()
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT user_code, full_name, email, phone_number, county, role,
                   is_subscribed, payment_status, registered_at, trial_ends_at,
                   subscribe_sms, subscribe_email, dispatch_preference
            FROM users
            WHERE email = :email;
        """), {"email": email}).fetchone()
    
    if not row:
        return None
    
    return {
        "user_code": row.user_code,
        "full_name": row.full_name,
        "email": row.email,
        "phone_number": row.phone_number,
        "county": row.county,
        "role": row.role,
        "is_subscribed": bool(row.is_subscribed),
        "payment_status": row.payment_status,
        "registered_at": row.registered_at,
        "trial_ends_at": row.trial_ends_at,
        "subscribe_sms": bool(row.subscribe_sms),
        "subscribe_email": bool(row.subscribe_email),
        "dispatch_preference": row.dispatch_preference,
    }


@app.route('/profile', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def profile_page():
    """User profile management page."""
    if 'user_email' not in session:
        flash("Please sign in to manage your profile.", "warning")
        return redirect(url_for('login_page'))
    
    email = session['user_email']
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        phone_number = request.form.get('phone_number', '').strip()
        county = request.form.get('county', '').strip()
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not full_name or not phone_number:
            flash("Please provide both your full name and phone number.", "error")
            return redirect(url_for('profile_page'))
        
        normalized_phone = _normalize_phone(phone_number)
        if not normalized_phone:
            flash("Please enter a valid Kenyan mobile number (e.g. 0712 345 678 or +254712345678).", "error")
            return redirect(url_for('profile_page'))
        
        if county and county not in COVERED_COUNTIES:
            flash("Please choose a valid county from the list.", "error")
            return redirect(url_for('profile_page'))
        
        engine = get_db_engine()
        try:
            with engine.connect() as connection:
                stored = connection.execute(
                    text("SELECT password_hash FROM users WHERE email = :email"),
                    {"email": email},
                ).fetchone()
                phone_in_use = connection.execute(
                    text("SELECT 1 FROM users WHERE phone_number = :phone AND email <> :email LIMIT 1"),
                    {"phone": normalized_phone, "email": email},
                ).fetchone()
                
                if phone_in_use:
                    flash("That phone number is already linked to another account.", "error")
                    return redirect(url_for('profile_page'))
            
            if not stored:
                flash("Your account could not be found. Please sign in again.", "error")
                return redirect(url_for('logout'))
            
            # Handle password change
            if new_password or confirm_password or current_password:
                if not (current_password and new_password and confirm_password):
                    audit("profile_update", actor=email, outcome="failed",
                          details={"reason": "password_fields_incomplete"})
                    flash("Please fill in all the fields to update the password.", "error")
                    return redirect(url_for('profile_page'))
                
                if not check_password_hash(stored.password_hash, current_password):
                    audit("profile_update", actor=email, outcome="failed",
                          details={"reason": "wrong_current_password"})
                    flash("Your current password is incorrect. Password not changed.", "error")
                    return redirect(url_for('profile_page'))
                
                is_valid, error = _validate_password(new_password)
                if not is_valid:
                    flash(error, "error")
                    return redirect(url_for('profile_page'))
                
                if new_password != confirm_password:
                    flash("The new passwords you entered do not match.", "error")
                    return redirect(url_for('profile_page'))
            
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE users
                    SET full_name = :name,
                        phone_number = :phone,
                        county = :county,
                        password_hash = COALESCE(:new_hash, password_hash)
                    WHERE email = :email;
                """), {
                    "name": full_name,
                    "phone": normalized_phone,
                    "county": county or None,
                    "new_hash": generate_password_hash(new_password) if new_password else None,
                    "email": email,
                })
        
        except Exception as e:
            logger.error("Profile update error: %s", e)
            flash("Could not save your profile changes. Please try again.", "error")
            return redirect(url_for('profile_page'))
        
        session['user_name'] = full_name
        audit("profile_update", actor=email, outcome="success",
              details={"password_changed": bool(new_password)})
        
        if new_password:
            flash("Password successfully changed!", "success")
        else:
            flash("Your profile has been updated successfully.", "success")
        return redirect(url_for('profile_page'))
    
    profile = _load_profile(email)
    if not profile:
        flash("Your account could not be found. Please sign in again.", "error")
        return redirect(url_for('logout'))
    
    return render_template(
        'profile.html',
        profile=profile,
        covered_counties=COVERED_COUNTIES,
    )


@app.route('/profile/delete', methods=['POST'])
@limiter.limit("5 per minute")
def delete_account():
    """Permanently delete user account."""
    if 'user_email' not in session:
        flash("Please sign in to manage your profile.", "warning")
        return redirect(url_for('login_page'))
    
    email = session['user_email']
    password_input = request.form.get('password', '')
    
    if not password_input:
        flash("Please enter your password to confirm account deletion.", "error")
        return redirect(url_for('profile_page'))
    
    engine = get_db_engine()
    try:
        with engine.connect() as connection:
            stored = connection.execute(
                text("SELECT password_hash, phone_number FROM users WHERE email = :email"),
                {"email": email},
            ).fetchone()
        
        if not stored:
            session.clear()
            flash("Your account no longer exists. You have been signed out.", "info")
            return redirect(url_for('landing'))
        
        if not check_password_hash(stored.password_hash, password_input):
            audit("account_delete", actor=email, outcome="failed",
                  details={"reason": "wrong_password"})
            flash("Incorrect password. Your account was not deleted.", "error")
            return redirect(url_for('profile_page'))
        
        phone_number = stored.phone_number
        
        with engine.begin() as connection:
            connection.execute(
                text("""
                    DELETE FROM alert_dispatch_logs
                    WHERE recipient IN (:email, :phone);
                """),
                {"email": email, "phone": phone_number or ""},
            )
            connection.execute(
                text("DELETE FROM sms_delivery_logs WHERE phone_number = :phone;"),
                {"phone": phone_number or ""},
            )
            connection.execute(
                text("DELETE FROM unsubscriptions WHERE email = :email;"),
                {"email": email},
            )
            deleted = connection.execute(
                text("DELETE FROM users WHERE email = :email;"),
                {"email": email},
            )
    
    except Exception as e:
        logger.error("Account deletion error: %s", e)
        audit("account_delete", actor=email, outcome="error", details={"reason": str(e)})
        flash("Could not delete your account. Please try again.", "error")
        return redirect(url_for('profile_page'))
    
    audit("account_delete", actor=email, outcome="success",
          details={"user_deleted": bool(deleted.rowcount)})
    session.clear()
    return redirect(url_for('landing', account_deleted=1))


# =====================================================================
# ROUTES: UNSUBSCRIBE
# =====================================================================

@app.route('/unsubscribe', methods=['GET', 'POST'])
def web_unsubscribe():
    """
    Click-to-unsubscribe page.
    GET: Shows confirmation form.
    POST: Performs the opt-out.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email and 'user_email' in session:
            email = session['user_email']
        if not email:
            flash("We could not determine your account. Please sign in.", "warning")
            return redirect(url_for('login_page'))
        return _perform_unsubscribe(email)
    
    # GET: Check if token is provided
    token = request.args.get('token')
    email = None
    
    if token:
        is_valid, result = _verify_unsub_token(token)
        if is_valid:
            email = result
        else:
            flash(result, "error")
            return redirect(url_for('login_page'))
    elif 'user_email' in session:
        email = session['user_email']
    else:
        flash("Please sign in to change your alert preferences.", "warning")
        return redirect(url_for('login_page'))
    
    return render_template("unsubscribe_confirm.html", email=email, token=token), 200


def _perform_unsubscribe(email: str):
    """Perform unsubscribe operation."""
    engine = get_db_engine()
    
    try:
        with engine.begin() as connection:
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
            logger.info("Email opt-out: email channel disabled for %s", email)
            audit("unsubscribe", actor=email, outcome="success", details={"channel": "email"})
            
            alert_service.send_unsubscribe_confirmation(
                name=user.full_name,
                phone_number="",
                email=email,
                channel="email",
                opt_sms=False,
                opt_email=True
            )
            
            return render_template("unsubscribe_success.html",
                                   email=email, base_url=alert_service.base_url), 200
        else:
            flash("No account registered under that email address.", "error")
            return redirect(url_for('dashboard'))
    
    except Exception as e:
        logger.error("Unsubscribe error: %s", e)
        flash("An error occurred processing your request. Please try again.", "error")
        return redirect(url_for('dashboard'))


@app.route('/api/v1/sms/preferences', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def sms_preferences():
    """
    Dashboard SMS management endpoint.
    GET: Returns the user's current SMS subscription status.
    POST: Allows SMS users to:
      - Subscribe to SMS alerts (enable SMS channel)
      - Unsubscribe from SMS alerts (disable SMS channel)
      - Opt out from receiving SMS entirely (disable SMS + set dispatch_preference)
    """
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Please sign in first."}), 401
    
    email = session['user_email']
    engine = get_db_engine()
    
    # GET: Return current SMS preference status
    if request.method == 'GET':
        try:
            with engine.connect() as connection:
                user = connection.execute(
                    text("SELECT subscribe_sms, subscribe_email, dispatch_preference "
                         "FROM users WHERE email = :email"),
                    {"email": email}
                ).fetchone()
            
            if not user:
                return jsonify({"status": "error", "message": "Account not found."}), 404
            
            return jsonify({
                "status": "ok",
                "subscribe_sms": bool(user.subscribe_sms),
                "subscribe_email": bool(user.subscribe_email),
                "dispatch_preference": user.dispatch_preference,
            }), 200
        except Exception as e:
            logger.error("SMS preferences GET error: %s", e)
            return jsonify({"status": "error", "message": "Could not load SMS preferences."}), 500
    
    # POST: Handle subscribe/unsubscribe/optout actions
    action = (request.form.get('action') or '').strip().lower()
    
    if action not in ('subscribe', 'unsubscribe', 'optout'):
        return jsonify({"status": "error", "message": "Invalid action."}), 400
    
    try:
        with engine.connect() as connection:
            user = connection.execute(
                text("SELECT full_name, phone_number, subscribe_sms, subscribe_email, "
                     "dispatch_preference FROM users WHERE email = :email"),
                {"email": email}
            ).fetchone()
        
        if not user:
            return jsonify({"status": "error", "message": "Account not found."}), 404
        
        with engine.begin() as conn:
            if action == 'subscribe':
                # Enable SMS channel
                conn.execute(text("""
                    UPDATE users
                    SET subscribe_sms = TRUE,
                        is_subscribed = TRUE,
                        dispatch_preference = 'sms',
                        unsubscribed_at = NULL
                    WHERE email = :email;
                """), {"email": email})
                logger.info("SMS subscribed for %s", email)
                audit("sms_preferences", actor=email, outcome="subscribe")
                
                # Send confirmation SMS
                try:
                    alert_service.send_sms_subscribe_confirmation(
                        name=user.full_name,
                        phone_number=user.phone_number
                    )
                except Exception as notify_err:
                    logger.warning("SMS subscribe confirmation warning: %s", notify_err)
                
                return jsonify({"status": "ok", "message": "SMS alerts enabled."}), 200
            
            elif action == 'unsubscribe':
                # Disable SMS channel only, keep email if enabled
                conn.execute(text("""
                    UPDATE users
                    SET subscribe_sms = FALSE,
                        is_subscribed = CASE
                            WHEN subscribe_email = TRUE THEN TRUE
                            ELSE FALSE
                        END,
                        unsubscribed_at = NOW()
                    WHERE email = :email;
                """), {"email": email})
                logger.info("SMS unsubscribed for %s", email)
                audit("sms_preferences", actor=email, outcome="unsubscribe")
                
                # Send an SMS confirmation (still uses the phone for the
                # confirmation even though the SMS channel is now disabled).
                try:
                    alert_service.send_unsubscribe_confirmation(
                        name=user.full_name,
                        phone_number=user.phone_number,
                        email="",
                        channel="sms",
                        opt_sms=True,
                        opt_email=False,
                    )
                except Exception as notify_err:
                    logger.warning("SMS unsubscribe confirmation warning: %s", notify_err)
                
                return jsonify({"status": "ok", "message": "SMS alerts disabled."}), 200
            
            elif action == 'optout':
                # Opt out from receiving SMS entirely
                conn.execute(text("""
                    UPDATE users
                    SET subscribe_sms = FALSE,
                        dispatch_preference = 'email',
                        is_subscribed = CASE
                            WHEN subscribe_email = TRUE THEN TRUE
                            ELSE FALSE
                        END,
                        unsubscribed_at = NOW()
                    WHERE email = :email;
                """), {"email": email})
                logger.info("SMS opt-out for %s", email)
                audit("sms_preferences", actor=email, outcome="optout")
                
                # Send an SMS confirmation of the opt-out.
                try:
                    alert_service.send_unsubscribe_confirmation(
                        name=user.full_name,
                        phone_number=user.phone_number,
                        email="",
                        channel="sms",
                        opt_sms=True,
                        opt_email=False,
                    )
                except Exception as notify_err:
                    logger.warning("SMS opt-out confirmation warning: %s", notify_err)
                
                return jsonify({"status": "ok", "message": "You have opted out from SMS alerts."}), 200
        
    except Exception as e:
        logger.error("SMS preferences error: %s", e)
        return jsonify({"status": "error", "message": "Could not update SMS preferences."}), 500


@app.route('/unsubscribe/reason', methods=['GET', 'POST'])
def unsubscribe_reason():
    """Capture unsubscribe reason."""
    engine = get_db_engine()
    email = request.args.get('email') or session.get('user_email')
    
    if not email:
        flash("We need your email address to continue.", "warning")
        return redirect(url_for('login_page'))
    
    if request.method == 'POST':
        suggested_answer = request.form.get('suggested_answer', '').strip()
        free_text = request.form.get('free_text', '').strip()
        reason = (free_text if free_text else suggested_answer)[:255]
        
        try:
            with engine.begin() as connection:
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
            
            logger.info("Unsubscribe reason recorded for %s: %s", email, reason)
            flash("Thank you for your feedback! We appreciate your input.", "success")
            return redirect(url_for('dashboard'))
        
        except Exception as e:
            logger.error("Unsubscribe reason recording error: %s", e)
            flash("Could not save your feedback. Please try again.", "error")
            return redirect(url_for('unsubscribe_reason', email=email))
    
    return render_template('unsubscribe_reason.html', email=email)


def _generate_unsub_ref(connection, full_name):
    """Generate unique unsubscribe reference."""
    escaped = full_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    existing = connection.execute(
        text(
            "SELECT unsub_ref FROM unsubscriptions "
            "WHERE unsub_ref = :n OR unsub_ref LIKE :np ESCAPE '\\'"
        ),
        {"n": full_name, "np": f"{escaped}%"},
    ).fetchall()
    count = len(existing)
    if count == 0:
        return full_name
    return f"{full_name}{count}"


# =====================================================================
# ROUTES: SMS CALLBACK
# =====================================================================

@app.route('/api/v1/sms/callback', methods=['POST'])
@csrf.exempt
@limiter.limit("30 per minute")
def incoming_sms_callback():
    """
    Africa's Talking SMS gateway callback.
    Handles incoming SMS messages:
      - "STOP" / "STOP ALL" / "UNSUBSCRIBE" / "CANCEL" → auto-disable the SMS
        channel for the sender's account (in addition to AT's own blacklist).
      - "1" / "2" / "3" → record the user's unsubscribe reason so the admin
        reports show why the user left.
    Any other incoming text is logged but NOT treated as an unsubscribe or a
    reason submission (previously every multi-character message was incorrectly
    recorded as an unsubscribe feedback row).
    """
    from_number = request.form.get("from", "").strip()
    text_content = request.form.get("text", "").strip().upper()
    
    logger.info("Incoming SMS from %s: '%s'", from_number, text_content)
    audit("sms_callback", target=from_number, outcome="received", details={"text": text_content})
    
    if not from_number:
        return jsonify({"status": "ignored", "reason": "No sender phone parameter found."}), 400
    
    engine = get_db_engine()
    
    # Normalize phone number for search
    norm_number = from_number
    if norm_number.startswith('+254'):
        norm_number = norm_number[4:]
    elif norm_number.startswith('254'):
        norm_number = norm_number[3:]
    elif norm_number.startswith('0'):
        norm_number = norm_number[1:]
    
    search_query = f"%{norm_number}"
    reason_map = {
        "1": "Too many messages",
        "2": "Not useful",
        "3": "Too expensive",
    }
    stop_commands = {"STOP", "STOPALL", "STOP ALL", "END", "CANCEL", "UNSUBSCRIBE", "QUIT"}
    
    # Only handle recognised unsubscribe commands and optional reason codes.
    if text_content not in stop_commands and text_content not in reason_map:
        logger.info("SMS callback ignored non-unsubscribe text from %s: %r", from_number, text_content)
        return jsonify({"status": "received"}), 200
    
    try:
        with engine.begin() as connection:
            user = connection.execute(
                text("SELECT email, full_name FROM users WHERE phone_number LIKE :phone_pattern"),
                {"phone_pattern": search_query}
            ).fetchone()
            if not user:
                logger.info("SMS callback: no user found for %s", from_number)
                return jsonify({"status": "received"}), 200
            
            if text_content in stop_commands:
                # Auto-disable SMS channel (mirror of the UserInBlacklist path).
                connection.execute(text("""
                    UPDATE users
                    SET subscribe_sms = FALSE,
                        is_subscribed = CASE
                            WHEN subscribe_email = TRUE THEN TRUE
                            ELSE FALSE
                        END,
                        unsubscribed_at = NOW()
                    WHERE phone_number LIKE :phone_pattern;
                """), {"phone_pattern": search_query})
                logger.warning(
                    "SMS callback: STOP received from %s (%s) — SMS channel disabled.",
                    from_number, user.email,
                )
            
            if text_content in reason_map:
                reason_text = reason_map[text_content]
                unsub_ref = _generate_unsub_ref(connection, user.full_name)
                connection.execute(text("""
                    INSERT INTO unsubscriptions (unsub_ref, channel, reason, email)
                    VALUES (:unsub_ref, 'sms', :reason, :email)
                """), {
                    "unsub_ref": unsub_ref,
                    "reason": reason_text,
                    "email": user.email,
                })
                logger.info("Unsubscribe reason (SMS reply) from %s: %s", from_number, reason_text)
    except Exception as e:
        logger.error("SMS callback processing error: %s", e)
    
    return jsonify({"status": "received"}), 200


# =====================================================================
# ROUTES: SUBSCRIPTION
# =====================================================================

@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe_portal():
    """
    Subscription management portal with 30-day free trial and M-PESA payment.
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
            
            now = utc_now()
            phone_number = user.phone_number
            payment_status = user.payment_status
            
            # Check if payment is required (trial expired and not active)
            if payment_status == 'expired' or (user.trial_ends_at and user.trial_ends_at < now and payment_status != 'active'):
                if not Config.MPESA_ENABLED:
                    flash("M-PESA payments are currently disabled. Please contact support.", "error")
                    return redirect(url_for('subscribe_portal'))
                
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
                            
                            conn.execute(text("""
                                INSERT INTO mpesa_stk_requests
                                    (checkout_id, user_email, phone_number, amount, status)
                                VALUES (:cid, :email, :phone, :amount, 'pending')
                                ON CONFLICT (checkout_id) DO NOTHING;
                            """), {
                                "cid": checkout_id,
                                "email": email,
                                "phone": phone_number or "",
                                "amount": 150,
                            })
                        
                        flash("M-PESA STK Push sent! Please check your phone and enter your PIN to complete payment.", "info")
                        return redirect(url_for('subscribe_portal'))
                    else:
                        error_msg = stk_result.get("errorMessage", stk_result.get("error", "Unknown error"))
                        logger.error("M-PESA STK Push failed: %s", error_msg)
                        flash(f"Could not initiate M-PESA payment: {error_msg}. Please try again.", "error")
                        return redirect(url_for('subscribe_portal'))
                except Exception as mpesa_err:
                    logger.error("M-PESA STK Push exception: %s", mpesa_err)
                    flash("Could not initiate M-PESA payment. Please try again later.", "error")
                    return redirect(url_for('subscribe_portal'))
            
            # Handle trial (free or active)
            trial_started_at = user.trial_started_at
            trial_ends_at = user.trial_ends_at
            carried_over_days = 0
            new_trial_start = now
            new_trial_end = now + timedelta(days=Config.TRIAL_DAYS)
            
            if trial_started_at and trial_ends_at:
                trial_ends_at = parse_dt(trial_ends_at)
                trial_started_at = parse_dt(trial_started_at)
                
                if trial_ends_at > now:
                    remaining = (trial_ends_at - now).days
                    if remaining > 0:
                        carried_over_days = remaining
                        new_trial_end = now + timedelta(days=remaining)
                        new_trial_start = trial_started_at
                    else:
                        new_trial_start = now
                        new_trial_end = now + timedelta(days=Config.TRIAL_DAYS)
                else:
                    if payment_status != 'active':
                        new_trial_start = now
                        new_trial_end = now + timedelta(days=Config.TRIAL_DAYS)
            
            with engine.begin() as conn:
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
                    "now_ts": utc_now(),
                    "email": email
                })
            
            if global_active:
                try:
                    with engine.connect() as conn:
                        user_row = conn.execute(
                            text("SELECT full_name, phone_number, email, subscribe_sms, subscribe_email FROM users WHERE email = :e"),
                            {"e": email}
                        ).fetchone()
                    if user_row:
                        alert_service.send_trial_notice(
                            name=user_row.full_name,
                            phone_number=user_row.phone_number,
                            email=user_row.email,
                            trial_end_date=new_trial_end.strftime("%Y-%m-%d"),
                            opt_sms=user_row.subscribe_sms,
                            opt_email=user_row.subscribe_email,
                            carried_over_days=carried_over_days,
                        )
                except Exception as notify_err:
                    logger.warning("Trial notice send warning: %s", notify_err)
            
            if carried_over_days > 0:
                flash(f"Your alert preferences are saved. {carried_over_days} free-trial day(s) carried over from your previous subscription — premium alerts resume now!", "success")
            else:
                flash("Your alert preferences are saved. Your free trial has started!", "success")
            return redirect(url_for('dashboard'))
        
        except Exception as e:
            logger.error("Subscription pipeline writing error: %s", e)
            flash("We could not update your alert preferences. Please try again.", "error")
            return redirect(url_for('subscribe_portal'))
    
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT is_subscribed, subscribe_sms, subscribe_email, payment_status, "
                     "trial_started_at, trial_ends_at, unsubscribed_at FROM users WHERE email = :e"),
                {"e": email}
            ).fetchone()
    except Exception as e:
        logger.warning("Routing Warning: Could not fetch initial state: %s", e)
        row = None
    
    return render_template('subscribe.html', status=row)


# =====================================================================
# ROUTES: M-PESA CALLBACK
# =====================================================================

@app.route('/api/v1/mpesa/callback', methods=['POST'])
@csrf.exempt
@limiter.limit("20 per minute")
def mpesa_callback():
    """
    Safaricom Daraja API webhook endpoint.
    Handles STK push callback with verification.
    """
    data = request.get_json(silent=True) or {}
    logger.debug("M-PESA callback payload received")
    
    try:
        stk_callback = data.get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        checkout_id = stk_callback.get("CheckoutRequestID")
        
        if not checkout_id:
            audit("mpesa_callback", target="", outcome="failed", details={"reason": "missing_checkout_id"})
            return jsonify({"ResultCode": 1, "ResultDesc": "Invalid payload"}), 400
        
        # Source IP allowlist check
        if not _mpesa_callback_ip_allowed(request.remote_addr):
            audit("mpesa_callback", actor=request.remote_addr or "unknown", target=checkout_id,
                  outcome="blocked", details={"reason": "ip_not_allowlisted"})
            return jsonify({"ResultCode": 1, "ResultDesc": "Forbidden"}), 403
        
        engine = get_db_engine()
        
        # Verify checkout ID exists and is pending
        with engine.connect() as conn:
            pending = conn.execute(text("""
                SELECT user_email, phone_number, amount, status, mpesa_receipt
                FROM mpesa_stk_requests WHERE checkout_id = :cid;
            """), {"cid": checkout_id}).fetchone()
        
        if not pending:
            audit("mpesa_callback", target=checkout_id, outcome="failed",
                  details={"reason": "unknown_checkout"})
            logger.warning("M-PESA callback rejected unknown CheckoutRequestID: %s", checkout_id)
            return jsonify({"ResultCode": 1, "ResultDesc": "Unknown transaction"}), 400
        
        if pending.status != 'pending':
            audit("mpesa_callback", target=checkout_id, outcome="blocked",
                  details={"reason": "already_processed", "status": pending.status})
            logger.warning("M-PESA callback rejected replay for CheckoutID %s (status=%s).", checkout_id, pending.status)
            return jsonify({"ResultCode": 1, "ResultDesc": "Already processed"}), 400
        
        if result_code == 0:
            # Verify amount and phone number
            callback_meta = (stk_callback.get("CallbackMetadata") or {}).get("Item", [])
            meta = {str(item.get("Name", "")): item.get("Value") for item in callback_meta}
            callback_amount = meta.get("Amount")
            callback_phone = str(meta.get("PhoneNumber", "") or "")
            receipt = str(meta.get("MpesaReceiptNumber", "") or "")
            
            expected_amount = int(pending.amount)
            if callback_amount is None or int(callback_amount) != expected_amount:
                audit("mpesa_callback", target=checkout_id, outcome="failed",
                      details={"reason": "amount_mismatch", "expected": expected_amount, "got": callback_amount})
                logger.warning("M-PESA callback amount mismatch for %s: expected %s, got %s", checkout_id, expected_amount, callback_amount)
                return jsonify({"ResultCode": 1, "ResultDesc": "Amount mismatch"}), 400
            
            if callback_phone and _normalize_phone_digits(callback_phone) != _normalize_phone_digits(pending.phone_number):
                audit("mpesa_callback", target=checkout_id, outcome="failed",
                      details={"reason": "phone_mismatch"})
                logger.warning("M-PESA callback phone mismatch for %s.", checkout_id)
                return jsonify({"ResultCode": 1, "ResultDesc": "Phone mismatch"}), 400
            
            # Prevent receipt reuse
            if receipt:
                with engine.connect() as conn:
                    used = conn.execute(text("""
                        SELECT 1 FROM mpesa_stk_requests
                        WHERE mpesa_receipt = :receipt AND checkout_id <> :cid
                        LIMIT 1;
                    """), {"receipt": receipt, "cid": checkout_id}).fetchone()
                if used:
                    audit("mpesa_callback", target=checkout_id, outcome="blocked",
                          details={"reason": "receipt_reused", "receipt": receipt})
                    logger.warning("M-PESA callback rejected reused receipt %s.", receipt)
                    return jsonify({"ResultCode": 1, "ResultDesc": "Receipt reused"}), 400
            
            # Process successful payment
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE mpesa_stk_requests
                    SET status = 'success', completed_at = NOW(), mpesa_receipt = :receipt
                    WHERE checkout_id = :cid;
                """), {"receipt": receipt, "cid": checkout_id})
                # Restore the user's channel preferences on successful payment.
                # The user's subscribe_sms/subscribe_email were saved when the
                # STK push was initiated, so we restore them here to ensure the
                # user receives alerts on their chosen channels after payment.
                conn.execute(text("""
                    UPDATE users
                    SET payment_status = 'active',
                        is_subscribed = CASE
                            WHEN subscribe_sms = TRUE OR subscribe_email = TRUE THEN TRUE
                            ELSE FALSE
                        END,
                        trial_ends_at = NULL
                    WHERE mpesa_checkout_id = :cid;
                """), {"cid": checkout_id})
            
            audit("mpesa_callback", target=checkout_id, outcome="success", details={"receipt": receipt})
            logger.info("Payment success: account marked active for CheckoutID %s", checkout_id)
            
            # Send thank-you notification
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
                logger.warning("Thank-you notification warning: %s", notify_err)
            
            return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200
        
        else:
            # Payment failed or cancelled
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE mpesa_stk_requests
                    SET status = 'failed', completed_at = NOW()
                    WHERE checkout_id = :cid;
                """), {"cid": checkout_id})
                conn.execute(text("""
                    UPDATE users
                    SET payment_status = 'failed',
                        is_subscribed = False
                    WHERE mpesa_checkout_id = :cid;
                """), {"cid": checkout_id})
            
            audit("mpesa_callback", target=checkout_id, outcome="failed", details={"result_code": result_code})
            logger.info("Payment failed/cancelled for CheckoutID %s", checkout_id)
            return jsonify({"ResultCode": 0, "ResultDesc": "Acknowledged"}), 200
    
    except Exception as e:
        logger.error("M-PESA callback execution error: %s", e)
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Error"}), 500


# =====================================================================
# ROUTES: TRIAL EXPIRY CHECK
# =====================================================================

@app.route('/api/v1/check-trial-expiry', methods=['GET'])
def check_trial_expiry():
    """
    Check for expired trials and unsubscribe users.
    Requires TRIAL_EXPIRY_CRON=1 environment variable.
    """
    if os.environ.get("TRIAL_EXPIRY_CRON", "").strip().lower() not in ("1", "true", "yes", "on"):
        return jsonify({
            "status": "disabled",
            "expired_count": 0,
            "message": ("Trial expiry scan is disabled. Set TRIAL_EXPIRY_CRON=1 "
                        "in the environment to enable it."),
        }), 200
    
    engine = get_db_engine()
    now = utc_now()
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
            # Send the expiry notice FIRST using the user's current channel
            # preferences, BEFORE disabling them in the database. Otherwise
            # the notice would never be sent because the channels are already off.
            alert_service.send_trial_expired_notice(
                name=user.full_name,
                phone_number=user.phone_number,
                email=user.email,
                opt_sms=user.subscribe_sms,
                opt_email=user.subscribe_email
            )
            
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
            logger.info("Trial expired for user %s", user.email)
        
        return jsonify({
            "status": "success",
            "expired_count": expired_count,
            "message": f"Checked trial expiry. {expired_count} user(s) expired."
        }), 200
    
    except Exception as e:
        logger.warning("Trial expiry check error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# ROUTES: ADMIN
# =====================================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin workspace landing page."""
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
    """Admin report generation hub."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "reports"})
    return render_template('admin/reports.html')


@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    """Admin system analytics dashboard."""
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
    """Admin user management page."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "users"})
    engine = get_db_engine()
    users = []
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT user_code, full_name, email, role, is_subscribed, payment_status,
                       COALESCE(subscription_started_at, registered_at) AS sub_at
                FROM users
                ORDER BY user_code;
            """)).fetchall()
        for row in rows:
            users.append({
                "user_code": row.user_code,
                "full_name": row.full_name,
                "email": row.email,
                "role": row.role,
                "is_subscribed": bool(row.is_subscribed),
                "payment_status": row.payment_status,
                "subscribed_at": row.sub_at.strftime("%Y-%m-%d %H:%M") if row.sub_at else "—",
            })
    except Exception as e:
        logger.error("Admin users load error: %s", e)
        flash("Could not load the user list.", "error")
    
    return render_template(
        'admin/users.html',
        users=users,
        admin_name=session.get('user_name', 'Admin'),
    )


@app.route('/admin/sms-delivery')
@admin_required
def admin_sms_delivery_page():
    """Admin SMS delivery debugging console."""
    audit("admin_view", actor=session.get('user_email'),
          outcome="view", details={"page": "sms_delivery"})
    return render_template('admin/sms_delivery.html',
                           admin_name=session.get('user_name', 'Admin'))


@app.route('/admin/users/<string:user_code>/promote', methods=['POST'])
@admin_required
def admin_promote_user(user_code):
    """Promote a citizen to admin role."""
    engine = get_db_engine()
    email = session.get('user_email')
    try:
        with engine.begin() as connection:
            target = connection.execute(
                text("SELECT email, role FROM users WHERE user_code = :uc"),
                {"uc": user_code},
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
                text("UPDATE users SET role = 'admin' WHERE user_code = :uc"),
                {"uc": user_code},
            )
        audit("admin_role_change", actor=email,
              target=target.email, outcome="promote")
        flash(f"{target.email} promoted to Admin.", "success")
    except Exception as e:
        logger.error("Admin promote error: %s", e)
        flash("Could not update the user role.", "error")
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<string:user_code>/demote', methods=['POST'])
@admin_required
def admin_demote_user(user_code):
    """Demote an admin to citizen role."""
    engine = get_db_engine()
    email = session.get('user_email')
    try:
        with engine.begin() as connection:
            target = connection.execute(
                text("SELECT email, role FROM users WHERE user_code = :uc"),
                {"uc": user_code},
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
                text("UPDATE users SET role = 'citizen' WHERE user_code = :uc"),
                {"uc": user_code},
            )
        audit("admin_role_change", actor=email,
              target=target.email, outcome="demote")
        flash(f"{target.email} demoted to Citizen.", "success")
    except Exception as e:
        logger.error("Admin demote error: %s", e)
        flash("Could not update the user role.", "error")
    return redirect(url_for('admin_users'))


# =====================================================================
# ROUTES: ADMIN API
# =====================================================================

@app.route('/api/v1/admin/risk-trend', methods=['GET'])
@admin_required
@limiter.limit("20 per minute")
def admin_risk_trend():
    """Get 7-day risk trend for admin charts."""
    try:
        labels = []
        now = _eat_now()
        for i in range(7):
            d = now + timedelta(days=i)
            labels.append(d.strftime("%b %d"))
        
        engine = get_db_engine()
        latest_query = """
            SELECT alert_code, county, calculated_score, risk_level
            FROM (
                SELECT alert_code, county, calculated_score, risk_level,
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
            logger.warning("Risk trend records warning: %s", e)
            records = {}
        
        series = []
        for county in COVERED_COUNTIES:
            try:
                record = records.get(county)
                if record:
                    raw = float(record.calculated_score)
                    current = round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)
                else:
                    live = analytics_engine.calculate_composite_risk(county)
                    current = round(float(live.get("composite_risk_score", 0.3)) * 100, 1)
                
                forecast = analytics_engine.forecast_risk(
                    county=county, horizon=7, start_offset=0)
                scores = forecast.get("scores", [])
                trend = forecast.get("trend", "stable")
                points = [round(s, 4) for s in scores]
                if not points:
                    points = [round(current / 100.0, 4)]
                if len(points) < 7:
                    points.extend([points[-1]] * (7 - len(points)))
                points = points[:7]
            except Exception as e:
                logger.error("Risk trend error (%s): %s", county, e)
                current = 0.0
                points = [0.0] * 7
                trend = "stable"
            
            series.append({
                "county": county,
                "trend": trend,
                "points": points,
                "current": current,
            })
        
        prune_risk_alerts(engine, keep_days=30)
        return jsonify({
            "status": "ok",
            "labels": labels,
            "series": series,
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("Risk trend endpoint error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load risk trend data right now."
        }), 500


@app.route('/api/v1/admin/telemetry/refresh', methods=['GET'])
@admin_required
@limiter.limit("30 per minute")
def admin_telemetry_refresh():
    """Force recompute all risk scores with full ingestion."""
    try:
        ingestion = run_all_ingestion()
        summary = _build_status_board(force_recompute=True)
        analytics = get_analytics(get_db_engine())
        return jsonify({
            "status": "ok",
            "analytics": analytics,
            "ingestion": ingestion,
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("Admin telemetry refresh error: %s", e)
        return jsonify({
            "status": "error",
            "message": "We could not refresh the live telemetry data right now. Please try again."
        }), 500


@app.route('/api/v1/admin/analytics/summary', methods=['GET'])
@admin_required
@limiter.limit("60 per minute")
def admin_analytics_summary():
    """Get lightweight analytics summary."""
    try:
        engine = get_db_engine()
        analytics = get_analytics(engine)
        return jsonify({
            "status": "ok",
            "analytics": analytics,
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("Admin analytics summary error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load the analytics summary right now."
        }), 500


@app.route('/api/v1/admin/report-snapshot')
@admin_required
@limiter.limit("30 per minute")
def admin_report_snapshot():
    """Get report snapshot for admin reports."""
    try:
        engine = get_db_engine()
        snapshot = get_report_snapshot(engine)
        return jsonify({"status": "ok", "snapshot": snapshot}), 200
    except Exception as e:
        logger.error("Report snapshot error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load the live report snapshot."
        }), 500


@app.route('/api/v1/admin/sms-delivery')
@admin_required
@limiter.limit("60 per minute")
def admin_sms_delivery_api():
    """Get SMS delivery logs for admin debugging."""
    try:
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 500))
        engine = get_db_engine()
        logs = get_sms_delivery_logs(engine, limit=limit)
        summary = {
            "total": len(logs),
            "success": sum(1 for l in logs if l["status"] == "SUCCESS"),
            "failed": sum(1 for l in logs if l["status"] == "FAILED"),
            "simulated": sum(1 for l in logs if l["status"] == "SIMULATED"),
        }
        return jsonify({
            "status": "ok",
            "summary": summary,
            "logs": logs,
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("SMS delivery API error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load the SMS delivery logs right now."
        }), 500


@app.route('/api/v1/admin/dispatch-logs')
@admin_required
@limiter.limit("60 per minute")
def admin_dispatch_logs_api():
    """Get alert dispatch logs for admin debugging."""
    try:
        limit = request.args.get('limit', 200, type=int)
        limit = max(1, min(limit, 1000))
        channel = (request.args.get('channel') or '').strip().lower()
        status = (request.args.get('status') or '').strip().upper()
        engine = get_db_engine()
        logs = get_alert_dispatch_logs(engine, limit=limit)
        
        if channel and channel != 'all':
            logs = [l for l in logs if l["channel"] == channel]
        if status and status != 'ALL':
            logs = [l for l in logs if l["status"] == status]
        
        summary = {
            "total": len(logs),
            "sms": sum(1 for l in logs if l["channel"] == "sms"),
            "email": sum(1 for l in logs if l["channel"] == "email"),
            "success": sum(1 for l in logs if l["status"] == "SUCCESS"),
            "failed": sum(1 for l in logs if l["status"] == "FAILED"),
            "simulated": sum(1 for l in logs if l["status"] == "SIMULATED"),
        }
        return jsonify({
            "status": "ok",
            "summary": summary,
            "logs": logs,
            "last_sync": _eat_str(),
        }), 200
    except Exception as e:
        logger.error("Dispatch logs API error: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not load the alert dispatch logs right now."
        }), 500


@app.route('/admin/reports/<report_type>/pdf')
@admin_required
@limiter.limit("30 per minute")
def admin_report_pdf(report_type):
    """Generate and stream PDF report."""
    allowed = {
        "predicted_calamities",
        "disease_outbreaks",
        "subscribed_members",
        "unsubscribed_members",
        "alert_dispatch_logs",
    }
    if report_type not in allowed:
        audit("admin_report", actor=session.get('user_email'),
              outcome="invalid", details={"report": report_type})
        flash("Unknown report type requested.", "error")
        return redirect(url_for('admin_reports'))
    
    date_from_raw = request.args.get('date_from', '').strip()
    date_to_raw = request.args.get('date_to', '').strip()
    
    try:
        date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date() if date_from_raw else None
        date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date() if date_to_raw else None
    except ValueError:
        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="invalid",
              details={"reason": "bad_date_format"})
        flash("Invalid date range. Dates must use the YYYY-MM-DD format.", "error")
        return redirect(url_for('admin_reports'))
    
    current_year = _eat_now().year
    for label, value in (("start", date_from), ("end", date_to)):
        if value is not None and value.year > current_year:
            audit("admin_report", actor=session.get('user_email'),
                  target=report_type, outcome="invalid",
                  details={"reason": "future_year", "field": label})
            flash(f"The {label} date cannot be in a year beyond {current_year}.", "error")
            return redirect(url_for('admin_reports'))
    
    if date_from and date_to and date_from > date_to:
        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="invalid",
              details={"reason": "from_after_to"})
        flash("The start date cannot be after the end date.", "error")
        return redirect(url_for('admin_reports'))
    
    try:
        engine = get_db_engine()
        pdf_bytes = build_admin_report(
            engine, report_type, date_from=date_from, date_to=date_to,
        )
        
        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="generated",
              details={"date_from": str(date_from) if date_from else None,
                       "date_to": str(date_to) if date_to else None})
        
        filenames = {
            "predicted_calamities": "predicted_calamities_report.pdf",
            "disease_outbreaks": "disease_outbreaks_report.pdf",
            "subscribed_members": "subscribed_members_report.pdf",
            "unsubscribed_members": "unsubscribed_members_report.pdf",
            "alert_dispatch_logs": "alert_dispatch_logs_report.pdf",
        }
        
        if date_from or date_to:
            span = f"_{date_from or 'start'}_{date_to or 'today'}"
            filename = filenames[report_type].replace(".pdf", f"{span}.pdf")
        else:
            filename = filenames[report_type]
        
        response = app.response_class(
            pdf_bytes,
            mimetype='application/pdf',
        )
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
    except ImportError:
        flash("PDF generation library (reportlab) is not installed. Run: pip install reportlab", "error")
        return redirect(url_for('admin_reports'))
    except Exception as e:
        logger.error("PDF generation error: %s", e)
        audit("admin_report", actor=session.get('user_email'),
              target=report_type, outcome="error", details={"error": str(e)})
        flash("Could not generate the PDF report. Please try again.", "error")
        return redirect(url_for('admin_reports'))


# =====================================================================
# APPLICATION ENTRY POINT
# =====================================================================

if __name__ == '__main__':
    if Config.IS_PRODUCTION and Config.FLASK_DEBUG:
        logger.warning("FLASK_DEBUG is enabled in production! This is a security risk.")
    
    logger.info("Starting AthGad AI REST Gateway Server on %s:%s (debug=%s, env=%s)...",
                Config.FLASK_HOST, Config.FLASK_PORT, Config.FLASK_DEBUG, Config.ENVIRONMENT)
    
    app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=Config.FLASK_DEBUG)