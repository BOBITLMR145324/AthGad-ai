import os
import smtplib
import requests
import urllib3
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import quote
from dotenv import load_dotenv
from sqlalchemy import text
from requests.adapters import HTTPAdapter, Retry
from core.db_helper import get_db_engine
from core.id_codes import new_dispatch_code

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
        self.at_is_sandbox = os.getenv("AT_IS_SANDBOX", "false").lower() == "true" or self.at_username == "sandbox"
        # Dynamically switch API base URL based on environment mode
        if self.at_is_sandbox:
            self.at_api_base = "https://api.sandbox.africastalking.com"
        else:
            self.at_api_base = "https://api.africastalking.com"

        # Africa's Talking API readiness flag
        self._at_ready = bool(self.at_api_key and self.at_username)
        if self._at_ready:
            print(f"✅ Africa's Talking REST API configured (mode: {'sandbox' if self.at_is_sandbox else 'production'}, username: {self.at_username})")
            # Sanity checks: catch the most common credential mistakes early.
            key_looks_sandbox = "sandbox" in (self.at_api_key or "").lower()
            if key_looks_sandbox and not self.at_is_sandbox:
                print(f"⚠️  AT_API_KEY looks like a SANDBOX key but AT_IS_SANDBOX is not 'true'.")
                print(f"   ↪ Set AT_IS_SANDBOX=true in config/.env (and use username 'sandbox') or use the PRODUCTION key.")
            elif not key_looks_sandbox and self.at_is_sandbox and self.at_username != "sandbox":
                print(f"⚠️  Sandbox mode is enabled but AT_USERNAME is '{self.at_username}' (usually 'sandbox' on the sandbox).")
        elif not self.at_api_key:
            print(f"⚠️  Africa's Talking API key (AT_API_KEY) is missing in config/.env — SMS alerts will be simulated.")
        elif not self.at_username:
            print(f"⚠️  Africa's Talking username (AT_USERNAME) is missing in config/.env — SMS alerts will be simulated.")

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
            print(f"⚠️  APP_BASE_URL not set in config/.env — email links point to {self.base_url}.")
            print(f"   ↪ Set APP_BASE_URL to your real domain in production or the Unsubscribe link "
                  f"inside alert emails will not work for recipients.")

    # ------------------------------------------------------------------
    # TIERED RISK ALERT DISPATCH
    # ------------------------------------------------------------------
    def dispatch_critical_notification(self, county: str, risk_payload: dict):
        """
        Evaluates regional anomalies and dispatches alerts to ALL users.
        Validates user trial periods and payment statuses against PostgreSQL.
        Channels are evaluated independently: a channel receives Premium details
        ONLY if the user has an active subscription or trial AND that channel is enabled.
        Otherwise, it falls back to the limited baseline alert.
        """
        score = risk_payload.get("composite_risk_score", "0.0%")
        level = risk_payload.get("risk_level", "Low")
        metrics = risk_payload.get("metrics", {})
        calamity = risk_payload.get("calamity_type", "Weather Anomaly")
        # Extract advisory blueprint for tiered content
        advisory = risk_payload.get("advisory", {})
        risk_advisory = {
            "cascading_effects": advisory.get("cascading_effects", []) if isinstance(advisory, dict) else [],
            "proactive_solutions": advisory.get("proactive_solutions", []) if isinstance(advisory, dict) else []
        }

        print(f"\n[ALERT ENGINE - {datetime.now()}] Evaluating risk vector for {county} County...")

        # System Threshold Rule: Only trigger alerts for abnormal operational states
        if level not in ["Medium", "High"]:
            print(f"Alert Engine: Current Risk level ({level}) is nominal. Dispatches suppressed.")
            return False

        if not self.engine:
            print("Alert Engine Error: Database engine context missing. Cannot verify subscribers.")
            return False

        try:
            # Fetch user vectors including payment status, trial expiration flags,
            # and the county they are interested in (for per-county targeting).
            # Users with a NULL/empty county still receive all alerts (backward
            # compatible), while users with a county set only receive alerts for
            # that specific county.
            with self.engine.connect() as connection:
                rows = connection.execute(text("""
                    SELECT full_name, email, phone_number, is_subscribed,
                           subscribe_sms, subscribe_email, payment_status,
                           trial_ends_at, county, dispatch_preference
                    FROM users;
                """)).fetchall()

            # Per-county targeting: include a user if they have no specific
            # county (legacy behaviour) OR if their county matches the alert.
            subscribers = [
                r for r in rows
                if not (getattr(r, 'county', None) or '').strip()
                or (getattr(r, 'county', None) or '').strip().lower() == county.lower()
            ]

            if not subscribers:
                print(f"Alert Engine Notice: No matching users found in database index for {county} County.")
                return True

            print(f"Alert Engine: Broadcasting verified channel-aware alerts for {len(subscribers)} user profiles (target: {county} County)...")

            now = datetime.now()

            for sub in subscribers:
                # ----------------------------------------------------
                # VERIFICATION CHECK: Verification & Tier Validation
                # ----------------------------------------------------
                payment_status = getattr(sub, 'payment_status', 'trialing')
                trial_ends_at = getattr(sub, 'trial_ends_at', None)

                # Check if trial is still active
                is_trial_active = False
                if trial_ends_at:
                    if isinstance(trial_ends_at, str):
                        try:
                            trial_ends_at = datetime.fromisoformat(trial_ends_at)
                        except ValueError:
                            trial_ends_at = None
                    if trial_ends_at and trial_ends_at > now:
                        is_trial_active = True

                # Global Premium Verification: True only for an active payment
                # OR an active free trial. A bare registration (is_subscribed)
                # without trial/payment stays on the free BASELINE tier so those
                # users still receive the re-subscription encouragement.
                account_is_premium = (payment_status == 'active') or is_trial_active

                # Check individual channel opt-ins
                opt_sms = getattr(sub, 'subscribe_sms', False) in [True, 1, 'True', '1']
                opt_email = getattr(sub, 'subscribe_email', False) in [True, 1, 'True', '1']

                # Channel Premium Status: True ONLY if verified account is premium AND channel is opted-in
                sms_is_premium = account_is_premium and opt_sms
                email_is_premium = account_is_premium and opt_email

                # Unsubscribed members (all channels off) still receive the
                # baseline safety alert so critical county warnings are never
                # missed and they're encouraged to re-subscribe. The fallback
                # channel uses their saved dispatch preference. This matches the
                # promise on the unsubscribe pages: "Important safety warnings
                # for your county will still be sent when needed."
                if not opt_sms and not opt_email:
                    pref = (getattr(sub, 'dispatch_preference', 'sms') or 'sms').strip().lower()
                    if pref == 'email' and (getattr(sub, 'email', None) or '').strip():
                        opt_email = True
                    else:
                        opt_sms = True

                # Dispatch on opted-in channels; unsubscribed members get the
                # baseline fallback channel selected above.
                if opt_sms:
                    # Channel 1: SMS Delivery Pipeline (Africa's Talking)
                    sms_delivered = self._send_at_sms(
                        sub.phone_number, sub.full_name, county, level, calamity,
                        is_premium=sms_is_premium, risk_advisory=risk_advisory)

                    # CHANNEL FAILOVER: If the SMS gateway rejected the message
                    # (no credit, bad auth, unreachable), still deliver the alert
                    # via the member's email so they are never left without a
                    # warning. Content tier follows the account's premium status.
                    if not sms_delivered and (getattr(sub, 'email', None) or '').strip():
                        print(f"⚠️  SMS delivery failed for {sub.full_name}; failing over to email {sub.email}")
                        self._send_smtp_email(
                            sub.email, sub.full_name, county, level, score, calamity, metrics,
                            is_premium=account_is_premium, risk_advisory=risk_advisory)

                if opt_email:
                    # Channel 2: EMAIL Delivery Pipeline
                    email_delivered = self._send_smtp_email(
                        sub.email, sub.full_name, county, level, score, calamity, metrics,
                        is_premium=email_is_premium, risk_advisory=risk_advisory)

                    # CHANNEL FAILOVER: If SMTP failed, try the member's phone.
                    if not email_delivered and (getattr(sub, 'phone_number', None) or '').strip():
                        print(f"⚠️  Email delivery failed for {sub.full_name}; failing over to SMS {sub.phone_number}")
                        self._send_at_sms(
                            sub.phone_number, sub.full_name, county, level, calamity,
                            is_premium=account_is_premium, risk_advisory=risk_advisory)

            return True

        except Exception as e:
            print(f"Alert Engine infrastructure processing failure: {e}")
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
                f"Reply STOP to stop receiving these alerts."
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
            if self.at_sender_id:
                payload["from"] = self.at_sender_id

            url = f"{self.at_api_base}/version1/messaging"

            session = requests.Session()
            session.trust_env = False
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST"]
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            headers["User-Agent"] = "AthGadAI/1.0"

            response = session.post(
                url,
                headers=headers,
                data=payload,
                timeout=30,
                verify=self.ssl_verify,
                proxies={"http": None, "https": None}
            )

            if response.status_code in [200, 201]:
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
                            tier=tier, status="SUCCESS", http_status=response.status_code,
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
                            tier=tier, status="FAILED", http_status=response.status_code,
                            at_status=status, cost=cost, message_id=msg_id,
                            error_detail=detail)
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type="alert",
                            message=message_body, subscription_status=subscription_status,
                            status="FAILED", error_detail=detail)
                        return False
                else:
                    detail = f"AT response missing Recipients: {str(resp_json)[:200]}"
                    self._log_sms_delivery(
                        phone=phone_number, name=name, message_type="alert",
                        tier=tier, status="FAILED", http_status=response.status_code,
                        at_status="NO_RECIPIENTS", error_detail=detail)
                    self._record_dispatch(
                        channel="sms", recipient=phone_number, message_type="alert",
                        message=message_body, subscription_status=subscription_status,
                        status="FAILED", error_detail=detail)
                    return False
            elif response.status_code == 401:
                detail = "invalid AT_API_KEY/AT_USERNAME (see fix guidance below)"
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type="alert",
                    tier="premium" if is_premium else "baseline", status="FAILED",
                    http_status=401, at_status="AUTH_FAILURE", error_detail=detail)
                print(f"   ↪ The AT_API_KEY in config/.env does not match AT_USERNAME='{self.at_username}' for the {self.at_api_base} environment.")
                print(f"   ↪ Fix: regenerate/update AT_API_KEY at https://account.africastalking.com/apps/sandbox/keys")
                print(f"         (sandbox)  or https://account.africastalking.com/apps/prod/keys (production).")
                print(f"   ↪ Sandbox mode requires AT_IS_SANDBOX=true and the SANDBOX key. Production needs the PRODUCTION key.")
                self._record_dispatch(
                    channel="sms", recipient=phone_number, message_type="alert",
                    message=message_body, subscription_status=subscription_status,
                    status="FAILED", error_detail=detail)
                return False
            else:
                detail = response.text[:200]
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type="alert",
                    tier="premium" if is_premium else "baseline", status="FAILED",
                    http_status=response.status_code, at_status="HTTP_ERROR",
                    error_detail=detail)
                self._record_dispatch(
                    channel="sms", recipient=phone_number, message_type="alert",
                    message=message_body, subscription_status=subscription_status,
                    status="FAILED", error_detail=detail)
                return False

        except requests.exceptions.SSLError as ssl_err:
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
        except requests.exceptions.ConnectionError as conn_err:
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
        except requests.exceptions.Timeout as timeout_err:
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

        if is_premium:
            subject = f"AthGad Alert: {level} Risk in {county} County"
            headline = "Important Safety Advisory"
            description = f"Hello {name}, our monitoring system has detected rising risk in your area. Here is what you need to know and how to stay safe."

            metrics_html = f"""
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; font-weight: bold; color: #94a3b8;">County:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ffffff; text-align: right;">{county} County</td>
                </tr>
                <tr>
                    <td style="padding: 10px; color: #94a3b8;">Main Threat:</td>
                    <td style="padding: 10px; color: #f1f5f9; text-align: right;">{calamity}</td>
                </tr>
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; color: #94a3b8;">Risk Level:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ef4444; text-align: right;">{score} ({level})</td>
                </tr>
            """

            cascading_html = ""
            if cascading_list:
                items = "".join(f"<li>{item}</li>" for item in cascading_list[:4])
                cascading_html = f"""
                    <h4 style="color: #ffffff; margin-bottom: 8px; font-size: 13px; text-transform: uppercase;">Possible Impacts:</h4>
                    <ul style="font-size: 12px; color: #94a3b8; padding-left: 20px; line-height: 1.8; margin-top: 0;">
                        {items}
                    </ul>
                """

            proactive_html = ""
            if proactive_list:
                items = "".join(f"<li>{item}</li>" for item in proactive_list[:4])
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
            subject = f"AthGad Baseline Advisory: Safety Anomaly detected in {county} County"
            headline = "Baseline Environmental Safety Warning"
            description = f"Hello {name}, AthGad sensors have flagged an operational climate anomaly in {county} County reaching a <strong>{level.upper()}</strong> alert state."

            metrics_html = f"""
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; font-weight: bold; color: #94a3b8;">Observation Target:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ffffff; text-align: right;">{county} County</td>
                </tr>
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; color: #94a3b8;">Risk Evaluation Level:</td>
                    <td style="padding: 10px; font-weight: bold; color: #eab308; text-align: right;">{level} Risk State</td>
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
            print(f"Alert Engine Notice: SMTP key empty. Simulated email data block for {recipient_email}.")
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
            msg.attach(MIMEText(body_html, 'html'))

            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.smtp_password)
            server.sendmail(self.sender_email, recipient_email, msg.as_string())
            server.quit()

            print(f"EMAIL SUCCESS: Telemetry layout dispatched to {recipient_email}. Tier: {'Premium' if is_premium else 'Free Baseline'}")
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="alert",
                message=subject, subscription_status="premium" if is_premium else "baseline",
                status="SUCCESS")
            return True
        except Exception as e:
            print(f"EMAIL ERROR: Failed to send to {recipient_email}: {e}")
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
                f"Visit {self.base_url}/subscribe to manage your subscription. "
                f"Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg, message_type="trial", name=name)

        if opt_email and email:
            subject = (
                "AthGad AI: Premium Trial Restored - Remaining Days Carry Over"
                if carried_over_days > 0
                else "AthGad AI: Your Free Trial Has Started"
            )
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">{("Welcome Back to Premium, " if carried_over_days > 0 else "Welcome to AthGad Premium, ")}{name}!</h2>
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
                f"Visit AthGad.ai/subscribe to re-subscribe and pay 150 KES/month via M-PESA. "
                f"Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg, message_type="trial_expired", name=name)

        if opt_email and email:
            subject = "AthGad AI: Your Free Trial Has Ended"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Trial Period Ended, {name}</h2>
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
        next_payment = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        if opt_sms and phone_number:
            msg = (
                f"AthGad AI: Thank you {name} for your payment of 150 KES! "
                f"Your premium subscription is now active. "
                f"Your next payment of 150 KES will be due on {next_payment}. "
                f"Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg, message_type="payment", name=name)

        if opt_email and email:
            subject = "AthGad AI: Payment Successful - Thank You!"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">Payment Successful, {name}!</h2>
                    <p style="color: #94a3b8;">Thank you for your payment of <strong>150 KES</strong>.</p>
                    <p style="color: #94a3b8;">Your premium subscription is now active.</p>
                    <p style="color: #eab308;">Your next payment of <strong>150 KES</strong> will be due on <strong>{next_payment}</strong>.</p>
                    <p style="color: #94a3b8;">Manage your subscription at <a href="{self.base_url}/subscribe" style="color: #38bdf8;">AthGad.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

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
                f"We're sorry to see you go. If you're willing, please tell us why: "
                f"Reply 1 for 'Too many messages', 2 for 'Not useful', 3 for 'Too expensive', "
                f"or type your own reason. We value your feedback! "
                f"{rejoin_encouragement} Visit {self.base_url}/subscribe to come back."
            )
            self._send_sms_raw(phone_number, msg, message_type="unsubscribe", name=name)

        if channel == "email" and opt_email and email:
            subject = "AthGad AI: You Have Been Unsubscribed"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Unsubscribe Confirmed, {name}</h2>
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
                      message_type: str = "notification", name: str = ""):
        """Sends a raw SMS without tier logic (for notifications)."""
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
            if self.at_sender_id:
                payload["from"] = self.at_sender_id

            url = f"{self.at_api_base}/version1/messaging"
            session = requests.Session()
            session.trust_env = False
            retry_strategy = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["POST"])
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            headers["User-Agent"] = "AthGadAI/1.0"

            response = session.post(url, headers=headers, data=payload, timeout=15, verify=self.ssl_verify, proxies={"http": None, "https": None})
            if response.status_code in [200, 201]:
                resp_json = response.json()
                recipients = resp_json.get('SMSMessageData', {}).get('Recipients', [])
                if recipients:
                    r = recipients[0]
                    at_status = r.get('status', 'Unknown')
                    if at_status == "Success":
                        self._log_sms_delivery(
                            phone=phone_number, name=name, message_type=message_type,
                            tier="none", status="SUCCESS", http_status=response.status_code,
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
                            tier="none", status="FAILED", http_status=response.status_code,
                            at_status=at_status, cost=r.get('cost', 'N/A'),
                            message_id=r.get('messageId', 'N/A'), error_detail=detail)
                        self._record_dispatch(
                            channel="sms", recipient=phone_number, message_type=message_type,
                            message=message_body, subscription_status="none",
                            status="FAILED", error_detail=detail)
                        return False
                else:
                    detail = f"AT response missing Recipients: {str(resp_json)[:200]}"
                    self._log_sms_delivery(
                        phone=phone_number, name=name, message_type=message_type,
                        tier="none", status="FAILED", http_status=response.status_code,
                        at_status="NO_RECIPIENTS", error_detail=detail)
                    self._record_dispatch(
                        channel="sms", recipient=phone_number, message_type=message_type,
                        message=message_body, subscription_status="none",
                        status="FAILED", error_detail=detail)
                    return False
            else:
                detail = response.text[:200]
                self._log_sms_delivery(
                    phone=phone_number, name=name, message_type=message_type,
                    tier="none", status="FAILED", http_status=response.status_code,
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
        print(
            f"📱 [SMS DELIVERY] status={status} phone={phone} name={name!r} "
            f"type={message_type} tier={tier} http={http_status} "
            f"at_status={at_status!r} cost={cost} msg_id={message_id} "
            f"error={error_detail}"
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
            print(f"⚠️ [SMS DELIVERY] could not persist report row: {log_err}")

    def _send_email_raw(self, recipient_email: str, subject: str, body_html: str):
        """Sends a raw email without tier logic (for notifications)."""
        if not self.smtp_password:
            print(f"📧 [EMAIL RAW SIMULATION] To: {recipient_email} -> {subject}")
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
            print(f"✅ Email raw sent to {recipient_email}: {subject}")
            self._record_dispatch(
                channel="email", recipient=recipient_email, message_type="notification",
                message=subject, subscription_status="none",
                status="SUCCESS")
            return True
        except Exception as e:
            print(f"❌ Email raw send error: {e}")
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
        print(
            f"📨 [DISPATCH LOG] channel={channel} recipient={recipient} "
            f"type={message_type} sub={subscription_status} status={status} "
            f"error={error_detail}"
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
            print(f"⚠️ [DISPATCH LOG] could not persist dispatch row: {log_err}")
