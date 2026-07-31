import os
import smtplib
import requests
import urllib3
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import text
from requests.adapters import HTTPAdapter, Retry

# Suppress SSL warnings for sandbox/dev environments (verify=False usage)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment configuration parameters with robust path resolution
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
load_dotenv(dotenv_path)

class EarthGuardAlertService:
    def __init__(self, db_engine=None):
        # Database Engine Context Binding
        self.engine = db_engine

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

        # SMTP Email Outbound Credentials
        self.sender_email = os.getenv("GMAIL_SENDER", os.getenv("ALERT_SENDER_EMAIL", "alerts@earthguard.ai"))
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.smtp_password = os.getenv("GMAIL_APP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))

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

        print(f"\n[ALERT ENGINE - {datetime.now()}] Evaluating risk vector for {county} County...")

        # System Threshold Rule: Only trigger alerts for abnormal operational states
        if level not in ["Medium", "High"]:
            print(f"Alert Engine: Current Risk level ({level}) is nominal. Dispatches suppressed.")
            return False

        if not self.engine:
            print("Alert Engine Error: Database engine context missing. Cannot verify subscribers.")
            return False

        try:
            # Fetch user vectors including payment verification status and trial expiration flags
            with self.engine.connect() as connection:
                subscribers = connection.execute(text("""
                    SELECT full_name, email, phone_number, is_subscribed,
                           subscribe_sms, subscribe_email, payment_status, trial_ends_at
                    FROM users;
                """)).fetchall()

            if not subscribers:
                print(f"Alert Engine Notice: No registered users found in database index.")
                return True

            print(f"Alert Engine: Broadcasting verified channel-aware alerts for {len(subscribers)} user profiles...")

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

                # Global Premium Verification: True if active payment OR active free trial
                account_is_premium = (payment_status == 'active') or is_trial_active or (
                    getattr(sub, 'is_subscribed', False) in [True, 1, 'True', '1'] and payment_status != 'expired'
                )

                # Check individual channel opt-ins
                opt_sms = getattr(sub, 'subscribe_sms', False) in [True, 1, 'True', '1']
                opt_email = getattr(sub, 'subscribe_email', False) in [True, 1, 'True', '1']

                # Channel Premium Status: True ONLY if verified account is premium AND channel is opted-in
                sms_is_premium = account_is_premium and opt_sms
                email_is_premium = account_is_premium and opt_email

                # Channel 1: SMS Delivery Pipeline (Africa's Talking)
                self._send_at_sms(sub.phone_number, sub.full_name, county, level, calamity, is_premium=sms_is_premium)

                # Channel 2: EMAIL Delivery Pipeline
                self._send_smtp_email(sub.email, sub.full_name, county, level, score, calamity, metrics, is_premium=email_is_premium)

            return True

        except Exception as e:
            print(f"Alert Engine infrastructure processing failure: {e}")
            return False

    def _send_at_sms(self, phone_number: str, name: str, county: str, level: str, calamity: str, is_premium: bool = False):
        """Dispatches automated SMS summary alerts via Africa's Talking with tier-restricted content."""
        phone_number = phone_number.strip()

        # Format phone number for Africa's Talking (international format with +)
        # Accepts: 07XXXXXXXX, +2547XXXXXXXX, 2547XXXXXXXX → normalized to +2547XXXXXXXX
        phone_number = phone_number.replace(' ', '').replace('-', '')
        if phone_number.startswith('+254'):
            pass  # Already in correct format with +
        elif phone_number.startswith('254'):
            phone_number = '+' + phone_number  # 2547... → +2547...
        elif phone_number.startswith('0'):
            phone_number = '+254' + phone_number[1:]  # 07... → +2547...
        elif phone_number.startswith('+'):
            pass  # Other international format, keep as-is

        if not self._at_ready:
            tier_label = "Verified Premium" if is_premium else "Unsubscribed (Free Baseline)"
            print(f"📡 [SMS SIMULATION MODE] Tier: {tier_label} -> To: {name} ({phone_number})")
            return True

        try:
            if is_premium:
                message_body = (
                    f"⚠️ EarthGuard AI [PREMIUM]:\nHello {name}, {county} is at {level.upper()} Risk.\n"
                    f"THREATS: {calamity}.\n"
                    f"Visit www.EarthguardAi.ac.ke for details. Reply STOP to opt out."
                )
            else:
                message_body = (
                    f"⚠️ EarthGuard AI [CRITICAL BASELINE]:\nAnomaly warning for {county} County reaching {level.upper()} Risk.\n"
                    f"Detailed parameters are locked.\n"
                    f"Upgrade at earthguard.ai/upgrade to unlock full real-time telemetry updates."
                )

            # Africa's Talking REST API: Send SMS via Basic Auth
            # ✅ CORRECT (Africa's Talking uses the 'apiKey' custom header)
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

            # Create a session with proxy bypass and retry logic
            # NOTE: verify=False is used for sandbox/dev to bypass SSL interception
            # from corporate proxies, VPNs, or antivirus SSL inspection that causes
            # [SSL: WRONG_VERSION_NUMBER] errors on Windows environments.
            session = requests.Session()

            # Bypass any system-configured HTTP/HTTPS proxies (common cause of SSL WRONG_VERSION_NUMBER)
            session.trust_env = False

            # Mount a retry adapter for resilience against transient network failures
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST"]
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)
            session.mount("http://", adapter)

            # Add User-Agent to avoid generic proxy filtering
            headers["User-Agent"] = "EarthGuardAI/1.0"

            response = session.post(
                url,
                headers=headers,
                data=payload,
                timeout=30,
                verify=False,
                proxies={"http": None, "https": None}
            )

            if response.status_code in [200, 201]:
                resp_json = response.json()
                recipients = resp_json.get('SMSMessageData', {}).get('Recipients', [])
                if recipients:
                    status = recipients[0].get('status')
                    cost = recipients[0].get('cost', 'N/A')
                    msg_id = recipients[0].get('messageId', 'N/A')
                    
                    # Verify Africa's Talking accepted the message for actual dispatch
                    if status == "Success":
                        print(f"✅ SMS queued on network for {name} ({phone_number}). Tier: {'Premium' if is_premium else 'Free Baseline'}. Cost: {cost}, MsgID: {msg_id}")
                        return True
                    else:
                        print(f"⚠️ Africa's Talking Gateway Warning: SMS status is '{status}' for {phone_number}. (Cost: {cost}, MsgID: {msg_id})")
                        return False
                else:
                    print(f"❌ Africa's Talking SMS Failure: No recipients returned in response payload: {resp_json}")
                    return False
            else:
                print(f"❌ Africa's Talking SMS Failure: Status {response.status_code} - {response.text}")
                return False

        except requests.exceptions.SSLError as ssl_err:
            print(f"❌ Africa's Talking SSL Error (WRONG_VERSION_NUMBER resolved by proxy bypass): {ssl_err}")
            print(f"   ↪ If this persists, check your Windows proxy settings or network firewall.")
            print(f"   ↪ Try: netsh winhttp reset proxy  (run as Administrator)")
            return False
        except requests.exceptions.ConnectionError as conn_err:
            print(f"❌ Africa's Talking Connection Error (network unreachable / DNS failure): {conn_err}")
            return False
        except requests.exceptions.Timeout as timeout_err:
            print(f"❌ Africa's Talking Timeout Error (server unreachable after retries): {timeout_err}")
            return False
        except Exception as e:
            print(f"❌ Africa's Talking SMS Exception: {e}")
            return False

    def _send_smtp_email(self, recipient_email: str, name: str, county: str, level: str, score: str, calamity: str, metrics: dict, is_premium: bool = False):
        """Delivers detailed HTML analytical matrices to verified Premium members or basic alerts to Free members."""

        if is_premium:
            subject = f"🚨 PREMIUM ENVIRONMENTAL TELEMETRY: {level} Risk State - {county} County"
            headline = "Automated High-Fidelity Advisory"
            description = f"Hello {name}, our deep learning engine has computed environmental data vectors for your area. Your threshold premium metrics have generated an active containment flag."

            metrics_html = f"""
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; font-weight: bold; color: #94a3b8;">Observation Target:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ffffff; text-align: right;">{county} County</td>
                </tr>
                <tr>
                    <td style="padding: 10px; color: #94a3b8;">Primary Calamity Classification:</td>
                    <td style="padding: 10px; color: #f1f5f9; text-align: right;">{calamity}</td>
                </tr>
                <tr style="background-color: #020617;">
                    <td style="padding: 10px; color: #94a3b8;">Composite Risk Index Evaluation:</td>
                    <td style="padding: 10px; font-weight: bold; color: #ef4444; text-align: right;">{score} ({level})</td>
                </tr>
            """

            subdomains_html = f"""
                <h4 style="color: #ffffff; margin-bottom: 8px; font-size: 13px; text-transform: uppercase;">Sub-Domain Signal Metrics:</h4>
                <ul style="font-size: 12px; color: #94a3b8; padding-left: 20px; line-height: 1.8; margin-top: 0;">
                    <li>Climate Severity Variance: {metrics.get('climate_severity', 'N/A')}</li>
                    <li>Health Vector Cascade Velocity: {metrics.get('health_severity', 'N/A')}</li>
                    <li>Isolation Forest Factor Vector: {metrics.get('hidden_anomaly_factor', 'N/A')}</li>
                </ul>
            """
            action_button = "👉 Action Required: Access your operational dashboard to view active proactive checklists immediately."
            action_color = "#34d399"

            footer_links_html = f"""
                <a href="https://earthguard.ai/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="http://127.0.0.1:5000/unsubscribe?email={recipient_email}" style="color: #ef4444; text-decoration: none;">Unsubscribe from Premium</a>
            """
        else:
            subject = f"⚠️ EarthGuard Baseline Advisory: Safety Anomaly detected in {county} County"
            headline = "Baseline Environmental Safety Warning"
            description = f"Hello {name}, EarthGuard sensors have flagged an operational climate anomaly in {county} County reaching a <strong>{level.upper()}</strong> alert state."

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

            subdomains_html = f"""
                <div style="background-color: #1e1b4b; border: 1px dashed #4338ca; padding: 12px; border-radius: 6px; margin: 15px 0; text-align: center;">
                    <p style="font-size: 12px; color: #c7d2fe; margin: 0;">
                        🔒 <strong>Sub-domain high-fidelity telemetry is locked.</strong> You are receiving baseline critical updates. Upgrade your profile or complete payment to unlock full real-time parameters.
                    </p>
                </div>
            """
            action_button = "⚡ Unlock Real-Time Pipelines: Visit earthguard.ai/upgrade to activate full tracking capabilities."
            action_color = "#38bdf8"

            footer_links_html = f"""
                <a href="http://127.0.0.1:5000/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="http://127.0.0.1:5000/subscribe" style="color: #10b981; font-weight: bold; text-decoration: none;">✨ Upgrade to Premium</a>
            """

        body_html = f"""
        <html>
        <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 30px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                <div style="margin-bottom: 20px;">
                    <span style="height: 10px; width: 10px; background-color: #10b981; display: inline-block; border-radius: 50%; margin-right: 6px;"></span>
                    <strong style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 16px; color: #ffffff;">EarthGuard AI System</strong>
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

                {subdomains_html}

                <p style="font-size: 13px; color: {action_color}; font-weight: bold; margin-top: 24px;">
                    {action_button}
                </p>

                <p style="font-size: 11px; color: #64748b; text-align: center; margin-top: 30px; border-top: 1px solid #1e293b; padding-top: 16px;">
                    Sent by EarthGuard AI System. <br><br>
                    {footer_links_html}
                </p>
            </div>
        </body>
        </html>
        """

        if not self.smtp_password:
            print(f"Alert Engine Notice: SMTP key empty. Simulated email data block for {recipient_email}.")
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

            print(f"📧 [EMAIL SUCCESS] Telemetry layout dispatched cleanly to {recipient_email}. Tier: {'Verified Premium' if is_premium else 'Free Baseline'}")
            return True
        except Exception as e:
            print(f"❌ [EMAIL ERROR] Failed to send to {recipient_email}: {e}")
            return False
