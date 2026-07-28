import os
import sys
from datetime import datetime
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
    
    flash("Please sign in to access target tracking matrices.", "warning")
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
        print(f"❌ Backend route exception intercepted: {str(e)}")
        return jsonify({"status": "error", "message": f"Analytics pipeline execution failed: {str(e)}"}), 500

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
        return jsonify({"status": "error", "message": f"Regional status board extraction failed: {str(e)}"}), 500

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
                # If stored as decimal (0.68), convert to percent score
                score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                risk_level = record.risk_level
            else:
                # Fallback to Analytics Engine live score if DB is empty
                live_data = analytics_engine.calculate_composite_risk(county)
                raw_score = live_data.get("composite_risk_score", 0.3)
                score_pct = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                risk_level = live_data.get("risk_level", "Low")
            
            # Determine threat category for filtering (Health vs Climate)
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
        print(f"⚠️ Telemetry Data Load Warning: {e}")
        # Default fallback structure if DB connection is unavailable
        for county in covered_counties:
            status_board.append({
                "county": county,
                "score_pct": 0.0,
                "risk_level": "Low",
                "threat_category": "climate",
                "primary_threat": "Monitoring Sync Active"
            })

    # Calculate dynamic averages for the two top hazard cards
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

# CITIZEN REGISTRATION & NOTIFICATION MANAGEMENT SYSTEM

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

    if not full_name or not email or not phone_number or not password:
        flash("All fields are required to establish an alert profile.", "error")
        return redirect(url_for('register_page'))

    try:
        with engine.connect() as connection:
            existing_user = connection.execute(text("""
                SELECT id FROM users WHERE email = :email OR phone_number = :phone;
            """), {"email": email, "phone": phone_number}).fetchone()

        if existing_user:
            flash("An account with this email or phone number already exists. Please log in directly.", "warning")
            return redirect(url_for('register_page'))

        hashed_password = generate_password_hash(password)

        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO users (full_name, email, phone_number, password_hash, receive_email, is_subscribed, subscribe_sms, subscribe_email, dispatch_preference)
                VALUES (:name, :email, :phone, :hash, :email_opt, False, False, False, 'sms');
            """), {
                "name": full_name,
                "email": email,
                "phone": phone_number,
                "hash": hashed_password,
                "email_opt": receive_email
            })

        session['user_email'] = email
        session['user_name'] = full_name

        flash("Registration successful! Welcome to EarthGuard AI.", "success")
        return redirect(url_for('dashboard'))

    except Exception as e:
        print(f"Database error encountered during registration: {e}")
        flash("An unexpected infrastructure error occurred. Please try again.", "error")
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

            flash(f"Welcome back, {user.full_name}! Connection to EarthGuard telemetry re-established.", "success")
            return redirect(url_for('dashboard'))  
        else:
            flash("Invalid email or password parameters. Please double-check your inputs.", "error")
            return redirect(url_for('login_page'))

    except Exception as e:
        print(f"Database error encountered during user authentication: {e}")
        flash("An unexpected infrastructure error occurred. Please try again.", "error")
        return redirect(url_for('login_page'))

@app.route('/logout', methods=['GET'])
def handle_logout():
    """Wipes active session state tokens, signing out the user safely."""
    session.clear()
    flash("Secure tracking link severed. Dashboard session terminated.", "info")
    return redirect(url_for('login_page'))


# SUBSCRIPTION & PAYMENT MANAGEMENT PIPELINE

@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe_portal():
    """Handles configuring user alert profiles and processing M-PESA STK pushes."""
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
        
    email = session['user_email']
    engine = get_db_engine()
    
    if request.method == 'POST':
        selected_mediums = request.form.getlist('dispatch_medium')
        payment_method = request.form.get('payment_method', 'mpesa')
        
        sms_opted_in = 'sms' in selected_mediums
        email_opted_in = 'email' in selected_mediums
        global_active = (sms_opted_in or email_opted_in)
        
        try:
            with engine.connect() as conn:
                user = conn.execute(
                    text("SELECT phone_number, payment_status FROM users WHERE email = :email"),
                    {"email": email}
                ).fetchone()

            if not user:
                flash("User session invalid.", "error")
                return redirect(url_for('login_page'))

            # Trigger M-PESA payment push if subscriber relies on M-PESA Express
            if payment_method == 'mpesa' and global_active:
                stk_response = initiate_stk_push(
                    phone_number=user.phone_number,
                    amount=150,
                    account_reference="EarthGuardSub"
                )
                
                checkout_id = stk_response.get("CheckoutRequestID") if isinstance(stk_response, dict) else None
                
                if checkout_id:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            UPDATE users 
                            SET mpesa_checkout_id = :cid,
                                subscribe_sms = :sms,
                                subscribe_email = :email_sub
                            WHERE email = :email;
                        """), {
                            "cid": checkout_id,
                            "sms": sms_opted_in,
                            "email_sub": email_opted_in,
                            "email": email
                        })
                    flash("📲 M-PESA STK Push sent! Check your phone and enter your PIN to complete subscription.", "info")
                    return redirect(url_for('subscribe_portal'))
                else:
                    flash("Failed to communicate with M-PESA API Gateway. Please verify phone number format.", "error")
                    return redirect(url_for('subscribe_portal'))

            # Free channel preference updates
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE users 
                    SET is_subscribed = :global_sub,
                        subscribe_sms = :sms,
                        subscribe_email = :email_sub
                    WHERE email = :email;
                """), {
                    "global_sub": global_active, 
                    "sms": sms_opted_in, 
                    "email_sub": email_opted_in, 
                    "email": email
                })
                
            flash("Notification preferences updated successfully.", "success")
            return redirect(url_for('dashboard'))

        except Exception as e:
            print(f"Subscription pipeline writing error: {e}")
            flash("Infrastructure error encountered while updating preferences.", "error")
            return redirect(url_for('subscribe_portal'))
            
    # GET Logic: Extract status rows
    try:
        with engine.connect() as connection:
            user_status = connection.execute(
                text("SELECT is_subscribed, subscribe_sms, subscribe_email, payment_status FROM users WHERE email = :e"), 
                {"e": email}
            ).fetchone()
    except Exception as e:
        print(f"Routing Warning: Could not fetch initial state: {e}")
        user_status = None

    return render_template('subscribe.html', status=user_status)


@app.route('/api/v1/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """
    Webhook endpoint triggered asynchronously by Safaricom Daraja API when user enters PIN.
    """
    data = request.get_json()
    print(f"📥 [M-PESA CALLBACK] {data}")

    try:
        stk_callback = data.get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        checkout_id = stk_callback.get("CheckoutRequestID")

        engine = get_db_engine()

        # ResultCode == 0 means payment was SUCCESSFUL
        if result_code == 0:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE users 
                    SET payment_status = 'active',
                        is_subscribed = True,
                        trial_ends_at = NULL
                    WHERE mpesa_checkout_id = :cid;
                """), {"cid": checkout_id})
            print(f"✅ [PAYMENT SUCCESS] Account marked active for CheckoutID: {checkout_id}")
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
            print(f"⚠️ [PAYMENT FAILED/CANCELLED] CheckoutID: {checkout_id}")
            return jsonify({"ResultCode": 0, "ResultDesc": "Acknowledged"}), 200

    except Exception as e:
        print(f"❌ M-PESA Callback Execution Error: {e}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Error"}), 500


