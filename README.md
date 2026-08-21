# 🌍 AthGad AI — AI-Powered Early Warning System for Eastern Kenya

**Version 1.0.0 | Proprietary | Powered by SamMutk Solutions**

AthGad AI is an intelligent environmental early-warning platform that monitors **climate**, **public-health**, and **geomagnetic** signals to predict drought, flooding, landslides, and disease outbreaks across **8 counties in Eastern Kenya** — Kitui, Machakos, Makueni, Marsabit, Isiolo, Meru, Embu, and Tharaka-Nithi.

The system fuses multiple live data feeds, detects anomalies using an **Isolation Forest** machine-learning model, computes a composite regional risk score, and automatically dispatches **SMS and Email alerts** to registered citizens — before crisis points strike their farms and communities.

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Key Features](#key-features)
3. [System Architecture](#system-architecture)
4. [Technologies & Languages Used](#technologies--languages-used)
5. [How the AI Engine Works](#how-the-ai-engine-works)
6. [Data Ingestion Pipelines](#data-ingestion-pipelines)
7. [Alert Dispatch System](#alert-dispatch-system)
8. [Premium Subscription & M-PESA Payments](#premium-subscription--mpesa-payments)
9. [User Management & Authentication](#user-management--authentication)
10. [Admin Workspace](#admin-workspace)
11. [API Endpoints](#api-endpoints)
12. [Database Schema](#database-schema)
13. [Getting Started](#getting-started)
14. [Configuration](#configuration)
15. [Testing & Validation](#testing--validation)
16. [Project Structure](#project-structure)
17. [Security Architecture](#security-architecture)
18. [Future Improvements](#future-improvements)
19. [License](#license)

---

## System Overview

AthGad AI is a full-stack web application that provides:

- **Real-time environmental monitoring** across 8 Eastern Kenya counties
- **AI-driven risk prediction** using Isolation Forest anomaly detection
- **Multi-channel alert delivery** via SMS (Africa's Talking) and Email (SMTP)
- **Tiered subscription model** with free trial and M-PESA payment integration
- **Public telemetry dashboard** for transparent risk visualization
- **Admin workspace** with PDF reports and system analytics
- **SMS management dashboard** for users to subscribe/unsubscribe/opt-out

The system operates on a continuous **ingestion → processing → analysis → alerting** pipeline:

```
External Data Sources → Ingestion Layer → PostgreSQL Database → AI Analytics Engine → Web Application → Alert Dispatch
```

### Covering Counties

| County        | Climate Zone     | Primary Threats            |
| ------------- | ---------------- | -------------------------- |
| Kitui         | Arid/Semi-Arid   | Drought, Water Scarcity    |
| Machakos      | Semi-Arid        | Drought, Crop Failure      |
| Makueni       | Arid/Semi-Arid   | Severe Drought             |
| Marsabit      | Arid             | Drought, Disease Outbreaks |
| Isiolo        | Arid             | Drought, Pest Surge        |
| Meru          | Humid            | Flooding, Landslides       |
| Embu          | Humid/Semi-Humid | Flooding, Landslides       |
| Tharaka-Nithi | Semi-Arid        | Mixed Hazards              |

---

## Key Features

| Feature                                  | Description                                                                                                                                        |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🛰️ **Multi-Source Data Ingestion**       | Live climate data (Open-Meteo), public health surveillance records, and NOAA space-weather geomagnetic indices                                     |
| 🧠 **AI Risk Fusion Engine**             | Isolation Forest anomaly detection with per-county contamination tuning + adaptive seasonal weights + time-series risk forecasting                 |
| 📍 **County-Level Advisory Registry**    | Localized, actionable safety guidance per county and hazard type                                                                                   |
| 📲 **Tiered SMS Alerts**                 | Africa's Talking SMS gateway — premium subscribers get full actionable alerts; free users get baseline safety warnings                             |
| 📧 **Tiered Email Alerts**               | Rich HTML safety advisories with impact lists and proactive checklists                                                                             |
| 💳 **M-PESA Payments**                   | 30-day free trial, then 150 KES/month via Safaricom Daraja STK Push with async webhook callback                                                    |
| 🔔 **Subscribe / Unsubscribe Pipelines** | Web (email) and SMS (`STOP`) opt-out flows with optional feedback collection                                                                       |
| 📊 **Public Telemetry Dashboard**        | Live "Detected Threats & Expected Risks" board plus county-by-county status grid                                                                   |
| 🟢 **Dynamic Landing Risk Signals**      | Public landing page "Live Risk Signals" card with real-time drought %, disease %, and hidden-pattern data                                          |
| 📱 **SMS Alert Management Dashboard**    | Users can subscribe, unsubscribe from SMS, or fully opt out from SMS alerts directly from the dashboard                                            |
| 📄 **Pagination & Filters**              | SMS delivery logs with dynamic paging (1–10, 10–20, etc.) that grow automatically + status filters (All/Success/Failed/Simulated)                  |
| 🔐 **Secure Accounts**                   | Password hashing (PBKDF2/scrypt), session-based authentication, parameterized database queries                                                     |
| 🛡️ **Admin Workspace**                   | Role-based admin access with PDF report generation (predicted calamities, disease outbreaks, subscribed/unsubscribed members, alert dispatch logs) |
| 🧪 **Comprehensive Test Suite**          | 47+ automated unit tests covering routes, security, M-PESA, analytics, county registry, and time utilities                                         |
| 🚀 **Notification Queue**                | Bounded single-worker queue serializing outbound SMS/email broadcasts to prevent race conditions                                                   |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL DATA SOURCES                         │
│  Open-Meteo (Climate)  Health Records      NOAA SWPC (Kp Index)      │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │                              │
                ▼                              ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│   INGESTION LAYER            │   │   PROCESSING LAYER           │
│   services/climate_ingestion │──▶│   core/processor.py          │
│   services/health_ingestion  │   │   (normalize → store)        │
│   services/space_weather_*   │   └──────────────┬───────────────┘
│   services/ingestion_runner  │                  │
└──────────────────────────────┘                  ▼
                                       ┌──────────────────────────────┐
                                       │  PostgreSQL + PostGIS DB     │
                                       │  climate_records             │
                                       │  health_records              │
                                       │  space_weather_records       │
                                       │  users                       │
                                       │  risk_alerts                 │
                                       │  mpesa_stk_requests          │
                                       │  alert_dispatch_logs          │
                                       │  sms_delivery_logs           │
                                       │  unsubscriptions             │
                                       └──────────────┬───────────────┘
                                                      │
                                                      ▼
                                       ┌──────────────────────────────┐
                                       │  AI ANALYTICS ENGINE         │
                                       │  core/analytics.py           │
                                       │  • Isolation Forest          │
                                       │  • Multi-Domain Fusion       │
                                       │  • Composite Risk Score      │
                                       │  • Risk Level Classification │
                                       │  • 7-Day Risk Forecasting     │
                                       │  • Adaptive Seasonal Weights │
                                       └──────────────┬───────────────┘
                                                      │
                                                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        WEB APPLICATION LAYER (Flask)                 │
│   REST API: /api/v1/risk-status, /alerts/history, /live-summary,     │
│             /telemetry/refresh, /admin/*, /sms/preferences (NEW)     │
│   Routes: /, /dashboard, /telemetry, /register, /login, /logout      │
│           /profile, /subscribe, /unsubscribe*, /admin/*              │
│   Webhooks: /api/v1/mpesa/callback, /api/v1/sms/callback            │
└──────────────┬──────────────────────────────────┬───────────────────┘
               ▼                                  ▼
┌───────────────────────────────────┐  ┌──────────────────────────────┐
│  ALERT DISPATCH SERVICE          │  │  FRONTEND (Templates + JS)     │
│  services/alert_service.py       │  │  index.html (dashboard)       │
│  • Africa's Talking SMS          │  │  landing / login / register   │
│  • SMTP Email                    │  │  subscribe / telemetry /       │
│  • Tiered (Premium/Free)         │  │  profile / admin              │
│  • Channel Separation (NEW)      │  │  static/js/dashboard.js       │
│  • No Cross-Channel Failover     │  │  static/js/admin-sms-console  │
└───────────────────────────────────┘  └──────────────────────────────┘
```

---

## Technologies & Languages Used

### Backend (Python)

| Technology           | Purpose                            |
| -------------------- | ---------------------------------- |
| **Python 3.10+**     | Primary backend language           |
| **Flask 3.1.3**      | Web application framework          |
| **Flask-CORS**       | Cross-origin resource sharing      |
| **Flask-WTF**        | CSRF protection                    |
| **Flask-Limiter**    | Rate limiting for API endpoints    |
| **SQLAlchemy 2.0**   | ORM and connection pooling         |
| **psycopg2-binary**  | PostgreSQL database driver         |
| **scikit-learn 1.9** | Isolation Forest anomaly detection |
| **pandas 3.0**       | Data manipulation and analytics    |
| **numpy 2.4**        | Numerical computing                |
| **reportlab**        | PDF report generation              |
| **requests**         | HTTP client for external services  |
| **gunicorn**         | Production WSGI server             |
| **python-dotenv**    | Environment variable management    |
| **pytest**           | Automated testing                  |

### Frontend

| Technology               | Purpose                                             |
| ------------------------ | --------------------------------------------------- |
| **HTML5**                | Templates (Jinja2 engine)                           |
| **CSS3**                 | Tailwind-replicated utility classes (no build step) |
| **JavaScript (Vanilla)** | Dashboard interactions, API calls, chart rendering  |
| **Chart.js 4.4.1**       | Risk metrics visualization                          |
| **Jinja2**               | Flask template engine                               |

### External Services

| Service                  | Purpose                                                            |
| ------------------------ | ------------------------------------------------------------------ |
| **Open-Meteo**           | Live climate data (temperature, precipitation, evapotranspiration) |
| **Africa's Talking**     | SMS gateway for alert delivery                                     |
| **Gmail SMTP**           | Email alert delivery                                               |
| **Safaricom Daraja API** | M-PESA STK Push payments                                           |
| **NOAA SWPC**            | Geomagnetic Kp-index data                                          |

---

## 🤖 How the AI Engine Works

### 1. Data Feature Extraction

The AI engine (`core/analytics.py`) constructs a 5-dimensional feature vector for each county from historical database records:

| Feature                    | Source Table          | Description                          |
| -------------------------- | --------------------- | ------------------------------------ |
| `temperature_max`          | climate_records       | Daily maximum temperature            |
| `rain` (precipitation_sum) | climate_records       | Daily precipitation total            |
| `evapotranspiration`       | climate_records       | Daily evapotranspiration             |
| `total_cases`              | health_records        | Reported disease cases per day       |
| `max_kp`                   | space_weather_records | Maximum geomagnetic Kp-index per day |

### 2. Anomaly Detection — Isolation Forest

```python
iso_forest = IsolationForest(contamination=contamination, random_state=42)
iso_forest.fit(X)
raw_scores = iso_forest.decision_function(X)
normalized_anomalies = (raw_scores.max() - raw_scores) / (raw_scores.max() - raw_scores.min() + 1e-6)
```

- Uses `sklearn.ensemble.IsolationForest` for multidimensional anomaly detection
- **Per-county contamination tuning**: Each county has a custom contamination value (Kitui: 0.15, Marsabit: 0.20, Meru: 0.10, etc.) with runtime override support via `CONTAMINATION_OVERRIDES`
- Raw anomaly scores are inverted and normalized to **0 (safe) → 1 (extreme anomaly)**

### 3. Multi-Domain Fusion Composite Score

```
Composite Score = (0.40 × Climate Severity) + (0.40 × Health Severity) + (0.20 × Space Weather Severity) + (0.15 × Anomaly Score)
```

| Domain                 | Weight |
| ---------------------- | ------ |
| Climate severity       | 40%    |
| Health severity        | 40%    |
| Space weather severity | 20%    |

### 4. Adaptive Seasonal Weights

The engine **dynamically adjusts weights** based on recent per-domain volatility and trend:

- A domain with unusually high volatility gets its weight boosted (up to +15%)
- Weights are normalized to sum to 1.0
- Fallback to static defaults when insufficient data (< 3 days)

### 5. Risk Forecasting (Time Series)

The engine projects the composite risk score forward over **7 days** using:

- **Exponential smoothing** for trend detection
- **Linear trend** on normalized features
- **Deterministic daily modulation** (via SHA-1 hash of county + date) so every forecast day is distinct
- Returns per-day forecast scores, a trend direction (increasing/stable/decreasing), and proactive mitigation actions

### 6. Risk Level Classification

| Composite Score | Risk Level | Alert Triggered                    |
| --------------- | ---------- | ---------------------------------- |
| < 0.35          | 🟢 Low     | No dispatch                        |
| 0.35 – 0.70     | 🟡 Medium  | Yes — dispatch to subscribed users |
| ≥ 0.70          | 🔴 High    | Yes — dispatch to subscribed users |

### 7. Alert Enrichment

Each risk calculation includes:

- `composite_risk_score` (0–1 decimal)
- `risk_level` (Low/Medium/High)
- `metrics` (climate_severity, health_severity, space_weather_severity, hidden_anomaly_factor)
- `advisory` (primary_calamity, vulnerability_drivers, proactive_solutions, cascading_effects)
- `forecast` (7-day score predictions with trend)

---

## 📡 Data Ingestion Pipelines

### Climate Ingestion (`services/climate_ingestion.py`)

- Fetches live weather data from **Open-Meteo API**
- Stores daily max temperature, precipitation, evapotranspiration in `climate_records`
- Runs via `python -m services.climate_ingestion`

### Health Ingestion (`services/health_ingestion.py`)

- Generates weekly health surveillance payloads per county
- Stores disease types (Malaria, Cholera, Dengue, Typhoid) and reported cases
- Runs via `python -m services.health_ingestion`

### Space Weather Ingestion (`services/space_weather_ingestion.py`)

- Fetches geomagnetic Kp indices from **NOAA SWPC**
- Stores in `space_weather_records`
- Runs via `python -m services.space_weather_ingestion`

### Ingestion Runner (`services/ingestion_runner.py`)

- Orchestrates all three ingestion services in one call
- Used by the admin `/api/v1/ingest/all` endpoint
- Runs via `python -m services.ingestion_runner`

---

## 📲 Alert Dispatch System

### Multi-Channel Delivery

AthGad AI dispatches alerts through **independent channels** with strict separation:

| Channel | Delivery Method           | Content Tier                                |
| ------- | ------------------------- | ------------------------------------------- |
| SMS     | Africa's Talking REST API | Full premium details with impacts & actions |
| Email   | SMTP (Gmail)              | Rich HTML with impacts + checklist          |

**Channel Separation (Critical Fix):**

- Users who opt for **SMS only** receive **SMS only — never emails**
- Users who opt for **Email only** receive **Email only — never SMS**
- No cross-channel failover is performed

### Tiered Content by Subscription

| Tier                        | SMS Content                                       | Email Content                                                            |
| --------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------ |
| **Premium** (paid or trial) | Full risk details, impacts list, ACTION checklist | Detailed HTML with metrics table, cascading effects, proactive solutions |
| **Baseline/Free**           | Quick safety notice + encouragement to upgrade    | Simple advisory with upgrade link                                        |

### Unsubscribed User Alert Behavior

- Users who have **unsubscribed from all channels** still receive **limited baseline** alerts for **critical count warnings** in their county
- These limited alerts **always use baseline content** (never premium content, even if the user has an active trial)
- Alerts encourage re-subscription with mention of remaining free-trial days carrying over

### Dispatch Cooldown System

- Prevents duplicate dispatches for the same county+risk level within a cooldown window (default **6 hours**)
- Uses an **atomic SQL UPDATE** to claim dispatch rights (prevents race conditions)
- Risk level changes (e.g., Medium → High) trigger immediate dispatch

### Notification Queue

- A **bounded, single-worker queue** (`services/notification_queue.py`) serializes outbound dispatch
- Prevents thread explosion under load
- Jobs run asynchronously so the API stays fast

---

## 💳 Premium & M-PESA Payments

### Premium Subscription Tiers

| Tier                  | Cost                     | Features                                                                |
| --------------------- | ------------------------ | ----------------------------------------------------------------------- |
| **Free/Baseline**     | $0                       | Limited SMS/email alerts, invitation to premium trial                   |
| **30-Day Free Trial** | $0                       | Full premium SMS + email alerts with detailed impacts and safety advice |
| **Premium Monthly**   | 150 KES/month via M-PESA | Full subscription (includes all trial features) + priority updates      |

### M-PESA Payment Flow (Daraja API)

```
User clicks "Pay with M-PESA" → STK Push initiated → User enters PIN on phone
→ Safaricom sends webhook callback → Server verifies payment → User activated
```

**Payment Initiation:**

1. User submits the subscription form with their chosen channels (SMS/Email)
2. If trial expired `payment_status = 'expired'`, system triggers STK Push via `initiate_stk_push()`
3. Server saves `mpesa_checkout_id` on the user record and creates a pending record in `mpesa_stk_requests`
4. User enters M-PESA PIN on their phone

**Callback Verification:**

- The `/api/v1/mpesa/callback` webhook validates:
  - CheckoutRequestID exists and is `pending` (blocks replays)
  - ResultCode is 0 (success)
  - `Amount` matches exactly 150 KES
  - `PhoneNumber` matches the user's registered phone
  - `MpesaReceiptNumber` has not been reused
  - Source IP is allowlisted (production fails closed)
- On success: user's `payment_status` → `active`, `is_subscribed` → TRUE, channel preferences **restored**, M-PESA receipt stored
- Thank-you SMS/Email sent via the user's preferred channel

**Trial Carry-Over:**

- If a user unsubscribes mid-trial and re-subscribes, remaining free-trial days **carry over automatically**
- If trial expires, the user is notified on both channels before SMS/Email are disabled

---

## ⚙️ User Management & Authentication

### Registration

- Users register with name, email, phone, password, and optional county
- Phone numbers are normalized to E.164 format (+2547XXXXXXXX)
- Password policy: minimum 8 characters, uppercase, lowercase, number, special character
- Email verification check, uniqueness enforced on email and phone
- Users select channels (SMS/Email) at registration

### Login & Sessions

- Werkzeug `generate_password_hash` / `check_password_hash` (PBKDF2)
- Session cookies with HTTPOnly and SameSite=Strict
- Role-based access (citizen/admin)
- Rate limited login (10/minute) to prevent brute force

### SMS Alert Management Dashboard (NEW)

The dashboard now includes **📱 SMS Alert Management** panel:

| Button                      | Action                                                                   |
| --------------------------- | ------------------------------------------------------------------------ |
| **Subscribe to SMS Alerts** | Enables SMS channel (`subscribe_sms = TRUE`) and sends confirmation text |
| **Unsubscribe from SMS**    | Disables SMS channel, keeps email if enabled                             |
| **Opt Out from SMS**        | Disables SMS entirely and switches default preference to email           |

Implemented via:

- POST `/api/v1/sms/preferences` endpoint
- `manageSms()` JavaScript function with CSRF token
- `send_sms_subscribe_confirmation()` SMS notification

### Unsubscribe Flows

**Web (Email):**

- User clicks unsubscribe link in email
- Confirm page → `subscribe_email = false`, `unsubscribed_at` set
- Confirmation email with optional feedback form

**SMS (STOP)**

- User replies `STOP` to Africa's Talking, they auto-blacklist the number
- System detects `UserInBlacklist` response and auto-disables SMS channel
- Keeps email if enabled

**Feedback Collection**

- `/unsubscribe/reason` captures both suggested and free-text reasons
- SMS users can reply with `1`, `2`, or `3` to provide structured feedback

---

## 🛡️ Admin Workspace

### Access Control

- Admin role stored in `users.role` and enforced by `@admin_required`
- Promote to admin via: `python create_admin.py admin@example.com`
- Audit logs record all admin actions

### Pages

#### 1. Overview Dashboard (`/admin`)

- Subscribed/unsubscribed user totals
- Risk alert volume and distribution
- High-risk county alerts
- 7-day risk trend chart

#### 2. Reports Hub (`/admin/reports`) — PDF Reports

| Report                 | Description                                                   |
| ---------------------- | ------------------------------------------------------------- |
| `predicted_calamities` | Predicted calamity for all 8 counties with mitigation actions |
| `disease_outbreaks`    | Disease outbreaks per county with cases                       |
| `subscribed_members`   | Currently subscribed members                                  |
| `unsubscribed_members` | Unsubscribed members with reasons and duration                |
| `alert_dispatch_logs`  | Every SMS/email dispatch with recipient, message, status      |

#### 3. Real-time SMS Console (`/admin/sms-delivery`)

- **Auto-refreshes every 5 seconds**
- Shows SMS delivery attempts with: status, AT response, cost, message ID, HTTP code, error details
- Also shows tracked alert dispatches (SMS + Email)
- **New: Pagination** — pages of 10 with dynamic page numbers (1–10, 10–20, etc.)
- **New: Status filtering** — All / Success / Failed / Simulated, applied to both SMS and dispatch views

#### 4. System Analytics (`/admin/analytics`)

- Total / subscribed / unsubscribed user counts
- Risk alert volume
- Risk level distribution by county
- Highest risk county

#### 5. Users (`/admin/users`)

- List all registered users
- Promote / demote admins

---

## 🔌 API Endpoints

### Public Endpoints

| Method | Endpoint                       | Description                   |
| ------ | ------------------------------ | ----------------------------- |
| GET    | `/`                            | Landing page                  |
| GET    | `/telemetry`                   | Public risk board (no login)  |
| GET    | `/api/v1/health`               | System health check           |
| GET    | `/api/v1/live-summary`         | Live risk signals             |
| GET    | `/api/v1/telemetry/refresh`    | Force-recompute risk scores   |
| GET    | `/api/v1/risk-status?county=X` | Delivery risk composite score |

### Auth Endpoints

| Method   | Endpoint              | Description         |
| -------- | --------------------- | ------------------- |
| GET/POST | `/register`           | Create account      |
| GET/POST | `/login`              | Login               |
| GET      | `/logout`             | Logout              |
| GET/POST | `/profile`            | View/edit profile   |
| POST     | `/profile/delete`     | Delete account      |
| GET/POST | `/subscribe`          | Manage subscription |
| GET      | `/unsubscribe`        | Unsubscribe email   |
| GET/POST | `/unsubscribe/reason` | Feedback form       |

### Alert & Dashboard APIs

| Method | Endpoint                  | Description                                   |
| ------ | ------------------------- | --------------------------------------------- |
| GET    | `/api/v1/alerts/history`  | Alert history per county                      |
| POST   | `/api/v1/sms/preferences` | SMS management (subscribe/unsubscribe/optout) |

### Webhooks

| Method | Endpoint                 | Description                  |
| ------ | ------------------------ | ---------------------------- |
| POST   | `/api/v1/mpesa/callback` | M-PESA payment verification  |
| POST   | `/api/v1/sms/callback`   | Africa's Talking inbound SMS |

### Admin APIs

| Method | Endpoint                      | Description          |
| ------ | ----------------------------- | -------------------- |
| GET    | `/admin`                      | Admin overview       |
| GET    | `/admin/reports`              | Report hub           |
| GET    | `/api/v1/admin/risk-trend`    | 7-day risk charts    |
| GET    | `/admin/sms-delivery`         | SMS delivery console |
| GET    | `/api/v1/admin/sms-delivery`  | SMS logs API         |
| GET    | `/api/v1/admin/dispatch-logs` | Dispatch logs API    |
| GET    | `/admin/reports/<type>/pdf`   | Generate PDF         |
| GET    | `/admin/analytics`            | System analytics     |
| GET    | `/admin/users`                | User management      |
| GET    | `/api/v1/ingest/all`          | Run all ingestion    |

---

## 🗃️ Database Schema

### Tables & Schema Overview

| Table                     | Columns                                                                                                                                                                                                                                                                                | Primary Key             |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| **users**                 | user_code, full_name, email, phone_number, county, password_hash, receive_email, is_subscribed, subscribe_sms, subscribe_email, dispatch_preference, payment_status, mpesa_checkout_id, trial_started_at, trial_ends_at, unsubscribed_at, role, subscription_started_at, registered_at | user_code (VARCHAR(40)) |
| **climate_records**       | climate_code, timestamp, temperature_max, precipitation_sum, evapotranspiration, geom                                                                                                                                                                                                  | climate_code            |
| **health_records**        | health_code, timestamp, county, disease_type, reported_cases, reporting_rate                                                                                                                                                                                                           | health_code             |
| **space_weather_records** | space_weather_code, timestamp, kp_index                                                                                                                                                                                                                                                | space_weather_code      |
| **risk_alerts**           | alert_code, timestamp, county, calculated_score (0.001-1.999), risk_level, notified, dispatched                                                                                                                                                                                        | alert_code              |
| **unsubscriptions**       | unsub_ref (name-based), unsubscribed_at, unsubscribed_at, channel, reason, email                                                                                                                                                                                                       | unsub_ref               |
| **mpesa_stk_requests**    | id (serial), checkout_id (UNIQUE), user_email, phone_number, amount, status (pending/success/failed), initiated_at, completed_at, mpesa_receipt                                                                                                                                        | id                      |
| **alert_dispatch_logs**   | id, dispatch_code, dispatched_at, channel, recipient, message_type, message_content, subscription_status, status, error_detail                                                                                                                                                         | id                      |
| **sms_delivery_logs**     | id, logged_at, phone_number, name, message_type, tier, status, http_status, at_status, cost, message_id, error_detail                                                                                                                                                                  | id                      |

### Human-Readable Primary Keys

Instead of auto-incrementing integers, core tables use prefixed codes:

| Table                   | Key column           | Example                      |
| ----------------------- | -------------------- | ---------------------------- |
| `climate_records`       | `climate_code`       | `CLI-20260808-3F9KQ2`        |
| `health_records`        | `health_code`        | `HLT-KIT-MAL-20260808-A3F9`  |
| `space_weather_records` | `space_weather_code` | `SPW-20260808-183012-Q2K3`   |
| `users`                 | `user_code`          | `USR-20260808-7KQ3F9`        |
| `risk_alerts`           | `alert_code`         | `RA-KIT-20260808183012-F9Q2` |
| `unsubscriptions`       | `unsub_ref`          | `John Doe2`                  |
| `alert_dispatch_logs`   | `dispatch_code`      | `DS-20260808183012-F9Q2`     |

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+**
- **PostgreSQL 12+** (PostGIS extension)
- A virtual environment tool
- API credentials for live services (optional — falls back to simulation)

### 1. Clone & Setup

```bash
git clone https://github.com/BOBITLMR145324/earthguard-ai.git
cd AthGad-ai
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 2. Configure Environment

Create `config/.env` with the required secrets:

```ini
# Database
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=AthGad_db

# Security
FLASK_SECRET_KEY=your_secret_key
SESSION_COOKIE_SECURE=false
ENVIRONMENT=development

# Africa's Talking SMS
AT_USERNAME=sandbox
AT_API_KEY=your_sandbox_key
AT_SENDER_ID=
AT_IS_SANDBOX=true
AT_API_BASE=sandbox  # or production
SSL_VERIFY=true

# Email (Gmail SMTP)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
GMAIL_SENDER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_app_password

# M-PESA
MPESA_CONSUMER_KEY=your_key
MPESA_CONSUMER_SECRET=your_secret
MPESA_ENVIRONMENT=sandbox
MPESA_SHORTCODE=174379
MPESA_PASSKEY=your_passkey
MPESA_CALLBACK_IP_ALLOWLIST=127.0.0.1/32

# App
APP_BASE_URL=http://127.0.0.1:5000
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=true
```

### 3. Initialize Database

The first time you run the app, the database schema is auto-created:

```bash
python -m core.db_init
```

### 4. Run the Application

```bash
python app.py
```

Open **http://127.0.0.1:5000**

### 5. Run Data Ingestions

```bash
python -m services.climate_ingestion            # Open-Meteo data
python -m services.health_ingestion             # Health surveillance
python -m services.space_weather_ingestion      # NOAA Kp-index
python -m services.ingestion_runner             # All at once
```

### 6. Create an Admin Account

```bash
python create_admin.py admin@example.com
```

---

## 🧪 Testing & Validation

```bash
# All tests
python -m pytest tests/ -q

# Specific test suites
python -m pytest tests/test_mpesa_service.py -v
python -m pytest tests/test_routes.py -v
python -m pytest tests/test_security.py -v
python -m pytest tests/test_analytics_direct.py -v

# Live endpoint checks (server must be running)
python tests/check_live_summary.py
python tests/run_endpoint_checks.py
```

**Test Coverage (47+ tests):**

- M-PESA service (STK push, token, callback validation)
- Security (CSRF, headers, session)
- Time utilities (EAT timezone, ISO parsing)
- County registry (calamity mapping, advisories)
- Analytics engine (risk computation, forecasting)
- Routes (endpoint contract + HTTP status codes)
- Admin reports (analytics data)

---

## 📁 Project Structure

```
AthGad-ai/
├── app.py                          # Flask application & all routes
├── create_admin.py                # CLI tool: promote user to admin
├── requirements.txt                # Python dependencies
├── pytest.ini                      # Test configuration
├── config/
│   └── .env                        # Environment secrets (git-ignored)
├── core/
│   ├── admin_reports.py            # Admin analytics & PDF data
│   ├── analytics.py                 # AI engine (IsolationForest + forecasting)
│   ├── county_registry.py           # Hazard blueprints & local advisories
│   ├── db_helper.py                # SQLAlchemy engine factory
│   ├── db_init.py                  # PostgreSQL schema & migrations
│   ├── id_codes.py                 # Human-readable key generators
│   ├── logging_setup.py            # Standardized logging
│   ├── processor.py                # Data normalization & storage
│   ├── security.py                 # Security headers, auditing
│   └── time_utils.py               # EAT timezone utilities
├── services/
│   ├── alert_service.py            # SMS/Email tiered alert dispatch
│   ├── climate_ingestion.py        # Open-Meteo climate fetcher
│   ├── health_ingestion.py         # Health surveillance generator
│   ├── ingestion_runner.py          # Orchestrates all ingestion
│   ├── mpesa_service.py             # Safaricom Daraja STK Push
│   ├── notification_queue.py       # Async dispatch queue
│   ├── pdf_report_service.py        # ReportLab PDF report generator
│   └── space_weather_ingestion.py  # NOAA Kp-index fetcher
├── static/
│   ├── css/
│   │   ├── style.css               # Tailwind-replicated utilities
│   │   └── admin.css                # Admin workspace styles
│   └── js/
│       ├── chart.umd.min.js        # Chart.js 4.4.1 (local)
│       ├── dashboard.js            # SMS management + risk dashboard
│       ├── admin-dashboard.js       # Risk-trend chart
│       ├── admin-sms-console.js    # SMS console + pagination/filters
│       ├── landing.js              # Landing live signals
│       ├── flash-alerts.js         # Auto-dismiss alerts
│       ├── password-toggle.js       # Password visibility toggles
│       ├── profile.js              # Profile form handling
│       └── telemetry.js            # Public telemetry board
├── templates/
│   ├── _logo.html                  # Shared logo
│   ├── index.html                  # Dashboard
│   ├── landing.html                # Landing page
│   ├── login.html / register.html  # Auth pages
│   ├── profile.html                # User profile
│   ├── subscribe.html               # Subscription & payment
│   ├── telemetry.html               # Public risk board
│   ├── unsubscribe*.html           # Unsubscribe flow
│   └── admin/
│       ├── dashboard.html          # Admin overview
│       ├── reports.html            # Report hub
│       ├── sms_delivery.html       # SMS delivery console
│       └── analytics.html          # System analytics
└── tests/
    ├── conftest.py                 # Test fixtures
    ├── test_mpesa_service.py       # 6 M-PESA tests
    ├── test_routes.py              # 10 route contract & HTTP tests
    ├── test_security.py             # Security header tests
    ├── test_time_utils.py          # EAT timezone tests
    ├── test_county_registry.py     # County advisories
    ├── test_analytics_direct.py     # AI engine tests
    ├── test_admin_reports.py        # Admin/report data tests
    ├── check_live_summary.py        # Live endpoint checker
    └── run_endpoint_checks.py      # Endpoint checker suite
```

---

## 🔒 Security Architecture

| Protection               | Implementation                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------- |
| **Password hashing**     | PBKDF2 via `generate_password_hash`                                                                   |
| **Session protection**   | Flask signed cookies, SameSite=Strict, HTTPS optional                                                 |
| **CSRF**                 | Flask-WTF `CSRFProtect` with meta token for JS                                                        |
| **SQL injection**        | All queries use parameterized SQLAlchemy statements                                                   |
| **Rate limiting**        | Flask-Limiter on all endpoints (default 200/hr)                                                       |
| **Secrets**              | Stored in `config/.env`, git-ignored                                                                  |
| **TLS**                  | Outbound verification enforced (`SSL_VERIFY=true`)                                                    |
| **CORS**                 | Restricted to explicit `CORS_ALLOWED_ORIGINS`                                                         |
| **Audit logging**        | All security events (login/register/admin) written with `audit()`                                     |
| **Security headers**     | X-Content-Type-Options, X-Frame-Options, Content-Security-Policy, Referrer-Policy, Permissions-Policy |
| **Rate limiting**        | Registration (10/hr), login (10/min), profile (20/min)                                                |
| **M-PESA IP allowlist**  | Callbacks validated against `MPESA_CALLBACK_IP_ALLOWLIST`                                             |
| **Payment verification** | M-PESA webhook checks amount, phone, receipt, replay                                                  |

---

## 🚀 Future Improvements

1. **Mobile App** — Native Android/iOS app for push notifications
2. **Multiple Languages** — Expand beyond English to Swahili and local dialects
3. **IoT Sensor Integration** — Real-time rain gauges / soil moisture sensors in the field
4. **SMS Expiration & Renewal** — Automatic recurring M-PESA charges (Daraja API currently requires manual trigger)
5. **ML Model Improvements**
   - Deep learning (LSTM) for time-series forecasting
   - Autoencoder anomaly detection for higher-dimensional features
   - Model retraining pipeline with evaluation metrics
6. **Multi-Cloud Deployment** — Dockerized deployment with CI/CD
7. **Data Visualization Dashboard** — Real-time maps (Leaflet/Mapbox) showing county risk levels geographically
8. **Community Reporting App** — Citizens report local observations to enrich model inputs
9. **Social Media Alerting** — WhatsApp Business API / Telegram push
10. **WTO Multiple Counties** — Expand beyond Eastern Kenya to the whole country
11. **Unified Alert Centre** — WebSocket-streamed live feed of every alert dispatch with sounding filters
12. **Audit Trail Export** — Export security & admin audit logs to CSV/PDF

---

## 📄 License

**Proprietary** — All Rights Reserved — AthGad AI (SamMutk Solutions). For demonstration, evaluation, and deployment under license agreement.
