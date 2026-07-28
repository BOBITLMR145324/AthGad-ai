import os
import smtplib
import requests
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import text

# Load environment configuration parameters
load_dotenv('config/.env')

class EarthGuardAlertService:
    def __init__(self, db_engine=None):
        # Database Engine Context Binding
        self.engine = db_engine

        # Infobip Configuration Gateways
        self.infobip_api_key = os.getenv("INFOBIP_API_KEY", "")
        self.infobip_base_url = os.getenv("INFOBIP_BASE_URL", "ee2n63.api.infobip.com")
        self.infobip_sender = os.getenv("INFOBIP_SENDER", "EarthGuard")

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

                # Channel 1: SMS Delivery Pipeline (Infobip)
                self._send_infobip_sms(sub.phone_number, sub.full_name, county, level, calamity, is_premium=sms_is_premium)
                
                # Channel 2: EMAIL Delivery Pipeline
                self._send_smtp_email(sub.email, sub.full_name, county, level, score, calamity, metrics, is_premium=email_is_premium)

            return True

        except Exception as e:
            print(f"Alert Engine infrastructure processing failure: {e}")
            return False

    def _send_infobip_sms(self, phone_number: str, name: str, county: str, level: str, calamity: str, is_premium: bool = False):
        """Dispatches automated SMS summary alerts via Infobip with tier-restricted content."""
        phone_number = phone_number.strip()
        
        # Format phone number for Infobip (international format without +)
        phone_number = phone_number.replace(' ', '').replace('-', '')
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+254'):
            phone_number = '254' + phone_number[4:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]

        if not self.infobip_api_key:
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

            # Infobip API: Send SMS via REST API
            url = f"https://{self.infobip_base_url}/sms/2/text/advanced"
            headers = {
                "Authorization": f"App {self.infobip_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "messages": [
                    {
                        "destinations": [{"to": phone_number}],
                        "from": self.infobip_sender,
                        "text": message_body
                    }
                ]
            }

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code in [200, 201]:
                response_data = response.json()
                message_id = response_data.get('messages', [{}])[0].get('messageId', 'N/A')
                print(f"✅ SMS successfully broadcasted to {name} ({phone_number}). Tier: {'Verified Premium' if is_premium else 'Free Baseline'}. MsgID: {message_id}")
                return True
            else:
                print(f"❌ Infobip SMS Failure: Status Code {response.status_code} - {response.text}")
                return False

        except Exception as e:
            print(f"❌ Infobip SMS Exception: {e}")
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
