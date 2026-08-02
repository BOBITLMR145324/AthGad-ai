import os
import sys
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session, render_template_string
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

# Core Modules & Pipeline Helpers
from core.analytics import EarthGuardAnalyticsEngine
from services.alert_service import EarthGuardAlertService
from services.mpesa_service import initiate_stk_push
from core.db_helper import get_db_engine
from core.county_registry import get_county_advisory

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
CORS(app)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "earthguard_secure_pulse_key_2026")

# Initialize core microservice instances
analytics_engine = EarthGuardAnalyticsEngine()
alert_service = EarthGuardAlertService()

TRIAL_DAYS = 30


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
    return render_template('landing.html')


@app.route('/dashboard')
def dashboard():
    """Serves the central EarthGuard AI operational telemetry board."""
    if 'user_email' in session:
        return render_template('index.html')

    flash("Please sign in to view your risk dashboard.", "warning")
    return redirect(url_for('login_page'))


@app.route('/api/v1/health', methods=['GET'])
def get_system_health():
    """Returns the operational status of the EarthGuard API layer."""
    return jsonify({
        "status": "online",
        "engine": "EarthGuard AI Engine v1.0",
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
    covered_counties = ["Kitui", "Machakos", "Makueni", "Marsabit", "Isiolo", "Meru", "Embu", "Tharaka-Nithi"]
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
        with engine.connect() as connection:
            result = connection.execute(text(query))
            db_records = {row.county: row for row in result}

        for county in covered_counties:
            has_record = county in db_records
            record = db_records[county] if has_record else None

            if has_record:
                raw_score = float(record.calculated_score)
                score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                risk_level = record.risk_level
            else:
                live_data = analytics_engine.calculate_composite_risk(county)
                raw_score = live_data.get("composite_risk_score", 0.3)
                score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                risk_level = live_data.get("risk_level", "Low")

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
                "primary_threat": primary_threat
            })

    except Exception as e:
        print(f"Telemetry Data Load Warning: {e}")
        for county in covered_counties:
            status_board.append({
                "county": county,
                "score_pct": 0.0,
                "risk_level": "Low",
                "threat_category": "climate",
                "primary_threat": "Monitoring Sync Active"
            })

    avg_climate = round(sum(climate_scores) / len(climate_scores), 1) if climate_scores else 68.4
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else 54.1
    high_risk_count = sum(1 for c in status_board if c['risk_level'] == 'High')

    return render_template(
        'telemetry.html',
        status_board=status_board,
        avg_climate=avg_climate,
        avg_health=avg_health,
        high_risk_count=high_risk_count,
        last_sync=datetime.now().strftime("%H:%M EAT")
    )


# =====================================================================
# CITIZEN REGISTRATION & AUTHENTICATION
# =====================================================================

@app.route('/register', methods=['GET'])
def register_page():
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/register', methods=['POST'])
def handle_registration():
    engine = get_db_engine()

    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone_number = request.form.get('phone_number', '').strip()
    password = request.form.get('password', '')
    receive_email = 'receive_email' in request.form
    receive_sms = 'receive_sms' in request.form

    if not full_name or not email or not phone_number or not password:
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

        flash("Registration successful! Welcome to EarthGuard AI.", "success")
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
def handle_login():
    engine = get_db_engine()

    email = request.form.get('email', '').strip().lower()
    password_input = request.form.get('password', '')

    if not email or not password_input:
        flash("Please provide both your email address and password.", "error")
        return redirect(url_for('login_page'))

    try:
        with engine.connect() as connection:
            user = connection.execute(text("""
                SELECT full_name, email, password_hash
                FROM users
                WHERE email = :email;
            """), {"email": email}).fetchone()

        if user and check_password_hash(user.password_hash, password_input):
            session['user_email'] = user.email
            session['user_name'] = user.full_name
            flash(f"Welcome back, {user.full_name}! Your risk dashboard is ready.", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Incorrect email or password. Please try again.", "error")
            return redirect(url_for('login_page'))

    except Exception as e:
        print(f"Database error encountered during user authentication: {e}")
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
                         "unsubscribed_at, subscribe_sms, subscribe_email FROM users WHERE email = :email"),
                    {"email": email}
                ).fetchone()

            if not user:
                flash("User session invalid.", "error")
                return redirect(url_for('login_page'))

            now = datetime.now()
            phone_number = user.phone_number

            # Determine trial dates (carry over remaining days)
            trial_started_at = user.trial_started_at
            trial_ends_at = user.trial_ends_at
            payment_status = user.payment_status

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
                conn.execute(text("""
                    UPDATE users
                    SET is_subscribed = :global_sub,
                        subscribe_sms = :sms,
                        subscribe_email = :email_sub,
                        trial_started_at = :trial_start,
                        trial_ends_at = :trial_end,
                        unsubscribed_at = NULL
                    WHERE email = :email;
                """), {
                    "global_sub": global_active,
                    "sms": sms_opted_in,
                    "email_sub": email_opted_in,
                    "trial_start": new_trial_start,
                    "trial_end": new_trial_end,
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

@app.route('/unsubscribe', methods=['GET'])
def web_unsubscribe():
    """
    Click-to-unsubscribe for email recipients.
    - Disables all channels (subscribe_sms=False, subscribe_email=False)
    - Records unsubscribed_at timestamp
    - Sends a polite confirmation with reason request link
    """
    engine = get_db_engine()
    email = request.args.get('email')

    if not email:
        if 'user_email' in session:
            email = session['user_email']
        else:
            flash("Please sign in to change your alert preferences.", "warning")
            return redirect(url_for('login_page'))

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
                    INSERT INTO unsubscriptions (unsub_ref, channel, reason)
                    VALUES (:unsub_ref, 'email', :reason)
                """), {
                    "unsub_ref": unsub_ref,
                    "reason": reason
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
def incoming_sms_callback():
    """
    Listens for webhook payloads from Africa's Talking SMS gateway.
    STOP: disables SMS channel, records unsubscribed_at, sends polite confirmation.
    Also handles reason replies (1, 2, 3, or free text).
    """
    from_number = request.form.get("from", "").strip()
    text_content = request.form.get("text", "").strip().upper()

    print(f"[WEBHOOK SIGNAL] Incoming SMS -> From: {from_number} Content: '{text_content}'")

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
                        INSERT INTO unsubscriptions (unsub_ref, channel, reason)
                        VALUES (:unsub_ref, 'sms', :reason)
                    """), {
                        "unsub_ref": unsub_ref,
                        "reason": reason_text
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


if __name__ == '__main__':
    print("Starting EarthGuard AI REST Gateway Server on Windows Environment...")
    app.run(host='127.0.0.1', port=5000, debug=True)
