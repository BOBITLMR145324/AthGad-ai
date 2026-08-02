import os
import smtplib
import requests
import urllib3
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
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
                self._send_at_sms(sub.phone_number, sub.full_name, county, level, calamity,
                                  is_premium=sms_is_premium, risk_advisory=risk_advisory)

                # Channel 2: EMAIL Delivery Pipeline
                self._send_smtp_email(sub.email, sub.full_name, county, level, score, calamity, metrics,
                                      is_premium=email_is_premium, risk_advisory=risk_advisory)

            return True

        except Exception as e:
            print(f"Alert Engine infrastructure processing failure: {e}")
            return False

    # ------------------------------------------------------------------
    # SMS DISPATCH (tiered content)
    # ------------------------------------------------------------------
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

        if not self._at_ready:
            tier_label = "Verified Premium" if is_premium else "Unsubscribed (Free Baseline)"
            print(f"📡 [SMS SIMULATION MODE] Tier: {tier_label} -> To: {name} ({phone_number})")
            return True

        try:
            if is_premium:
                # Summarize cascading effects and proactive measures for SMS (keep concise)
                cascading_list = risk_advisory.get("cascading_effects", []) or []
                proactive_list = risk_advisory.get("proactive_solutions", []) or []
                cascading_text = "; ".join(cascading_list[:3]) if cascading_list else "Monitor local advisories."
                proactive_text = "; ".join(proactive_list[:2]) if proactive_list else "Stay safe and follow local guidance."
                message_body = (
                    f"EarthGuard AI: Hello {name}! {county} County is at {level.upper()} RISK. "
                    f"Threat: {calamity}. "
                    f"Possible impacts: {cascading_text} "
                    f"What to do: {proactive_text} "
                    f"Reply STOP to stop receiving these alerts."
                )
            else:
                message_body = (
                    f"EarthGuard AI: Safety alert for {county} County. "
                    f"The risk level is {level.upper()}. "
                    f"More details are available with a paid subscription. "
                    f"Sign up at earthguard.ai/subscribe to get full alerts and safety advice."
                )

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
            subject = f"EarthGuard Alert: {level} Risk in {county} County"
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
                <a href="https://earthguard.ai/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="http://127.0.0.1:5000/unsubscribe?email={recipient_email}" style="color: #ef4444; text-decoration: none;">Unsubscribe from Premium</a>
            """
        else:
            subject = f"EarthGuard Baseline Advisory: Safety Anomaly detected in {county} County"
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

            cascading_html = ""
            proactive_html = ""

            action_button = "Unlock Real-Time Pipelines: Visit earthguard.ai/upgrade to activate full tracking capabilities."
            action_color = "#38bdf8"

            footer_links_html = f"""
                <a href="http://127.0.0.1:5000/dashboard" style="color: #38bdf8; text-decoration: none;">Command Interface</a> |
                <a href="http://127.0.0.1:5000/subscribe" style="color: #10b981; font-weight: bold; text-decoration: none;">Upgrade to Premium</a>
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

                {cascading_html}
                {proactive_html}

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

            print(f"EMAIL SUCCESS: Telemetry layout dispatched to {recipient_email}. Tier: {'Premium' if is_premium else 'Free Baseline'}")
            return True
        except Exception as e:
            print(f"EMAIL ERROR: Failed to send to {recipient_email}: {e}")
            return False

    # ------------------------------------------------------------------
    # NOTIFICATION HELPERS (trial, unsubscribe, payment)
    # ------------------------------------------------------------------
    def send_trial_notice(self, name: str, phone_number: str, email: str, trial_end_date: str,
                          opt_sms: bool = False, opt_email: bool = False):
        """Notifies the user that their 30-day free trial has started."""
        if opt_sms and phone_number:
            msg = (
                f"EarthGuard AI: Welcome {name}! Your 30-day free trial has started. "
                f"You will receive full premium alerts until {trial_end_date}. "
                f"After that, a fee of 150 KES/month via M-PESA applies. "
                f"Visit earthguard.ai/subscribe to manage your subscription. Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg)

        if opt_email and email:
            subject = "EarthGuard AI: Your 30-Day Free Trial Has Started"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">Welcome to EarthGuard Premium, {name}!</h2>
                    <p style="color: #94a3b8;">Your 30-day free trial is now active.</p>
                    <p style="color: #94a3b8;">You will receive full premium alerts until <strong>{trial_end_date}</strong>.</p>
                    <p style="color: #eab308;">After the trial, a fee of <strong>150 KES/month</strong> via M-PESA will apply to continue receiving premium alerts.</p>
                    <p style="color: #94a3b8;">Manage your subscription at <a href="http://127.0.0.1:5000/subscribe" style="color: #38bdf8;">earthguard.ai/subscribe</a>.</p>
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
                f"EarthGuard AI: Hello {name}, your 30-day free trial has ended. "
                f"You have been unsubscribed from premium alerts. "
                f"Visit earthguard.ai/subscribe to re-subscribe and pay 150 KES/month via M-PESA. "
                f"Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg)

        if opt_email and email:
            subject = "EarthGuard AI: Your Free Trial Has Ended"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Trial Period Ended, {name}</h2>
                    <p style="color: #94a3b8;">Your 30-day free trial has concluded and you have been unsubscribed from premium alerts.</p>
                    <p style="color: #eab308;">To continue receiving full premium alerts, please re-subscribe and pay <strong>150 KES/month</strong> via M-PESA.</p>
                    <p style="color: #94a3b8;"><a href="http://127.0.0.1:5000/subscribe" style="color: #38bdf8;">Click here to re-subscribe</a></p>
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
                f"EarthGuard AI: Thank you {name} for your payment of 150 KES! "
                f"Your premium subscription is now active. "
                f"Your next payment of 150 KES will be due on {next_payment}. "
                f"Reply STOP to opt out."
            )
            self._send_sms_raw(phone_number, msg)

        if opt_email and email:
            subject = "EarthGuard AI: Payment Successful - Thank You!"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #10b981;">Payment Successful, {name}!</h2>
                    <p style="color: #94a3b8;">Thank you for your payment of <strong>150 KES</strong>.</p>
                    <p style="color: #94a3b8;">Your premium subscription is now active.</p>
                    <p style="color: #eab308;">Your next payment of <strong>150 KES</strong> will be due on <strong>{next_payment}</strong>.</p>
                    <p style="color: #94a3b8;">Manage your subscription at <a href="http://127.0.0.1:5000/subscribe" style="color: #38bdf8;">earthguard.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    def send_unsubscribe_confirmation(self, name: str, phone_number: str, email: str, channel: str,
                                      opt_sms: bool = False, opt_email: bool = False):
        """Sends a polite confirmation requesting a reason for unsubscription."""
        if channel == "sms" and opt_sms and phone_number:
            msg = (
                f"EarthGuard AI: Hello {name}, you have been unsubscribed from SMS alerts. "
                f"We're sorry to see you go. If you're willing, please tell us why: "
                f"Reply 1 for 'Too many messages', 2 for 'Not useful', 3 for 'Too expensive', "
                f"or type your own reason. We value your feedback!"
            )
            self._send_sms_raw(phone_number, msg)

        if channel == "email" and opt_email and email:
            subject = "EarthGuard AI: You Have Been Unsubscribed"
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; background-color: #020617; color: #f8fafc; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background-color: #0f172a; border: 1px solid #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #ef4444;">Unsubscribe Confirmed, {name}</h2>
                    <p style="color: #94a3b8;">You have been unsubscribed from email alerts.</p>
                    <p style="color: #94a3b8;">We're sorry to see you go. If you're willing, please tell us why by visiting the link below:</p>
                    <p style="text-align: center;">
                        <a href="http://127.0.0.1:5000/unsubscribe/reason?email={email}" 
                           style="display: inline-block; background-color: #1e293b; color: #f8fafc; padding: 10px 20px; border-radius: 8px; text-decoration: none;">
                           Share Your Feedback
                        </a>
                    </p>
                    <p style="color: #64748b; font-size: 12px;">If you change your mind, you can re-subscribe at <a href="http://127.0.0.1:5000/subscribe" style="color: #38bdf8;">earthguard.ai/subscribe</a>.</p>
                </div>
            </body>
            </html>
            """
            self._send_email_raw(email, subject, body_html)

    # ------------------------------------------------------------------
    # RAW SEND HELPERS (no tier logic)
    # ------------------------------------------------------------------
    def _send_sms_raw(self, phone_number: str, message_body: str):
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
            print(f"📡 [SMS RAW SIMULATION] To: {phone_number} -> {message_body[:60]}...")
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
            headers["User-Agent"] = "EarthGuardAI/1.0"

            response = session.post(url, headers=headers, data=payload, timeout=15, verify=False, proxies={"http": None, "https": None})
            if response.status_code in [200, 201]:
                print(f"✅ SMS raw sent to {phone_number}")
                return True
            else:
                print(f"⚠️ SMS raw send failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ SMS raw send error: {e}")
            return False

    def _send_email_raw(self, recipient_email: str, subject: str, body_html: str):
        """Sends a raw email without tier logic (for notifications)."""
        if not self.smtp_password:
            print(f"📧 [EMAIL RAW SIMULATION] To: {recipient_email} -> {subject}")
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
            return True
        except Exception as e:
            print(f"❌ Email raw send error: {e}")
            return False
