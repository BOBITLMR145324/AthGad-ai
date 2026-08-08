"""
core/security.py
=================
Centralized security hardening helpers for the AthGad AI Flask application.

Provides:
  - Structured JSON logging (with audit trail)
  - Ephemeral/generated FLASK_SECRET_KEY management
  - Security response headers (TLS/HSTS/CSP/clickjacking/MIME sniffing)
  - Audit logging helper for sensitive actions
  - Ahead-of-time rate-limit identifiers (client IP hashing)
"""

import os
import hashlib
import json
import logging
import logging.handlers
import secrets
from datetime import datetime, timezone
from flask import request, g

# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON for machine parsing/auditing."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": record.created,
            "iso_time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach a request context block when available (Flask request scope)
        try:
            if request:
                payload["request"] = {
                    "method": request.method,
                    "path": request.path,
                    "remote_addr": request.remote_addr,
                    "user_agent": request.headers.get("User-Agent", ""),
                }
        except Exception:
            pass
        # Include any extra attributes attached to the record (e.g. audit data)
        for key in ("event", "actor", "target", "outcome", "details"):
            attr = getattr(record, key, None)
            if attr is not None:
                payload[key] = attr
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _make_logger(name: str) -> logging.Logger:
    """
    Builds a structured JSON logger with console output AND a persistent,
    rotating JSON-lines file so audit records survive process/container
    restarts. `name` is also used to pick a stable per-logger file name.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = JsonFormatter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Persistent rotating file handler (JSON-lines). Audit entries written here
    # are not lost on restart, unlike console-only output in containers.
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
    )
    try:
        os.makedirs(log_dir, exist_ok=True)
        slug = name.replace(".", "_").replace(" ", "_")
        log_file = os.environ.get(
            f"LOG_FILE_{slug.upper()}",
            os.path.join(log_dir, f"{slug}.jsonl"),
        )
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Could not attach persistent log file: {e}")

    logger.propagate = False
    return logger


# Application logger + audit logger
app_logger = _make_logger("AthGad")
audit_logger = _make_logger("AthGad.audit")


def audit(event: str, *, actor: str = "anonymous", target: str = "",
          outcome: str = "info", details: dict = None):
    """
    Records a structured audit-log entry for sensitive actions.
    Usage:
        audit("login", actor="user@example.com", outcome="success")
    """
    audit_logger.info(
        event,
        extra={
            "event": event,
            "actor": actor,
            "target": target,
            "outcome": outcome,
            "details": details or {},
        },
    )


# ---------------------------------------------------------------------------
# Secret Key Management
# ---------------------------------------------------------------------------

def get_secret_key(override: str = None) -> str:
    """
    Returns a server-side secret key.
    - If FLASK_SECRET_KEY is set, it is used (recommended for production).
    - Otherwise, an ephemeral random key is generated with a loud warning.
      NOTE: ephemeral keys invalidate sessions on restart — production MUST
      set FLASK_SECRET_KEY explicitly.
    """
    if override:
        return override
    env_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    generated = secrets.token_hex(32)
    app_logger.warning(
        "FLASK_SECRET_KEY is not set. Generated an ephemeral session key. "
        "Set FLASK_SECRET_KEY in production to persist sessions across restarts."
    )
    return generated


# ---------------------------------------------------------------------------
# Security / TLS / HSTS Response Headers
# ---------------------------------------------------------------------------

def apply_security_headers(response):
    """
    Attaches hardened HTTP security headers to every response.
    HSTS is only sent when ENFORCE_HTTPS is true (production behind TLS).
    """
    # HSTS — only meaningful when the app is served over HTTPS.
    enforce_https = os.environ.get("ENFORCE_HTTPS", "false").lower() == "true"
    if enforce_https:
        hsts_max_age = os.environ.get("HSTS_MAX_AGE", "31536000")
        response.headers["Strict-Transport-Security"] = (
            f"max-age={hsts_max_age}; includeSubDomains; preload"
        )
    # Prevent MIME sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Clickjacking protection
    response.headers["X-Frame-Options"] = "DENY"
    # Referrer policy
    response.headers["Referrer-Policy"] = "no-referrer"
    # Content Security Policy (allow inline styles/scripts for local CSS + charts)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data: https://fonts.googleapis.com https://fonts.gstatic.com; "
        "connect-src 'self'"
    )
    # Permissions policy (limit browser features)
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


# ---------------------------------------------------------------------------
# Rate-limit identifiers (used by Flask-Limiter)
# ---------------------------------------------------------------------------

def _client_ip() -> str:
    """Best-effort client IP extraction, honoring trusted proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def client_identifier() -> str:
    """
    Returns a stable, hashed identifier for rate-limiting without exposing
    raw client IPs in logs. Used as the Flask-Limiter key_func.
    """
    raw = _client_ip()
    return "ip:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
