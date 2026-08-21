# 📊 AthGad AI — Comprehensive System Report

**Report Date:** August 19, 2026  
**System Version:** 1.0.0  
**Developer:** SamMutk Solutions  
**Repository:** https://github.com/BOBITLMR145324/earthguard-ai.git

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Description](#system-description)
3. [How the System Works](#how-the-system-works)
4. [Technologies & Languages](#technologies--languages)
5. [AI Model Training & Architecture](#ai-model-training--architecture)
6. [Premium Subscription Model](#premium-subscription-model)
7. [Complete Feature Inventory](#complete-feature-inventory)
8. [Data Flow & Processing Pipeline](#data-flow--processing-pipeline)
9. [Performance & Reliability](#performance--reliability)
10. [Security Assessment](#security-assessment)
11. [Known Limitations](#known-limitations)
12. [Recommendations for Future Improvements](#recommendations-for-future-improvements)
13. [Appendix: Database Schema](#appendix-database-schema)

---

## 1. Executive Summary

AthGad AI is a production-ready, AI-powered environmental early-warning system designed to protect communities in Eastern Kenya from climate-related disasters and disease outbreaks. The system monitors **8 counties** (Kitui, Machakos, Makueni, Marsabit, Isiolo, Meru, Embu, Tharaka-Nithi) by fusing climate data from Open-Meteo, public health surveillance records, and NOAA geomagnetic space-weather indices.

The platform uses an **Isolation Forest machine-learning model** to detect multidimensional anomalies across temperature, precipitation, evapotranspiration, disease cases, and geomagnetic activity. It computes a composite risk score for each county, classifies risk levels (Low/Medium/High), and automatically dispatches tiered SMS and email alerts to registered citizens.

The system includes a **freemium subscription model** with a 30-day free trial and 150 KES/month premium tier paid via **M-PESA (Safaricom Daraja API)**. It features a public telemetry dashboard, an admin workspace with PDF report generation, real-time SMS delivery monitoring with pagination and filters, and a comprehensive SMS management dashboard for users.

---

## 2. System Description

### 2.1 Purpose

AthGad AI addresses the critical need for **early warning systems** in Eastern Kenya, a region vulnerable to:

- **Drought** — affecting agriculture, water supply, and food security
- **Flooding & Landslides** — particularly in humid counties like Meru and Embu
- **Disease Outbreaks** — malaria, cholera, dengue, and typhoid
- **Crop Failure** — due to erratic rainfall patterns

### 2.2 Target Users

| User Type                    | Access Level | Primary Use                                     |
| ---------------------------- | ------------ | ----------------------------------------------- |
| **Citizens**                 | Public       | Register, receive alerts, manage subscriptions  |
| **Farmers**                  | Public       | Receive SMS alerts about drought/rainfall risks |
| **Community Health Workers** | Public       | Receive disease outbreak alerts                 |
| **Administrators**           | Admin        | Monitor system, generate reports, manage users  |
| **Government Agencies**      | Admin        | Access analytics and PDF reports                |

### 2.3 Geographic Coverage

| County        | Risk Profile      | Primary Hazards                |
| ------------- | ----------------- | ------------------------------ |
| Kitui         | High drought risk | Severe drought, water scarcity |
| Machakos      | High drought risk | Drought, crop failure          |
| Makueni       | High drought risk | Severe drought                 |
| Marsabit      | Extreme drought   | Drought, disease outbreaks     |
| Isiolo        | High drought risk | Drought, pest surge            |
| Meru          | Flood risk        | Flooding, landslides           |
| Embu          | Flood risk        | Flooding, landslides           |
| Tharaka-Nithi | Mixed             | Mixed hazards                  |

---

## 3. How the System Works

### 3.1 End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        STEP 1: DATA INGESTION                          │
│                                                                         │
│  • Climate: Open-Meteo API → temperature, precipitation, evapotransp.   │
│  • Health: Surveillance records → disease cases per county              │
│  • Space: NOAA SWPC → geomagnetic Kp-index                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STEP 2: DATA PROCESSING & STORAGE                    │
│                                                                         │
│  • core/processor.py normalizes and validates incoming data             │
│  • Records stored in PostgreSQL with PostGIS spatial support            │
│  • Human-readable primary keys (e.g., CLI-20260808-3F9KQ2)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STEP 3: AI RISK ANALYSIS                             │
│                                                                         │
│  • Isolation Forest detects multidimensional anomalies                  │
│  • Per-county contamination tuning (0.10–0.20)                          │
│  • Multi-domain fusion: 40% climate + 40% health + 20% space            │
│  • Adaptive seasonal weights based on volatility                         │
│  • 7-day risk forecasting with exponential smoothing                    │
│  • Composite risk score persisted to risk_alerts table                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STEP 4: ALERT DISPATCH                               │
│                                                                         │
│  • Risk level Medium/High triggers dispatch                             │
│  • Dispatch cooldown (6h) prevents duplicate alerts                     │
│  • Notification queue serializes outbound broadcasts                    │
│  • SMS via Africa's Talking REST API                                    │
│  • Email via SMTP (Gmail)                                               │
│  • Channel separation: SMS users get SMS only, email users get email    │
│  • Tiered content: premium vs baseline                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STEP 5: USER INTERACTION                             │
│                                                                         │
│  • Dashboard: real-time risk scores, charts, county status              │
│  • Telemetry: public risk board with filters                            │
│  • SMS Management: subscribe/unsubscribe/opt-out                        │
│  • Subscription: M-PESA payment flow                                    │
│  • Admin: reports, analytics, SMS delivery console                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Alert Dispatch Logic

```
For each user in affected county:
    ├── Is user subscribed to SMS? → Send SMS (premium or baseline content)
    ├── Is user subscribed to Email? → Send Email (premium or baseline content)
    └── Is user unsubscribed from all channels?
        → Send LIMITED baseline alert via saved dispatch preference
        → Content is ALWAYS baseline (never premium)
```

### 3.3 M-PESA Payment Flow

```
User submits subscription form
    → Check payment_status
    → If expired: initiate STK Push (150 KES)
    → User enters PIN on phone
    → Safaricom sends webhook callback
    → Server validates: checkout ID, amount, phone, receipt, IP
    → On success: activate user, restore channel preferences
    → Send thank-you notification
```

---

## 4. Technologies & Languages

### 4.1 Programming Languages

| Language       | Usage                            | Percentage |
| -------------- | -------------------------------- | ---------- |
| **Python**     | Backend, AI engine, services     | ~70%       |
| **JavaScript** | Frontend interactions, API calls | ~15%       |
| **HTML/CSS**   | Templates and styling            | ~10%       |
| **SQL**        | Database queries                 | ~5%        |

### 4.2 Backend Framework & Libraries

| Library         | Version | Purpose             |
| --------------- | ------- | ------------------- |
| Flask           | 3.1.3   | Web framework       |
| SQLAlchemy      | 2.0.50  | ORM/database        |
| scikit-learn    | 1.9.0   | Isolation Forest ML |
| pandas          | 3.0.3   | Data analysis       |
| numpy           | 2.4.6   | Numerical computing |
| reportlab       | 4.2.5   | PDF generation      |
| psycopg2-binary | 2.9.12  | PostgreSQL driver   |
| Flask-WTF       | 1.2.2   | CSRF protection     |
| Flask-Limiter   | 3.8.0   | Rate limiting       |
| Flask-CORS      | 6.0.5   | CORS handling       |
| requests        | 2.34.2  | HTTP client         |
| gunicorn        | 26.0.0  | Production server   |
| pytest          | 9.1.1   | Testing             |

### 4.3 Frontend Technologies

| Technology            | Purpose                              |
| --------------------- | ------------------------------------ |
| HTML5 + Jinja2        | Server-side templates                |
| CSS3 (Tailwind-style) | Responsive dark-themed UI            |
| Vanilla JavaScript    | Dashboard, telemetry, SMS management |
| Chart.js 4.4.1        | Risk visualization charts            |

### 4.4 Infrastructure

| Component     | Technology                    |
| ------------- | ----------------------------- |
| Database      | PostgreSQL 12+ with PostGIS   |
| SMS Gateway   | Africa's Talking REST API     |
| Email         | SMTP (Gmail)                  |
| Payments      | Safaricom Daraja API (M-PESA) |
| Climate Data  | Open-Meteo API                |
| Space Weather | NOAA SWPC                     |

---

## 5. AI Model Training & Architecture

### 5.1 Model Type

**Isolation Forest** (`sklearn.ensemble.IsolationForest`) — an unsupervised anomaly detection algorithm that isolates anomalies instead of profiling normal data points.

### 5.2 Training Data

The model is trained on **historical database records** from:

| Source                  | Features                                               | Time Window  |
| ----------------------- | ------------------------------------------------------ | ------------ |
| `climate_records`       | temperature_max, precipitation_sum, evapotranspiration | Last 30 days |
| `health_records`        | reported_cases (per county)                            | Last 30 days |
| `space_weather_records` | kp_index                                               | Last 30 days |

### 5.3 Feature Vector

Each county's observation vector contains 5 features:

```
X = [temperature_max, precipitation_sum, evapotranspiration, total_cases, max_kp]
```

### 5.4 Training Process

1. **Data Collection**: Query last 30 days of records from PostgreSQL
2. **Fallback Baselines**: If DB is empty, use deterministic per-county baselines (no randomness)
3. **Model Training**: `IsolationForest(contamination=per_county_value, random_state=42)`
4. **Score Computation**: `decision_function(X)` → invert → normalize to 0–1
5. **Risk Fusion**: Combine with climate/health/space severity scores
6. **Classification**: Map composite score to Low/Medium/High
7. **Forecasting**: Project forward 7 days using exponential smoothing

### 5.5 Per-County Contamination Tuning

| County        | Contamination | Rationale                             |
| ------------- | ------------- | ------------------------------------- |
| Kitui         | 0.15          | Standard drought-prone                |
| Machakos      | 0.12          | Moderate variability                  |
| Makueni       | 0.15          | Standard drought-prone                |
| Marsabit      | 0.20          | High variability (extreme conditions) |
| Isiolo        | 0.18          | High variability                      |
| Meru          | 0.10          | Lower variability (humid)             |
| Embu          | 0.10          | Lower variability (humid)             |
| Tharaka-Nithi | 0.12          | Moderate variability                  |

### 5.6 Model Retraining

The model is **retrained on every risk calculation** using the latest 30 days of data. This ensures the model adapts to seasonal changes and emerging patterns without manual intervention.

---

## 6. Premium Subscription Model

### 6.1 Pricing Structure

| Tier                | Price   | Duration  | Features                                  |
| ------------------- | ------- | --------- | ----------------------------------------- |
| **Free/Baseline**   | Free    | Unlimited | Limited SMS/email alerts, upgrade prompts |
| **Premium Trial**   | Free    | 30 days   | Full premium alerts, detailed advisories  |
| **Premium Monthly** | 150 KES | 30 days   | All premium features, priority updates    |

### 6.2 Payment Method

- **M-PESA STK Push** via Safaricom Daraja API
- Sandbox mode for testing (shortcode 174379)
- Production mode requires real paybill/till number
- Callback URL must be HTTPS in production

### 6.3 Subscription Lifecycle

```
Register → Free baseline → Activate 30-day trial → Trial expires
    → Pay 150 KES via M-PESA → Premium active (30 days)
    → Premium expires → Back to baseline → Re-subscribe
```

### 6.4 Trial Carry-Over Feature

If a user unsubscribes mid-trial and re-subscribes:

- Remaining trial days are **carried over automatically**
- User is notified of the carry-over in the confirmation message
- This encourages re-subscription without losing value

### 6.5 Channel Preferences

Users can choose:

- **SMS only** — receive alerts via text message
- **Email only** — receive alerts via email
- **Both** — receive alerts via both channels
- **None** — unsubscribed (still receives limited baseline alerts)

---

## 7. Complete Feature Inventory

### 7.1 Core Features

| #   | Feature                     | Status    | Description                        |
| --- | --------------------------- | --------- | ---------------------------------- |
| 1   | Multi-source data ingestion | ✅ Active | Climate, health, space weather     |
| 2   | AI risk analysis            | ✅ Active | Isolation Forest + fusion          |
| 3   | Risk forecasting            | ✅ Active | 7-day projections                  |
| 4   | SMS alerts                  | ✅ Active | Africa's Talking                   |
| 5   | Email alerts                | ✅ Active | SMTP with HTML templates           |
| 6   | M-PESA payments             | ✅ Active | Daraja STK Push                    |
| 7   | User registration/login     | ✅ Active | Secure authentication              |
| 8   | Profile management          | ✅ Active | Edit name, phone, county, password |
| 9   | Subscription management     | ✅ Active | Trial, payment, channels           |
| 10  | Unsubscribe flows           | ✅ Active | Web + SMS STOP                     |
| 11  | Public telemetry            | ✅ Active | Live risk board                    |
| 12  | Landing page signals        | ✅ Active | Dynamic live risk card             |
| 13  | Admin workspace             | ✅ Active | Reports, analytics, users          |
| 14  | PDF reports                 | ✅ Active | 5 report types                     |
| 15  | SMS delivery console        | ✅ Active | Real-time monitoring               |
| 16  | Notification queue          | ✅ Active | Async dispatch                     |
| 17  | Dispatch cooldown           | ✅ Active | 6-hour dedup window                |
| 18  | Channel separation          | ✅ Active | SMS/Email independence             |
| 19  | SMS management dashboard    | ✅ Active | Subscribe/unsubscribe/opt-out      |
| 20  | SMS log pagination          | ✅ Active | Dynamic page controls              |
| 21  | Status filters              | ✅ Active | All/Success/Failed/Simulated       |

### 7.2 Security Features

| #   | Feature               | Implementation                        |
| --- | --------------------- | ------------------------------------- |
| 1   | Password hashing      | PBKDF2 via Werkzeug                   |
| 2   | CSRF protection       | Flask-WTF                             |
| 3   | Rate limiting         | Flask-Limiter                         |
| 4   | SQL injection defense | Parameterized queries                 |
| 5   | Session security      | HTTPOnly, SameSite=Strict             |
| 6   | Security headers      | CSP, X-Frame-Options, etc.            |
| 7   | CORS restriction      | Explicit allowlist                    |
| 8   | TLS verification      | SSL_VERIFY=true                       |
| 9   | M-PESA IP allowlist   | Callback validation                   |
| 10  | Audit logging         | All security events                   |
| 11  | Phone normalization   | E.164 format                          |
| 12  | Payment verification  | Amount, phone, receipt, replay checks |

### 7.3 Admin Features

| #   | Feature              | Description             |
| --- | -------------------- | ----------------------- |
| 1   | Overview dashboard   | User counts, risk stats |
| 2   | PDF reports          | 5 report types          |
| 3   | SMS delivery console | Real-time logs          |
| 4   | System analytics     | Risk distribution       |
| 5   | User management      | Promote/demote admins   |
| 6   | Data ingestion       | Run all pipelines       |
| 7   | Risk trend charts    | 7-day visualization     |

---

## 8. Data Flow & Processing Pipeline

### 8.1 Ingestion Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    INGESTION SCHEDULE                       │
├─────────────────────────────────────────────────────────────┤
│  Climate:    Every 6 hours (Open-Meteo API)                 │
│  Health:     Weekly (surveillance records)                  │
│  Space:      Every 3 hours (NOAA SWPC)                      │
│  All:        Manual via admin /api/v1/ingest/all            │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 Processing Pipeline

```
Raw Data → Normalize → Validate → Store → Analyze → Alert
```

1. **Normalize**: Convert units, format timestamps, validate ranges
2. **Validate**: Check for missing values, outliers, invalid entries
3. **Store**: Insert into PostgreSQL with human-readable keys
4. **Analyze**: Run AI engine to compute risk scores
5. **Alert**: Dispatch notifications for Medium/High risk

### 8.3 Database Tables

| Table                   | Purpose                       | Records                    |
| ----------------------- | ----------------------------- | -------------------------- |
| `users`                 | User accounts & subscriptions | All registered users       |
| `climate_records`       | Weather data                  | Daily climate observations |
| `health_records`        | Disease surveillance          | Weekly health data         |
| `space_weather_records` | Geomagnetic data              | Kp-index readings          |
| `risk_alerts`           | Computed risk scores          | Every risk calculation     |
| `mpesa_stk_requests`    | Payment tracking              | Every STK push             |
| `alert_dispatch_logs`   | Alert audit trail             | Every SMS/email sent       |
| `sms_delivery_logs`     | SMS delivery details          | Every SMS attempt          |
| `unsubscriptions`       | Opt-out records               | Unsubscribe feedback       |

---

## 9. Performance & Reliability

### 9.1 Performance Features

| Feature                | Implementation                               |
| ---------------------- | -------------------------------------------- |
| **Database indexing**  | 13 performance indexes on hot query paths    |
| **Connection pooling** | SQLAlchemy QueuePool (10-20 connections)     |
| **Async dispatch**     | Notification queue (bounded, single-worker)  |
| **Dispatch dedup**     | Atomic SQL claim (prevents duplicate alerts) |
| **Caching**            | M-PESA OAuth token cached (1-hour validity)  |
| **Asset versioning**   | SHA-256 hash for cache busting               |

### 9.2 Reliability Features

| Feature                     | Implementation                                 |
| --------------------------- | ---------------------------------------------- |
| **Graceful degradation**    | Analytics falls back to baselines if DB empty  |
| **Retry logic**             | HTTP retries for SMS (3 attempts)              |
| **Error logging**           | Structured logging to console + rotating files |
| **Audit trail**             | All security events logged                     |
| **SMS delivery tracking**   | Every attempt logged with status               |
| **Alert dispatch tracking** | Every dispatch logged with content             |

### 9.3 Test Coverage

| Test Suite      | Tests    | Coverage                       |
| --------------- | -------- | ------------------------------ |
| M-PESA service  | 6        | STK push, token, callback      |
| Routes          | 10       | Endpoint contract, HTTP status |
| Security        | Multiple | Headers, CSRF, sessions        |
| Time utilities  | Multiple | EAT timezone, parsing          |
| County registry | Multiple | Calamity mapping               |
| Analytics       | Multiple | Risk computation, forecasting  |
| Admin reports   | Multiple | Analytics data queries         |

---

## 10. Security Assessment

### 10.1 Strengths

✅ **Strong password hashing** — PBKDF2 with per-user salts  
✅ **CSRF protection** — All forms and AJAX requests  
✅ **Rate limiting** — Prevents brute force and abuse  
✅ **Parameterized SQL** — No SQL injection vectors  
✅ **Security headers** — CSP, X-Frame-Options, nosniff  
✅ **Payment verification** — Multi-factor callback validation  
✅ **IP allowlisting** — M-PESA callbacks restricted  
✅ **TLS verification** — Outbound requests verify certificates  
✅ **Audit logging** — All security events recorded  
✅ **Session security** — HTTPOnly, SameSite=Strict cookies

### 10.2 Areas for Improvement

⚠️ **HTTPS enforcement** — `SESSION_COOKIE_SECURE` defaults to false in development  
⚠️ **2FA** — No two-factor authentication for admin accounts  
⚠️ **Rate limit storage** — Defaults to in-memory (lost on restart)  
⚠️ **Backup strategy** — No automated database backup documented  
⚠️ **Dependency updates** — Regular security patches needed

---

## 11. Known Limitations

| #   | Limitation                       | Impact                                   | Mitigation                |
| --- | -------------------------------- | ---------------------------------------- | ------------------------- |
| 1   | **No automatic M-PESA renewal**  | Users must manually re-subscribe monthly | Reminder emails/SMS       |
| 2   | **In-memory notification queue** | Jobs lost if process restarts            | Redis/RQ for production   |
| 3   | **Single-language UI**           | English only                             | Future: Swahili support   |
| 4   | **No mobile app**                | Web-only access                          | Future: native apps       |
| 5   | **Synthetic health data**        | May not reflect real outbreaks           | Connect to MoH feed       |
| 6   | **No geospatial visualization**  | No map view of risk levels               | Future: Leaflet/Mapbox    |
| 7   | **Manual admin promotion**       | Requires CLI command                     | Future: admin UI          |
| 8   | **No email verification**        | Users can register with any email        | Future: verification flow |

---

## 12. Recommendations for Future Improvements

### 12.1 High Priority

1. **Automatic M-PESA Recurring Payments**
   - Implement Daraja API recurring payment (B2C/C2B)
   - Auto-renew premium subscriptions monthly
   - Send payment reminders 3 days before expiry

2. **Dockerized Deployment**
   - Create Dockerfile and docker-compose.yml
   - Include PostgreSQL, Redis, and app containers
   - Set up CI/CD pipeline (GitHub Actions)

3. **Redis-backed Notification Queue**
   - Replace in-memory queue with Redis/RQ
   - Ensure durable delivery across restarts
   - Support multiple workers for scale

4. **Email Verification**
   - Send verification link on registration
   - Require verified email for premium features
   - Prevent fake account creation

### 12.2 Medium Priority

5. **Mobile Application**
   - React Native / Flutter app
   - Push notifications (FCM)
   - Offline risk data caching

6. **Swahili Language Support**
   - i18n framework for templates
   - SMS alerts in Swahili
   - Localized advisories

7. **Geospatial Risk Maps**
   - Leaflet/Mapbox integration
   - Color-coded county risk overlays
   - Click-to-detail county cards

8. **Community Reporting**
   - Citizens report local observations
   - Crowdsourced data enriches ML model
   - Photo uploads for verification

### 12.3 Advanced AI Improvements

9. **LSTM Deep Learning Model**
   - Replace/augment Isolation Forest with LSTM
   - Better time-series pattern recognition
   - Improved long-term forecasting

10. **Autoencoder Anomaly Detection**
    - Higher-dimensional feature support
    - Reconstruction error for anomaly scoring
    - Better handling of seasonal patterns

11. **Model Evaluation Pipeline**
    - Track precision/recall of predictions
    - Compare model versions
    - Automated retraining on new data

12. **Multi-County Expansion**
    - Expand beyond Eastern Kenya
    - Add all 47 Kenyan counties
    - Regional risk aggregation

### 12.4 Operational Improvements

13. **Automated Database Backups**
    - Daily PostgreSQL dumps
    - Offsite storage (S3)
    - Point-in-time recovery

14. **Monitoring & Alerting**
    - Prometheus + Grafana
    - System health dashboards
    - Alert on service failures

15. **Audit Trail Export**
    - CSV/PDF export of audit logs
    - Compliance reporting
    - Searchable audit interface

16. **WhatsApp/Telegram Alerts**
    - Additional delivery channels
    - Rich media alerts
    - Group alerting for communities

---

## 13. Appendix: Database Schema

### users

```sql
CREATE TABLE users (
    user_code VARCHAR(40) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    county VARCHAR(50),
    phone_number VARCHAR(20) NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    receive_email BOOLEAN DEFAULT FALSE,
    is_subscribed BOOLEAN DEFAULT FALSE,
    subscribe_sms BOOLEAN DEFAULT FALSE,
    subscribe_email BOOLEAN DEFAULT FALSE,
    dispatch_preference VARCHAR(10) DEFAULT 'sms',
    payment_status VARCHAR(20) DEFAULT 'trialing',
    mpesa_checkout_id VARCHAR(100),
    trial_started_at TIMESTAMP,
    trial_ends_at TIMESTAMP,
    unsubscribed_at TIMESTAMP,
    role VARCHAR(20) DEFAULT 'citizen',
    subscription_started_at TIMESTAMP,
    registered_at TIMESTAMP DEFAULT NOW()
);
```

### risk_alerts

```sql
CREATE TABLE risk_alerts (
    alert_code VARCHAR(40) PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    county VARCHAR(50) NOT NULL,
    calculated_score NUMERIC(4,3) NOT NULL,
    risk_level VARCHAR(15) NOT NULL,
    notified BOOLEAN DEFAULT FALSE,
    dispatched BOOLEAN DEFAULT FALSE
);
```

### mpesa_stk_requests

```sql
CREATE TABLE mpesa_stk_requests (
    id SERIAL PRIMARY KEY,
    checkout_id VARCHAR(100) NOT NULL UNIQUE,
    user_email VARCHAR(255) NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    amount NUMERIC(10, 0) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    initiated_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    mpesa_receipt VARCHAR(40)
);
```

### alert_dispatch_logs

```sql
CREATE TABLE alert_dispatch_logs (
    id SERIAL PRIMARY KEY,
    dispatch_code VARCHAR(40) UNIQUE,
    dispatched_at TIMESTAMP DEFAULT NOW(),
    channel VARCHAR(10) NOT NULL,
    recipient VARCHAR(120) NOT NULL,
    message_type VARCHAR(30) DEFAULT 'alert',
    message_content TEXT,
    subscription_status VARCHAR(30) DEFAULT 'unknown',
    status VARCHAR(20) NOT NULL,
    error_detail VARCHAR(500)
);
```

### sms_delivery_logs

```sql
CREATE TABLE sms_delivery_logs (
    id SERIAL PRIMARY KEY,
    logged_at TIMESTAMP DEFAULT NOW(),
    phone_number VARCHAR(20),
    name VARCHAR(100),
    message_type VARCHAR(30) DEFAULT 'notification',
    tier VARCHAR(20) DEFAULT 'none',
    status VARCHAR(20) NOT NULL,
    http_status INTEGER,
    at_status VARCHAR(40),
    cost VARCHAR(20),
    message_id VARCHAR(60),
    error_detail VARCHAR(500)
);
```

---

## Conclusion

AthGad AI represents a comprehensive, production-ready early warning system that combines modern AI/ML techniques with practical community-focused features. The system successfully addresses the critical need for timely environmental risk communication in Eastern Kenya through its multi-channel alert delivery, tiered subscription model, and transparent public dashboards.

With the recent improvements to channel separation, SMS management, pagination, and M-PESA integration, the system is well-positioned for deployment. The recommendations outlined in Section 12 provide a clear roadmap for scaling the system to national coverage and enhancing its AI capabilities.

---

_Report generated by SamMutk Solutions — August 2026_