# WEB AND INCOMING CORE TELEPHONY UN-SUBSCRIBE PIPELINES

@app.route('/unsubscribe', methods=['GET'])
def web_unsubscribe():
    """
    Provides a click-to-unsubscribe channel for email recipients.
    """
    engine = get_db_engine()
    email = request.args.get('email')

    if not email:
        if 'user_email' in session:
            email = session['user_email']
        else:
            flash("Authentication token or matching profile reference required to modify preferences manually.", "warning")
            return redirect(url_for('login_page'))
    
    try:
        with engine.begin() as connection:
            result = connection.execute(text("""
                UPDATE users 
                SET is_subscribed = False,
                    subscribe_sms = False,
                    subscribe_email = False
                WHERE email = :email;
            """), {"email": email})
            
            rows_affected = result.rowcount
            
        if rows_affected > 0:
            print(f"🚫 [EMAIL OPT-OUT] Downgraded subscription status for: {email}")
            
            return render_template_string("""
            <!DOCTYPE html>
            <html>
            <body style="font-family: sans-serif; text-align: center; background-color: #020617; color: #f8fafc; padding-top: 80px; margin: 0;">
                <div style="max-width: 450px; margin: 0 auto; background-color: #0f172a; padding: 40px; border: 1px solid #1e293b; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
                    <h2 style="color: #ef4444; margin-top: 0;">Opt-Out Successful</h2>
                    <p style="color: #94a3b8; line-height: 1.6; font-size: 14px;">You have been successfully opted out of premium analytical updates for <strong>{{ email }}</strong>.</p>
                    <p style="color: #eab308; line-height: 1.6; font-size: 13px;">⚠️ Note: Critical baseline safety notifications for your county will remain active.</p>
                </div>
            </body>
            </html>
            """, email=email), 200
        else:
            flash("No account registered under that email identification address.", "error")
            return redirect(url_for('dashboard'))

    except Exception as e:
        print(f"Web portal processing error for unsubscribe vector: {e}")
        flash("An error occurred processing your request. Please try again.", "error")
        return redirect(url_for('dashboard'))


