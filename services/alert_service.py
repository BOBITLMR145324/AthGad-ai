import os
import html
import ssl
import smtplib
import logging
import urllib3
import base64
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import quote
from dotenv import load_dotenv
from sqlalchemy import text
from urllib3.util.retry import Retry as URLLib3Retry
from core.db_helper import get_db_engine
from core.id_codes import new_dispatch_code
from core.time_utils import utc_now, parse_dt

logger = logging.getLogger(__name__)

# Suppress SSL warnings only when SSL_VERIFY is explicitly disabled. When SSL
# verification is enabled (default), warnings are not suppressed.
load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
)

_SSL_VERIFY = os.getenv("SSL_VERIFY", "true").lower() in ("1", "true", "yes", "on")
if not _SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class AthGadAlertService:
    def __init__(self, db_engine=None):
        # Database Engine Context Binding
        self.engine = db_engine

        # SSL verification for outbound SMS requests. Defaults to TRUE (secure).
        # Set SSL_VERIFY=false in production ONLY if behind a proxy that
        # terminates TLS with a private cert you explicitly trust. This removes
        # the previous hardcoded verify=False (MITM risk).
        self.ssl_verify = _SSL_VERIFY

        # Africa's Talking SMS Configuration (REST API)
        self.at_username = os.getenv("AT_USERNAME")
        self.at_api_key = os.getenv("AT_API_KEY")
        self.at_sender_id = os.getenv("AT_SENDER_ID", None)
        # FIX: Strip the '+' symbol from sender ID if present, as many carriers reject it.
        if self.at_sender_id and self.at_sender_id.startswith('+'):
            self.at_sender_id = self.at_sender_id[1:]

        # FIX: Auto-detect sandbox mode from the environment.
        self.at_is_sandbox = (
            os.getenv("AT_IS_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on")
            or self.at_username == "sandbox"
        )
        
        # Dynamically switch API base URL based on environment mode
        if self.at_is_sandbox:
            self.at_api_base = "https://api.sandbox.africastalking.com"
        else:
            self.at_api_base = "https://api.africastalking.com"

        # Africa's Talking API readiness flag
        self._at_ready = bool(self.at_api_key and self.at_username)
        if self._at_ready:
            logger.info("Africa's Talking REST API configured (mode: %s, username: %s)",
                        "sandbox" if self.at_is_sandbox else "production", self.at_username)
            # Sanity checks: catch the most common credential mistakes early.
            key_looks_sandbox = "sandbox" in (self.at_api_key or "").lower()
            if key_looks_sandbox and not self.at_is_sandbox:
                logger.warning("AT_API_KEY looks like a SANDBOX key but AT_IS_SANDBOX is not 'true'.")
                logger.warning("Set AT_IS_SANDBOX=true in config/.env (and use username 'sandbox') or use the PRODUCTION key.")
            elif not key_looks_sandbox and self.at_is_sandbox and self.at_username != "sandbox":
                logger.warning("Sandbox mode is enabled but AT_USERNAME is '%s' (usually 'sandbox' on the sandbox).", self.at_username)
        elif not self.at_api_key:
            logger.warning("Africa's Talking API key (AT_API_KEY) is missing in config/.env — SMS alerts will be simulated.")
        elif not self.at_username:
            logger.warning("Africa's Talking username (AT_USERNAME) is missing in config/.env — SMS alerts will be simulated.")

        # SMTP Email Outbound Credentials
        self.sender_email = os.getenv("GMAIL_SENDER", os.getenv("ALERT_SENDER_EMAIL", "alerts@AthGad.ai"))
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.smtp_password = os.getenv("GMAIL_APP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))

        # Public base URL used in generated links (emails / web pages). Defaults
        # to localhost for local development; set APP_BASE_URL in production so
        # recipients receive real, working links instead of hardcoded 127.0.0.1.
        self.base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
        if self.base_url.startswith(("127.0.0.1", "localhost")):
            logger.warning("APP_BASE_URL not set in config/.env — email links point to %s.", self.base_url)
            logger.warning("Set APP_BASE_URL to your real domain in production or the Unsubscribe link "
                           "inside alert emails will not work for recipients.")

        # Dispatch cooldown (hours): repeat risk evaluations for the same county
        # and risk level are suppressed inside this window so polling / refreshes
        # never re-notify every user. A level CHANGE (e.g. Medium -> High) still
        # dispatches immediately.
        try:
            self.dispatch_cooldown_hours = max(1, int(os.getenv("ALERT_DISPATCH_COOLDOWN_HOURS", "6")))
        except (TypeError, ValueError):
            self.dispatch_cooldown_hours = 6

    def _get_at_http_pool(self):
        """
        Creates a urllib3 PoolManager with a custom SSL context.

        FIX: urllib3 2.7.0 has a bug on Python 3.14 where the default SSL
        context fails with 'SSL: WRONG_VERSION_NUMBER'. Passing an explicit
        ssl.create_default_context() works around this. We use urllib3
        directly instead of requests because requests cannot propagate a
        custom SSL context through its HTTPAdapter on this Python version.
        """
        ctx = ssl.create_default_context()
        if not self.ssl_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        retries = URLLib3Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        return urllib3.PoolManager(
            ssl_context=ctx,
            retries=retries,
            timeout=urllib3.Timeout(connect=10, read=30),
            headers={"User-Agent": "AthGadAI/1.0"},
        )

    # ------------------------------------------------------------------
    # TIERED RISK ALERT DISPATCH
    # ------------------------------------------------------------------
    def dispatch_critical_notification(self, county: str, risk_data: dict, engine=None):
        """
        Evaluates regional anomalies and dispatches alerts to matching users.

        The dedup cooldown suppresses delivery ONLY for users who already
        received a successful alert for this county+level during the cooldown.
        A NEW subscriber (who has no successful alert_dispatch_log row for this
        county) is always delivered so a newly-subscribed user immediately gets
        the alert even if a prior broadcast happened minutes ago.
        A broadcast that finds NO subscribers un-claims the cooldown so a stale
        marked row never locks out a user who registers moments later.
        """
        if engine is None:
            engine = self.engine
        if engine is None:
            logger.error("Alert Engine Error: Database engine missing. Cannot verify subscribers.")
            return False

        score = risk_data.get("composite_risk_score", "0.0%")
        level = risk_data.get("risk_level", "Low")
        metrics = risk_data.get("metrics", {})
        calamity = risk_data.get("calamity_type", "Weather Anomaly")
        advisory = risk_data.get("advisory", {})
        risk_advisory = {
            "cascading_effects": advisory.get("cascading_effects", []) if isinstance(advisory, dict) else [],
            "proactive_solutions": advisory.get("proactive_solutions", []) if isinstance(advisory, dict) else []
        }

        logger.info("Alert Engine: evaluating risk vector for %s County...", county)

        if level not in ["Medium", "High"]:
            logger.info("Alert Engine: Current Risk level (%s) is nominal. Dispatches suppressed.", level)
            return False

        # Fetch subscribers FIRST so we can detect newly-registered / newly-
        # subscribed users who have never received this county's alert.
        try:
            with engine.connect() as connection:
                rows = connection.execute(text("""
                    SELECT full_name, email, phone_number, is_subscribed,
                           subscribe_sms, subscribe_email, payment_status,
                           trial_ends_at, county, dispatch_preference
                    FROM users
                    WHERE county IS NULL
                       OR LOWER(county) = LOWER(:county);
                """), {"county": county}).fetchall()
            subscribers = list(rows)
        except Exception as e:
            logger.error("Alert Engine: subscriber fetch failed (%s): %s", county, e)
            return False

        if not subscribers:
            logger.info("Alert Engine Notice: No matching users for %s County.", county)
            # Do NOT leave a claimed row: if nobody was subscribed, no alert was
            # sent, so future registrants must be able to receive immediately.
            self._unclaim_dispatch(engine, county, level)
            return True

        # Dedup gate. Only suppress for recipients who ALREADY received this alert.
        if not self._claim_dispatch(engine, county, level):
            # Figure out which subscribers have NEVER received a successful
            # alert for this county+level during the cooldown. Deliver to those
            # (newly subscribed users) now.
            pending = []
            for sub in subscribers:
                if not self._has_received_alert(engine, sub, county, level):
                    pending.append(sub)
            if not pending:
                logger.info("Alert Engine: Duplicate dispatch for %s (%s) suppressed "
                            "- all subscribers already informed.", county, level)
                return False
            logger.info("Alert Engine: Cooldown suppresses existing subscribers, but "
                        "%d NEW subscriber(s) for %s (%s) will receive the alert.",
                        len(pending), county, level)
            subscribers = pending

        logger.info("Alert Engine: Broadcasting alerts for %d user profiles (target: %s County)...",
                    len(subscribers), county)
        now = utc_now()
        any_delivered = False

        for sub in subscribers:
            payment_status = getattr(sub, 'payment_status', 'trialing')
            trial_ends_at = getattr(sub, 'trial_ends_at', None)
            is_trial_active = False
            if trial_ends_at:
                trial_ends_at = parse_dt(trial_ends_at)
                if trial_ends_at and trial_ends_at > now:
                    is_trial_active = True
            account_is_premium = (payment_status == 'active') or is_trial_active

            opt_sms = getattr(sub, 'subscribe_sms', False) in [True, 1, 'True', '1']
            opt_email = getattr(sub, 'subscribe_email', False) in [True, 1, 'True', '1']
            sms_is_premium = account_is_premium and opt_sms
            email_is_premium = account_is_premium and opt_email

            if not opt_sms and not opt_email:
                pref = (getattr(sub, 'dispatch_preference', 'sms') or 'sms').strip().lower()
                if pref == 'email' and (getattr(sub, 'email', None) or '').strip():
                    opt_email = True
                    email_is_premium = False
                else:
                    opt_sms = True
                    sms_is_premium = False

            if opt_sms:
                sms_ok = self._send_at_sms(
                    sub.phone_number, sub.full_name, county, level, calamity,
                    is_premium=sms_is_premium, risk_advisory=risk_advisory)
                if sms_ok:
                    any_delivered = True

            if opt_email:
                email_ok = self._send_smtp_email(
                    sub.email, sub.full_name, county, level, score, calamity, metrics,
                    is_premium=email_is_premium, risk_advisory=risk_advisory)
                if email_ok:
                    any_delivered = True

        if not any_delivered:
            logger.warning("Alert Engine: No channel delivered for %s (%s) - "
                           "un-claiming dispatch so a retry can happen.", county, level)
            self._unclaim_dispatch(engine, county, level)
            return False

        return True

    def _claim_dispatch(self, engine, county: str, level: str) -> bool:
        """
        Atomically claims the right to dispatch an alert for a county+risk level.

        Returns True when this call may proceed with the broadcast; False when
        the same county+level was already dispatched within the cooldown window.

        The claim is atomic (a single UPDATE that flips dispatched = TRUE) so two
        concurrent risk-status calls cannot both pass the gate.

        FIX: The previous implementation only looked for rows with
        `dispatched = FALSE`, but core/analytics.py inserts a NEW row with
        `dispatched = FALSE` on EVERY risk computation — so the cooldown NEVER
        suppressed duplicates. Now we first check whether ANY recent row for the
        county+level has already been marked dispatched=TRUE within the cooldown
        window. If so, we suppress this dispatch (and mark any straggler
        undispatched rows so a later call also suppresses). Otherwise we atomically
        claim the newest undispatched row so this is the one-and-only broadcast.
        """
        try:
            with engine.begin() as connection:
                # Already dispatched recently for this county+level?
                already_dispatched = connection.execute(text("""
                    SELECT 1
                    FROM risk_alerts
                    WHERE county = :county
                      AND risk_level = :level
                      AND dispatched = TRUE
                      AND timestamp >= NOW() - (INTERVAL '1 hour' * :hours)
                    LIMIT 1
                """), {"county": county, "level": level, "hours": self.dispatch_cooldown_hours}).fetchone()

                if already_dispatched is not None:
                    # A dispatch already ran within the cooldown window → suppress.
                    # Clean up any fresh undispatched rows so they don't re-arm.
                    connection.execute(text("""
                        UPDATE risk_alerts
                        SET dispatched = TRUE
                        WHERE county = :county
                          AND risk_level = :level
                          AND dispatched = FALSE
                          AND timestamp >= NOW() - (INTERVAL '1 hour' * :hours)
                    """), {"county": county, "level": level, "hours": self.dispatch_cooldown_hours})
                    return False

                # No prior dispatched row in window → claim the newest undispatched one.
                result = connection.execute(text("""
                    UPDATE risk_alerts
                    SET dispatched = TRUE
                    WHERE county = :county
                      AND risk_level = :level
                      AND dispatched = FALSE
                      AND timestamp >= NOW() - (INTERVAL '1 hour' * :hours)
                """), {"county": county, "level": level, "hours": self.dispatch_cooldown_hours})

                if result.rowcount > 0:
                    return True

                # Nothing to claim. If NO recent row exists at all (e.g. data was
                # pruned or never persisted), allow dispatch so real alerts are
                # never silently dropped. Otherwise, a previous claim won the race.
                recent = connection.execute(text("""
                    SELECT 1
                    FROM risk_alerts
                    WHERE county = :county
                      AND risk_level = :level
                      AND timestamp >= NOW() - (INTERVAL '1 hour' * :hours)
                    LIMIT 1
                """), {"county": county, "level": level, "hours": self.dispatch_cooldown_hours}).fetchone()

                return recent is None
        except Exception as e:
            logger.warning("Alert Engine dedup warning (%s, %s): %s", county, level, e)
            return True

    def _unclaim_dispatch(self, engine, county: str, level: str):
        """
        Resets the `dispatched` flag on recent risk-alert rows for a county+level
        back to FALSE so a later risk-status poll / refresh can attempt the
        broadcast again.

        Called when a dispatch ran but NO channel delivered successfully
        (e.g. every SMS recipient was blacklisted by Africa's Talking). A failed
        delivery should consume the cooldown window; otherwise the user never
        receives an alert until the 6-hour window expires.
        """
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE risk_alerts
                    SET dispatched = FALSE
                    WHERE county = :county
                      AND risk_level = :level
                      AND timestamp >= NOW() - (INTERVAL '1 hour' * :hours)
                """), {"county": county, "level": level, "hours": self.dispatch_cooldown_hours})
        except Exception as e:
            logger.warning("Alert Engine un-claim warning (%s, %s): %s", county, level, e)

    def _has_received_alert(self, engine, sub, county: str, level: str) -> bool:
        """
        Returns True when `sub` has already received a SUCCESSFUL alert
        dispatch for this county+risk-level within the cooldown window.
        Used by dispatch_critical_notification so a newly-subscribed user who
        has never been alerted is always delivered even when a prior broadcast
        suppressed the cooldown.
        """
        try:
            recipient = (getattr(sub, 'phone_number', None) or '').strip()
            if not recipient:
                recipient = (getattr(sub, 'email', None) or '').strip()
            if not recipient:
                return False
            with engine.connect() as connection:
                row = connection.execute(text("""
                    SELECT 1
                    FROM alert_dispatch_logs
                    WHERE recipient = :recipient
                      AND message_type = 'alert'
                      AND status = 'SUCCESS'
                      AND message_content LIKE :county_pat
                      AND dispatched_at >= NOW() - (INTERVAL '1 hour' * :hours)
                    LIMIT 1
                """), {
                    "recipient": recipient[:120],
                    "county_pat": f"%{county}%",
                    "hours": self.dispatch_cooldown_hours,
                }).fetchone()
                return row is not None
        except Exception as e:
            logger.warning("Alert Engine: received-alert check warning (%s): %s", getattr(sub, 'email', '?'), e)
            return False

    # ------------------------------------------------------------------
    # SMS DISPATCH (tiered content)
    # ------------------------------------------------------------------
    def _build_alert_sms_body(self, name: str, county: str, level: str, calamity: str,
                              is_premium: bool, risk_advisory: dict) -> str:
        """Builds the exact SMS message body for a tiered alert dispatch."""
        risk_advisory = risk_advisory or {}
        if is_premium:
            # Structure the message as clean labelled blocks — uppercase
            # headings serve as SMS "bold" and every block sits on its own
            # line so the alert is easy to scan on a phone.
            cascading_list = risk_advisory.get("cascading_effects", []) or []
            proactive_list = risk_advisory.get("proactive_solutions", []) or []

            impacts = "\n".join(
                f"{i}. {item}" for i, item in enumerate(cascading_list[:3], 1)
            ) if cascading_list else "1. Monitor local advisories."

            actions = "\n".join(
                f"{i}. {item}" for i, item in enumerate(proactive_list[:2], 1)
            ) if proactive_list else "1. Stay safe and follow local guidance."

            return (
                f"AthGad AI ALERT: {county.upper()} COUNTY - {level.upper()} RISK\n"
                f"Hello {name}!\n"
                f"\n"
                f"THREAT:\n{calamity}\n"
                f"\n"
                f"IMPACTS:\n{impacts}\n"
                f"\n"
                f"WHAT TO DO:\n{actions}\n"
                f"\n"
                f"Manage your alerts at {self.base_url}/dashboard"
            )
        return (
            f"AthGad AI: Safety alert for {county} County. "
            f"The risk level is {level.upper()}. "
            f"Get full details & safety advice with a free 30-day premium trial "
            f"at {self.base_url}/subscribe. Re-subscribing after a break keeps any "
            f"remaining free-trial days, so you never lose them."
        )

    def _send_at_sms(self, phone_number: str, name: str, county: str, level: str, calamity: str,
                     is_premium: bool = False, risk_advisory: dict = None):
        """Dispatches automated SMS summary alerts via Africa's Talking with tier-restricted content."""
        risk_advisory = risk_advisory or {}
        phone_number = phone_number.strip()

        # Format phone number for Africa's Talking (international format with +)
        phone_number = phone_number.replace(' ', '').replace('-', '')
        if phone_number.startswith('+254'):
            pass  # Already in correct format with +
        elif phone_number.startswith('254'):
            phone_number = '+' + phone_number
        elif phone_number.startswith('0'):
            phone_number = '+254' + phone_number[1:]
        elif phone_number.startswith('+'):
            pass  # Other international format, keep as-is

        tier_label = "premium" if is_premium else "baseline"
        subscription_status = tier_label
        message_body = self._build_alert_sms_body(
            name, county, level, calamity, is_premium, risk_advisory)

        if not self._at_ready:
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type="alert", tier=tier_label,
                status="SIMULATED", error_detail="AT not configured - SMS simulated, not sent")
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type="alert",
                message=message_body, subscription_status=subscription_status,
                status="SIMULATED",
                error_detail="AT not configured - SMS simulated, not sent")
            return True

        try:
            headers = {
                "apiKey": self.at_api_key,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            payload = {
                "username": self.at_username,
                "to": phone_number,
                "message": message_body
            }
            
            # FIX: Only include 'from' if a sender ID is explicitly defined 
            # and is not blacklisted. If omitted, Africa's Talking uses your default shortcode.
            if self.at_sender_id and self.at_sender_id.strip():
                payload["from"] = self.at_sender_id.strip()
            else:
                logger.info(f"SMS dispatch: 'from' omitted for {phone_number} (Using default AT shortcode instead).")

            url = f"{self.at_api_base}/version1/messaging"

            # FIX: Use urllib3 directly with a custom SSL context. urllib3 2.7.0
            # has a bug on Python 3.14 where the default SSL context fails with
            # 'SSL: WRONG_VERSION_NUMBER'. requests cannot propagate a custom
            # SSL context through its HTTPAdapter on this Python version.
            http = self._get_at_http_pool()
            
            # FIX: Use encode_multipart=False to send as x-www-form-urlencoded
            # Africa's Talking API rejects multipart/form-data with boundary strings
            response = http.request(
                "POST",
                url,
                headers=headers,
                fields=payload,
                encode_multipart=False,  # <-- CRITICAL FIX: sends as x-www-form-urlencoded
                timeout=urllib3.Timeout(connect=10, read=30),
            )

            if response.status in [200, 201]:
                resp_json = response.json()
                recipients = resp_json.get('SMSMessageData', {}).get('Recipients', [])
                tier = "premium" if is_premium else "baseline"
                if recipients:
                    status = recipients[0].get('status', 'Unknown')
                    cost = recipients[0].get('cost', 'N/A')
                    msg_id = recipients[0].get('messageId', 'N/A')
                    if status == "Success":
                        self._log_sms_delivery(
                            phone=phone_number, name=name, message_type="alert",
                            tier=tier, status="SUCCESS", http_status=response.status,
                            at_status=status, cost=cost, message_id=msg_id)
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type="alert",
                            message=message_body, subscription_status=subscription_status,
                            status="SUCCESS")
                        return True
                    else:
                        detail = f"AT rejected with status '{status}'"
                        self._log_sms_delivery(
                            phone=phone_number, name=name, message_type="alert",
                            tier=tier, status="FAILED", http_status=response.status,
                            at_status=status, cost=cost, message_id=msg_id,
                            error_detail=detail)
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type="alert",
                            message=message_body, subscription_status=subscription_status,
                            status="FAILED", error_detail=detail)
                        
                        if status == "UserInBlacklist":
                            logger.info(
                                "SMS alert blacklisted by AT for %s (%s) — "
                                "keeping SMS subscription active; delivery blocked by AT.",
                                name, phone_number,
                            )
                        return False
                else:
                    detail = f"AT response missing Recipients: {str(resp_json)[:200]}"
                    self._log_sms_delivery(
                        phone=phone_number, name=name, message_type="alert",
                        tier=tier, status="FAILED", http_status=response.status,
                        at_status="NO_RECIPIENTS", error_detail=detail)
                    self._record_dispatch(
                        channel="sms", recipient=phone_number, message_type="alert",
                        message=message_body, subscription_status=subscription_status,
                        status="FAILED", error_detail=detail)
                    return False
            elif response.status == 401:
                detail = "invalid AT_API_KEY/AT_USERNAME (see fix guidance below)"
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type="alert",
                    tier="premium" if is_premium else "baseline", status="FAILED",
                    http_status=401, at_status="AUTH_FAILURE", error_detail=detail)
                logger.warning("The AT_API_KEY in config/.env does not match AT_USERNAME='%s' for the %s environment.", self.at_username, self.at_api_base)
                logger.warning("Fix: regenerate/update AT_API_KEY at https://account.africastalking.com/apps/sandbox/keys")
                logger.warning("      (sandbox)  or https://account.africastalking.com/apps/prod/keys (production).")
                logger.warning("Sandbox mode requires AT_IS_SANDBOX=true and the SANDBOX key. Production needs the PRODUCTION key.")
                self._record_dispatch(
                    channel="sms", recipient=phone_number, message_type="alert",
                    message=message_body, subscription_status=subscription_status,
                    status="FAILED", error_detail=detail)
                return False
            else:
                detail = response.data.decode('utf-8', errors='replace')[:200]
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type="alert",
                    tier="premium" if is_premium else "baseline", status="FAILED",
                    http_status=response.status, at_status="HTTP_ERROR",
                    error_detail=detail)
                self._record_dispatch(
                    channel="sms", recipient=phone_number, message_type="alert",
                    message=message_body, subscription_status=subscription_status,
                    status="FAILED", error_detail=detail)
                return False

        except urllib3.exceptions.SSLError as ssl_err:
            detail = str(ssl_err)[:500]
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type="alert",
                tier="premium" if is_premium else "baseline", status="FAILED",
                at_status="SSL_ERROR", error_detail=detail)
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type="alert",
                message=message_body, subscription_status=subscription_status,
                status="FAILED", error_detail=detail)
            return False
        except urllib3.exceptions.MaxRetryError as conn_err:
            detail = str(conn_err)[:500]
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type="alert",
                tier="premium" if is_premium else "baseline", status="FAILED",
                at_status="CONNECTION_ERROR", error_detail=detail)
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type="alert",
                message=message_body, subscription_status=subscription_status,
                status="FAILED", error_detail=detail)
            return False
        except urllib3.exceptions.TimeoutError as timeout_err:
            detail = str(timeout_err)[:500]
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type="alert",
                tier="premium" if is_premium else "baseline", status="FAILED",
                at_status="TIMEOUT", error_detail=detail)
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type="alert",
                message=message_body, subscription_status=subscription_status,
                status="FAILED", error_detail=detail)
            return False
        except Exception as e:
            detail = str(e)[:500]
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type="alert",
                tier="premium" if is_premium else "baseline", status="FAILED",
                at_status="EXCEPTION", error_detail=detail)
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type="alert",
                message=message_body, subscription_status=subscription_status,
                status="FAILED", error_detail=detail)
            return False

    # ------------------------------------------------------------------
    # EMAIL DISPATCH (tiered content)
    # ------------------------------------------------------------------
    def _send_smtp_email(self, recipient_email: str, name: str, county: str, level: str, score: str, calamity: str,
                         metrics: dict, is_premium: bool = False, risk_advisory: dict = None):
        """Delivers detailed HTML analytical matrices to verified Premium members or basic alerts to Free members."""
        risk_advisory = risk_advisory or {}
        cascading_list = risk_advisory.get("cascading_effects", []) or []
        proactive_list = risk_advisory.get("proactive_solutions", []) or []

        def _esc(value):
            return html.escape(str(value or ""), quote=True)

        safe_name = _esc(name)
        safe_county = _esc(county)
        safe_level = _esc(level)
        safe_score = _esc(score)
        safe_calamity = _esc(calamity)

        if is_premium:
            subject = f"AthGad Alert: {safe_level} Risk in {safe_county} County"
            headline = "Important Safety Advisory"
            description = f"Hello {safe_name}, our monitoring system has detected rising risk in your area. Here is what you need to know and how to stay safe."

            metrics_html = f"""
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; font-weight: bold; color: #94a3b8;">County:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ffffff; text-align: right;">{safe_county} County</td>
                </tr>
                <tr>
                    <td style="padding: 10px; color: #94a3b8;">Main Threat:</td>
                    <td style="padding: 10px; color: #f1f5f9; text-align: right;">{safe_calamity}</td>
                </tr>
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; color: #94a3b8;">Risk Level:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ef4444; text-align: right;">{safe_score} ({safe_level})</td>
                </tr>
            """

            cascading_html = ""
            if cascading_list:
                items = "".join(f"<li>{_esc(item)}</li>" for item in cascading_list[:4])
                cascading_html = f"""
                    <h4 style="color: #ffffff; margin-bottom: 8px; font-size: 13px; text-transform: uppercase;">Possible Impacts:</h4>
                    <ul style="font-size: 12px; color: #94a3b8; padding-left: 20px; line-height: 1.8; margin-top: 0;">
                        {items}
                    </ul>
                """

            proactive_html = ""
            if proactive_list:
                items = "".join(f"<li>{_esc(item)}</li>" for item in proactive_list[:4])
                proactive_html = f"""
                    <h4 style="color: #ffffff; margin-bottom: 8px; font-size: 13px; text-transform: uppercase;">What You Can Do:</h4>
                    <ul style="font-size: 12px; color: #94a3b8; padding-left: 20px; line-height: 1.8; margin-top: 0;">
                        {items}
                    </ul>
                """

            action_button = "Action Required: Access your operational dashboard to view the full proactive checklist."
            action_color = "#34d399"

            footer_links_html = f"""
                <a href="{self.base_url}/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="{self.base_url}/unsubscribe?email={quote(recipient_email, safe='')}" style="color: #ef4444; text-decoration: none;">Unsubscribe from Premium</a>
            """
        else:
            subject = f"AthGad Baseline Advisory: Safety Anomaly detected in {safe_county} County"
            headline = "Baseline Environmental Safety Warning"
            description = f"Hello {safe_name}, AthGad sensors have flagged an operational climate anomaly in {safe_county} County reaching a <strong>{safe_level}</strong> alert state."

            metrics_html = f"""
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; font-weight: bold; color: #94a3b8;">Observation Target:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ffffff; text-align: right;">{safe_county} County</td>
                </tr>
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; color: #94a3b8;">Risk Evaluation Level:</td>
                    <td style="padding: 10px; font-weight: bold; color: #eab308; text-align: right;">{safe_level} Risk State</td>
                </tr>
            """

            cascading_html = ""
            proactive_html = ""

            action_button = (
                "Unlock Real-Time Pipelines: Activate your FREE 30-day premium trial at "
                "AthGad.ai/subscribe for full risk reports, impacts and safety advice. "
                "Re-subscribing after a break carries any remaining free-trial days over."
            )
            action_color = "#38bdf8"

            footer_links_html = f"""
                <a href="{self.base_url}/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="{self.base_url}/subscribe" style="color: #10b981; font-weight: bold; text-decoration: none;">Upgrade to Premium</a>
            """

        body_html = f"""
        <html>
        <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 30px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                <div style="margin-bottom: 20px;">
                    <span style="height: 10px; width: 10px; background-color: #10b981; display: inline-block; border-radius: 50%; margin-right: 6px;"></span>
                    <strong style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 16px; color: #ffffff;">AthGad AI System</strong>
                </div>

                <h2 style="color: #ef4444; font-size: 20px; border-bottom: 1px solid #1e293b; padding-bottom: 12px; margin-top: 0;">
                    {headline}
                </h2>

                <p style="font-size: 14px; color: #94a3b8; line-height: 1.6;">
                    {description}
                </p>

                <table style="width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 13px;">
                    {metrics_html}
                </table>

                {cascading_html}
                {proactive_html}

                <p style="font-size: 13px; color: {action_color}; font-weight: bold; margin-top: 24px;">
                    {action_button}
                </p>

                <p style="font-size: 11px; color: #64748b; text-align: center; margin-top: 30px; border-top: 1px solid #1e293b; padding-top: 16px;">
                    Sent by AthGad AI System. <br><br>
                    {footer_links_html}
                </p>
            </div>
        </body>
        </html>
        """

        if not self.smtp_password:
            logger.info("Alert Engine Notice: SMTP key empty. Simulated email data block for %s.", recipient_email)
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="alert",
                message=subject, subscription_status="premium" if is_premium else "baseline",
                status="SIMULATED",
                error_detail="SMTP password empty - email simulated, not sent")
            return True

        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            msg['Subject'] = subject

            # FIX: Include a plain-text alternative alongside the HTML so
            # spam filters / content blockers / notification-previews display
            # usable content. A pure HTML-only message is much more likely to
            # land in Gmail's Spam/Promotions folder.
            text_body = (
                f"{headline}\n\n"
                f"{html.unescape(description)}\n\n"
                f"County: {safe_county} | Risk: {safe_level}\n"
                f"Threat: {safe_calamity}\n\n"
                f"Manage your alerts at {self.base_url}"
            )
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(body_html, 'html'))

            import socket
            socket.setdefaulttimeout(15)

            # Pre-resolve the SMTP host and log the IPs we resolve. This makes
            # DNS failures obvious and actionable when the network blocks
            # smtp.gmail.com (the #1 cause of getaddrinfo failed on home/office
            # networks in Kenya and other countries).
            try:
                resolved = socket.getaddrinfo(self.smtp_server, self.smtp_port, socket.AF_INET, socket.SOCK_STREAM)
                logger.info("EMAIL SMTP host '%s' resolved to: %s",
                            self.smtp_server,
                            sorted({addr[4][0] for addr in resolved}))
            except socket.gaierror as dns_err:
                logger.critical(
                    "EMAIL DNS FAILURE: Cannot resolve SMTP host '%s' (getaddrinfo failed). "
                    "This is a NETWORK/DNS issue, not an app bug. Options: "
                    "(1) verify `nslookup smtp.gmail.com` works on this machine, "
                    "(2) use a different SMTP relay host reachable from this network "
                    "(e.g. Microsoft 365 smtp.office365.com:587, Zoho smtp.zoho.com:587), "
                    "(3) set SMTP_SERVER to a direct IP if DNS is broken, "
                    "(4) allow list smtp.gmail.com:587 on the outbound firewall.",
                    self.smtp_server,
                )
                raise

            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15)
            server.starttls()
            server.login(self.sender_email, self.smtp_password)
            server.sendmail(self.sender_email, recipient_email, msg.as_string())
            server.quit()

            logger.info("EMAIL SUCCESS: Telemetry layout dispatched to %s. Tier: %s",
                        recipient_email, "Premium" if is_premium else "Free Baseline")
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="alert",
                message=subject, subscription_status="premium" if is_premium else "baseline",
                status="SUCCESS")
            return True
        except Exception as e:
            logger.error("EMAIL ERROR: Failed to send to %s: %s", recipient_email, e)
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="alert",
                message=subject, subscription_status="premium" if is_premium else "baseline",
                status="FAILED", error_detail=str(e)[:500])
            return False

    # ------------------------------------------------------------------
    # NOTIFICATION HELPERS (trial, unsubscribe, payment)
    # ------------------------------------------------------------------
    def send_trial_notice(self, name: str, phone_number: str, email: str, trial_end_date: str,
                          opt_sms: bool = False, opt_email: bool = False,
                          carried_over_days: int = 0):
        """
        Notifies the user that their free trial has started. When the user
        unsubscribed early and re-subscribed, `carried_over_days` is > 0 and the
        message clearly explains their remaining trial days carried over.
        """
        trial_blurb = (
            f"Your 30-day free trial has started. You will receive full premium "
            f"alerts until {trial_end_date}."
        )
        carryover_blurb = (
            f"You have {carried_over_days} day(s) of premium trial remaining "
            f"from your previous subscription, so your premium alerts are now "
            f"active until {trial_end_date}."
        )
        detail_line = carryover_blurb if carried_over_days > 0 else trial_blurb

        if opt_sms and phone_number:
            msg = (
                f"AthGad AI: Welcome back {name}! {detail_line} "
                f"After that, a fee of 150 KES/month via M-PESA applies. "
                f"Visit {self.base_url}/subscribe to manage your subscription."
            )
            self._send_sms_raw(phone_number, msg, message_type="trial", name=name)

        if opt_email and email:
            subject = (
                "AthGad AI: Premium Trial Restored - Remaining Days Carry Over"
                if carried_over_days > 0
                else "AthGad AI: Your Free Trial Has Started"
            )
            safe_name = html.escape(str(name), quote=True)
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">{("Welcome Back to Premium, " if carried_over_days > 0 else "Welcome to AthGad Premium, ")}{safe_name}!</h2>
                    <p style="color: #94a3b8;">{detail_line}</p>
                    <p style="color: #eab308;">After the trial, a fee of <strong>150 KES/month</strong> via M-PESA will apply to continue receiving premium alerts.</p>
                    <p style="color: #94a3b8;">Manage your subscription at <a href="{self.base_url}/subscribe" style="color: #38bdf8;">AthGad.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    def send_trial_expired_notice(self, name: str, phone_number: str, email: str,
                                  opt_sms: bool = False, opt_email: bool = False):
        """Notifies the user that their trial has ended and they have been unsubscribed."""
        if opt_sms and phone_number:
            msg = (
                f"AthGad AI: Hello {name}, your 30-day free trial has ended. "
                f"You have been unsubscribed from premium alerts. "
                f"Visit AthGad.ai/subscribe to re-subscribe and pay 150 KES/month via M-PESA."
            )
            self._send_sms_raw(phone_number, msg, message_type="trial_expired", name=name)

        if opt_email and email:
            subject = "AthGad AI: Your Free Trial Has Ended"
            safe_name = html.escape(str(name), quote=True)
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Trial Period Ended, {safe_name}</h2>
                    <p style="color: #94a3b8;">Your 30-day free trial has concluded and you have been unsubscribed from premium alerts.</p>
                    <p style="color: #eab308;">To continue receiving full premium alerts, please re-subscribe and pay <strong>150 KES/month</strong> via M-PESA.</p>
                    <p style="color: #94a3b8;"><a href="{self.base_url}/subscribe" style="color: #38bdf8;">Click here to re-subscribe</a></p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    def send_payment_thank_you(self, name: str, phone_number: str, email: str,
                               opt_sms: bool = False, opt_email: bool = False):
        """Sends a thank-you message after successful M-PESA payment with next payment date."""
        next_payment = (utc_now() + timedelta(days=30)).strftime("%Y-%m-%d")
        if opt_sms and phone_number:
            msg = (
                f"AthGad AI: Thank you {name} for your payment of 150 KES! "
                f"Your premium subscription is now active. "
                f"Your next payment of 150 KES will be due on {next_payment}. "
                f"Manage your alerts at {self.base_url}/dashboard."
            )
            self._send_sms_raw(phone_number, msg, message_type="payment", name=name)

        if opt_email and email:
            subject = "AthGad AI: Payment Successful - Thank You!"
            safe_name = html.escape(str(name), quote=True)
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">Payment Successful, {safe_name}!</h2>
                    <p style="color: #94a3b8;">Thank you for your payment of <strong>150 KES</strong>.</p>
                    <p style="color: #94a3b8;">Your premium subscription is now active.</p>
                    <p style="color: #eab308;">Your next payment of <strong>150 KES</strong> will be due on <strong>{next_payment}</strong>.</p>
                    <p style="color: #94a3b8;">Manage your subscription at <a href="{self.base_url}/subscribe" style="color: #38bdf8;">AthGad.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    def send_sms_subscribe_confirmation(self, name: str, phone_number: str):
        """Sends a confirmation SMS when a user subscribes to SMS alerts."""
        if not phone_number:
            return
        msg = (
            f"AthGad AI: Hello {name}, you are now subscribed to SMS alerts. "
            f"You will receive urgent safety alerts for your county. "
            f"Manage your alerts at {self.base_url}/dashboard."
        )
        self._send_sms_raw(phone_number, msg, message_type="subscribe", name=name)

    def send_unsubscribe_confirmation(self, name: str, phone_number: str, email: str, channel: str,
                                      opt_sms: bool = False, opt_email: bool = False):
        """
        Sends a polite confirmation requesting a reason for unsubscription and
        encourages the user to re-subscribe: any remaining free-trial days from
        a premature unsubscription carry over to their next subscription.
        """
        rejoin_encouragement = (
            "You can re-subscribe any time to resume premium alerts, and any "
            "remaining free-trial days from your previous subscription will "
            "carry over automatically."
        )
        if channel == "sms" and opt_sms and phone_number:
            msg = (
                f"AthGad AI: Hello {name}, you have been unsubscribed from SMS alerts. "
                f"{rejoin_encouragement} Visit {self.base_url}/subscribe to come back."
            )
            self._send_sms_raw(phone_number, msg, message_type="unsubscribe", name=name)

        if channel == "email" and opt_email and email:
            subject = "AthGad AI: You Have Been Unsubscribed"
            safe_name = html.escape(str(name), quote=True)
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Unsubscribe Confirmed, {safe_name}</h2>
                    <p style="color: #94a3b8;">You have been unsubscribed from email alerts.</p>
                    <p style="color: #94a3b8;">We're sorry to see you go. If you're willing, please tell us why by visiting the link below:</p>
                    <p style="text-align: center;">
                        <a href="{self.base_url}/unsubscribe/reason?email={quote(email, safe='')}"
                           style="display: inline-block; background-color: #1e293b; color: #f8fafc; padding: 10px 20px; border-radius: 8px; text-decoration: none;">
                           Share Your Feedback
                        </a>
                    </p>
                    <p style="color: #10b981; line-height: 1.6;">{rejoin_encouragement}</p>
                    <p style="color: #64748b; font-size: 12px;">Re-subscribe any time at <a href="{self.base_url}/subscribe" style="color: #38bdf8;">AthGad.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    # ------------------------------------------------------------------
    # RAW SEND HELPERS (no tier logic)
    # ------------------------------------------------------------------
    def _send_sms_raw(self, phone_number: str, message_body: str,
                      message_type: str = "notification", name: str = "",
                      auto_disable_on_blacklist: bool = False):
        """
        Sends a raw SMS without tier logic (for notifications).

        `auto_disable_on_blacklist` defaults to False: a `UserInBlacklist`
        response on a confirmation / trial / payment notification should NOT
        disable the subscriber's SMS channel. (Previously the auto-disable was
        unconditional, so a user clicking "Subscribe to SMS" would get their
        `subscribe_sms` flag instantly flipped back to FALSE when the welcome
        SMS failed with `UserInBlacklist` — making the dashboard appear broken.)
        Only actual alert dispatches (`_send_at_sms`) pass True so a permanently
        blacklisted recipient stops burning dispatch attempts.
        """
        phone_number = phone_number.strip().replace(' ', '').replace('-', '')
        if phone_number.startswith('+254'):
            pass
        elif phone_number.startswith('254'):
            phone_number = '+' + phone_number
        elif phone_number.startswith('0'):
            phone_number = '+254' + phone_number[1:]
        elif phone_number.startswith('+'):
            pass

        if not self._at_ready:
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type=message_type,
                tier="none", status="SIMULATED",
                error_detail="AT not configured - SMS simulated, not sent")
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type=message_type,
                message=message_body, subscription_status="none",
                status="SIMULATED",
                error_detail="AT not configured - SMS simulated, not sent")
            return True

        try:
            headers = {
                "apiKey": self.at_api_key,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            payload = {
                "username": self.at_username,
                "to": phone_number,
                "message": message_body
            }
            if self.at_sender_id and self.at_sender_id.strip():
                payload["from"] = self.at_sender_id.strip()

            url = f"{self.at_api_base}/version1/messaging"

            # FIX: Use urllib3 directly with a custom SSL context. urllib3 2.7.0
            # has a bug on Python 3.14 where the default SSL context fails with
            # 'SSL: WRONG_VERSION_NUMBER'. requests cannot propagate a custom
            # SSL context through its HTTPAdapter on this Python version.
            http = self._get_at_http_pool()
            
            # FIX: Use encode_multipart=False to send as x-www-form-urlencoded
            # Africa's Talking API rejects multipart/form-data with boundary strings
            response = http.request(
                "POST",
                url,
                headers=headers,
                fields=payload,
                encode_multipart=False,  # <-- CRITICAL FIX: sends as x-www-form-urlencoded
                timeout=urllib3.Timeout(connect=10, read=30),
            )

            if response.status in [200, 201]:
                resp_json = response.json()
                recipients = resp_json.get('SMSMessageData', {}).get('Recipients', [])
                if recipients:
                    r = recipients[0]
                    at_status = r.get('status', 'Unknown')
                    if at_status == "Success":
                        self._log_sms_delivery(
                            phone=phone_number, name=name, message_type=message_type,
                            tier="none", status="SUCCESS", http_status=response.status,
                            at_status=at_status, cost=r.get('cost', 'N/A'),
                            message_id=r.get('messageId', 'N/A'))
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type=message_type,
                            message=message_body, subscription_status="none",
                            status="SUCCESS")
                        return True
                    else:
                        detail = f"AT rejected with status '{at_status}'"
                        self._log_sms_delivery(
                            phone=phone_number, name=name, message_type=message_type,
                            tier="none", status="FAILED", http_status=response.status,
                            at_status=at_status, cost=r.get('cost', 'N/A'),
                            message_id=r.get('messageId', 'N/A'), error_detail=detail)
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type=message_type,
                            message=message_body, subscription_status="none",
                            status="FAILED", error_detail=detail)
                        
                        if at_status == "UserInBlacklist" and auto_disable_on_blacklist:
                            try:
                                engine = get_db_engine()
                                with engine.begin() as conn:
                                    conn.execute(text("""
                                        UPDATE users
                                        SET subscribe_sms = FALSE,
                                            is_subscribed = CASE
                                                WHEN subscribe_email = TRUE THEN TRUE
                                                ELSE FALSE
                                            END,
                                            unsubscribed_at = NOW()
                                        WHERE phone_number = :phone;
                                    """), {"phone": phone_number})
                                logger.warning(
                                    "SMS channel auto-disabled for %s (%s) — "
                                    "number blacklisted by Africa's Talking (STOP reply).",
                                    name, phone_number)
                            except Exception as db_err:
                                logger.warning(
                                    "Could not auto-disable SMS channel for %s: %s",
                                    phone_number, db_err)
                        elif at_status == "UserInBlacklist":
                            logger.info(
                                "SMS blacklist notice (no auto-disable): %s (%s) "
                                "is blacklisted by AT but this was a %s notification.",
                                name, phone_number, message_type,
                            )
                        return False
                else:
                    detail = f"AT response missing Recipients: {str(resp_json)[:200]}"
                    self._log_sms_delivery(
                        phone=phone_number, name=name, message_type=message_type,
                        tier="none", status="FAILED", http_status=response.status,
                        at_status="NO_RECIPIENTS", error_detail=detail)
                    self._record_dispatch(
                        channel="sms", recipient=phone_number, message_type=message_type,
                        message=message_body, subscription_status="none",
                        status="FAILED", error_detail=detail)
                    return False
            else:
                detail = response.data.decode('utf-8', errors='replace')[:200]
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type=message_type,
                    tier="none", status="FAILED", http_status=response.status,
                    at_status="HTTP_ERROR", error_detail=detail)
                self._record_dispatch(
                    channel="sms", recipient=phone_number, message_type=message_type,
                    message=message_body, subscription_status="none",
                    status="FAILED", error_detail=detail)
                return False
        except Exception as e:
            detail = str(e)[:500]
            self._log_sms_delivery(
                phone=phone_number, name=name, message_type=message_type,
                tier="none", status="FAILED", at_status="EXCEPTION",
                error_detail=detail)
            self._record_dispatch(
                channel="sms", recipient=phone_number, message_type=message_type,
                message=message_body, subscription_status="none",
                status="FAILED", error_detail=detail)
            return False

    def _log_sms_delivery(self, *, phone, name, message_type, tier, status,
                          http_status=None, at_status=None, cost=None,
                          message_id=None, error_detail=None):
        """Emits a structured, greppable delivery report to the console and
        persists it to sms_delivery_logs so SMS successes/failures can be
        debugged from the admin workspace. Never raises - reporting must not
        break the send flow."""
        logger.info(
            "SMS DELIVERY status=%s phone=%s name=%r type=%s tier=%s "
            "http=%s at_status=%r cost=%s msg_id=%s error=%s",
            status, phone, name, message_type, tier, http_status,
            at_status, cost, message_id, error_detail,
        )
        try:
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO sms_delivery_logs
                        (phone_number, name, message_type, tier, status, http_status,
                         at_status, cost, message_id, error_detail)
                    VALUES (:p, :n, :mt, :tier, :s, :h, :at, :c, :mid, :e)
                """), {
                    "p": phone[:20], "n": name[:100], "mt": message_type[:30],
                    "tier": tier[:20], "s": status[:20], "h": http_status,
                    "at": (at_status or "")[:40], "c": (cost or "")[:20],
                    "mid": (message_id or "")[:60], "e": (error_detail or "")[:500],
                })
        except Exception as log_err:
            logger.warning("[SMS DELIVERY] could not persist report row: %s", log_err)

    def _send_email_raw(self, recipient_email: str, subject: str, body_html: str):
        """Sends a raw email without tier logic (for notifications)."""
        if not self.smtp_password:
            logger.info("[EMAIL RAW SIMULATION] To: %s -> %s", recipient_email, subject)
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="notification",
                message=subject, subscription_status="none",
                status="SIMULATED",
                error_detail="SMTP password empty - email simulated, not sent")
            return True

        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body_html, 'html'))

            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.smtp_password)
            server.sendmail(self.sender_email, recipient_email, msg.as_string())
            server.quit()
            logger.info("Email raw sent to %s: %s", recipient_email, subject)
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="notification",
                message=subject, subscription_status="none",
                status="SUCCESS")
            return True
        except Exception as e:
            logger.error("Email raw send error: %s", e)
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="notification",
                message=subject, subscription_status="none",
                status="FAILED", error_detail=str(e)[:500])
            return False

    def _record_dispatch(self, *, channel, recipient, message_type, message,
                         subscription_status, status, error_detail=None):
        """
        Persists a row to alert_dispatch_logs so every dispatched SMS/email is
        tracked with its recipient (phone number or email), the exact message
        that was sent, and the recipient's subscription status at dispatch time.
        Never raises - reporting must not break the send flow.
        """
        logger.info(
            "DISPATCH LOG channel=%s recipient=%s type=%s sub=%s status=%s error=%s",
            channel, recipient, message_type, subscription_status, status, error_detail,
        )
        try:
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alert_dispatch_logs
                        (dispatch_code, channel, recipient, message_type,
                         message_content, subscription_status, status, error_detail)
                    VALUES (:code, :ch, :rec, :mt, :msg, :sub, :s, :e)
                """), {
                    "code": new_dispatch_code(),
                    "ch": channel[:10],
                    "rec": recipient[:120],
                    "mt": (message_type or "alert")[:30],
                    "msg": (message or "")[:5000],
                    "sub": (subscription_status or "unknown")[:30],
                    "s": status[:20],
                    "e": (error_detail or "")[:500],
                })
        except Exception as log_err:
            logger.warning("[DISPATCH LOG] could not persist dispatch row: %s", log_err)