@app.route('/api/v1/sms/callback', methods=['POST'])
def incoming_sms_callback():
    """
    Listens for webhook payloads dispatched from the Infobip SMS gateway.
    Converts incoming parameters and checks for opt-out codes (STOP).
    Accepts both Infobip's JSON format (sender, message) and URL-encoded fallback (from, text).
    """
    # Try parsing JSON body first (Infobip's default format), fall back to form data
    json_data = request.get_json(silent=True)
    if json_data:
        from_number = json_data.get("from", json_data.get("sender", "")).strip()
        text_content = json_data.get("text", json_data.get("message", "")).strip().upper()
    else:
        from_number = request.form.get("from", request.form.get("From", "")).strip()
        text_content = request.form.get("text", request.form.get("Body", "")).strip().upper()

    print(f"\n📥 [WEBHOOK SIGNAL] Incoming SMS Gateway event triggered -> From: {from_number} Content: '{text_content}'")

    if not from_number:
        return jsonify({"status": "ignored", "reason": "No sender phone parameter found."}), 400

    if text_content == "STOP":
        engine = get_db_engine()
        
        norm_number = from_number
        if norm_number.startswith('+254'):
            norm_number = norm_number[4:]
        elif norm_number.startswith('254'):
            norm_number = norm_number[3:]
        elif norm_number.startswith('0'):
            norm_number = norm_number[1:]

        search_query = f"%{norm_number}"

        try:
            with engine.begin() as connection:
                user = connection.execute(text("""
                    SELECT is_subscribed, subscribe_email 
                    FROM users 
                    WHERE phone_number LIKE :phone_pattern;
                """), {"phone_pattern": search_query}).fetchone()

                if not user:
                    print(f"⚠️ [PIPELINE ALERT] STOP keyword matched but no user record found for {from_number}")
                    return jsonify({"status": "not_found", "message": "Phone vector does not exist in registry index."}), 200

                has_premium_email = getattr(user, 'is_subscribed', False) and getattr(user, 'subscribe_email', False)
                new_global_sub = True if has_premium_email else False

                result = connection.execute(text("""
                    UPDATE users 
                    SET is_subscribed = :global_sub,
                        subscribe_sms = True
                    WHERE phone_number LIKE :phone_pattern;
                """), {
                    "global_sub": new_global_sub,
                    "phone_pattern": search_query
                })
                
                rows_affected = result.rowcount

            if rows_affected > 0:
                print(f"🚫 [PIPELINE SUCCESS] Downgraded SMS channel for {from_number} to baseline view.")
                return jsonify({"status": "success", "message": "Subscription tier altered successfully."}), 200

        except Exception as e:
            print(f"❌ Webhook subscriber execution failed: {e}")
            return jsonify({"status": "database_error", "message": str(e)}), 500

    # Return 200 OK to acknowledge receipt of the webhook from Infobip
    return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    print("Starting EarthGuard AI REST Gateway Server on Windows Environment...")
    app.run(host='127.0.0.1', port=5000, debug=True)