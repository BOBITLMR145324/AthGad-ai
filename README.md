# 🌍 EarthGuard AI

**AI-Powered Early Warning System for Eastern Kenya**

EarthGuard AI is an intelligent environmental early-warning platform that monitors climate, public-health, and geomagnetic signals to predict drought, flooding, landslides, and disease outbreaks across **8 counties in Eastern Kenya** — Kitui, Machakos, Makueni, Marsabit, Isiolo, Meru, Embu, and Tharaka-Nithi.

The system fuses multiple live data feeds, detects anomalies using an **Isolation Forest** machine-learning model, computes a composite regional risk score, and automatically dispatches **SMS and Email alerts** to registered citizens — before crisis points strike their farms and communities.

---

## ✨ Key Features

| Feature                                  | Description                                                                                                                        |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 🛰️ **Multi-Source Data Ingestion**       | Live climate data (Open-Meteo), public health surveillance records, and NOAA space-weather geomagnetic indices                     |
| 🧠 **AI Risk Fusion Engine**             | Isolation Forest anomaly detection combined with a weighted multi-domain risk formula (Climate 40%, Health 40%, Space Weather 20%) |
| 📍 **County-Level Advisory Registry**    | Localized, actionable safety guidance per county and hazard type (Severe Drought / Flooding & Landslides)                          |
| 📲 **Tiered SMS Alerts**                 | Africa's Talking SMS gateway — premium subscribers get full actionable alerts; free users get baseline safety warnings             |
| 📧 **Tiered Email Alerts**               | Rich HTML safety advisories with impact lists and proactive checklists                                                             |
| 💳 **M-PESA Payments**                   | 30-day free trial, then 150 KES/month via Safaricom Daraja STK Push with async webhook callback                                    |
| 🔔 **Subscribe / Unsubscribe Pipelines** | Web (email) and SMS (`STOP`) opt-out flows with optional feedback collection                                                       |
| 📊 **Public Telemetry Dashboard**        | Live "Detected Threats & Expected Risks" board plus a county-by-county status grid                                                 |
| 🔐 **Secure Accounts**                   | Password hashing, session-based authentication, parameterized database queries                                                     |

---

## 🏗️ System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL DATA SOURCES                         │
│  Open-Meteo (Climate)  Health Records  NOAA SWPC (Geomagnetic Kp)    │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │                              │
                ▼                              ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│   INGESTION LAYER            │   │   PROCESSING LAYER           │
│   services/climate_ingestion │──▶│   core/processor.py          │
│   services/health_ingestion  │   │   (normalize → store)        │
│   services/space_weather_*   │   └──────────────┬───────────────┘
└──────────────────────────────┘                  ▼
                                      ┌──────────────────────────────┐
                                      │  PostgreSQL + PostGIS DB     │
                                      │  climate_records             │
                                      │  health_records              │
                                      │  space_weather_records       │
                                      │  users                       │
                                      │  risk_alerts                 │
                                      │  unsubscriptions             │
                                      └──────────────┬───────────────┘
                                                     ▼
                                      ┌──────────────────────────────┐
                                      │  AI ANALYTICS ENGINE         │
                                      │  core/analytics.py           │
                                      │  • Isolation Forest          │
                                      │  • Multi-Domain Fusion       │
                                      │  • Composite Risk Score      │
                                      │  • Risk Level (Low/Med/High) │
                                      └──────────────┬───────────────┘
                                                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        WEB APPLICATION LAYER (Flask)                 │
│   REST API: /api/v1/risk-status, /alerts/history, /health            │
│   Routes: /, /dashboard, /telemetry, /register, /login, /logout      │
│           /subscribe, /unsubscribe, /unsubscribe/reason              │
│   Webhooks: /api/v1/mpesa/callback, /api/v1/sms/callback             │
└──────────────┬──────────────────────────────────┬───────────────────┘
               ▼                                  ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│  ALERT DISPATCH SERVICE      │   │  FRONTEND (Templates + JS)   │
│  services/alert_service.py   │   │  index.html (dashboard)      │
│  • Africa's Talking SMS      │   │  landing / login / register  │
│  • SMTP Email                │   │  subscribe / telemetry       │
│  • Tiered (Premium/Free)     │   │  static/js/dashboard.js      │
└──────────────────────────────┘   └──────────────────────────────┘
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+**
- **PostgreSQL 12+** (with PostGIS extension available)
- A virtual environment tool (`venv` recommended)
- API credentials for optional live services (see [Configuration](#configuration))

### 1. Clone & Set Up

```bash
git clone https://github.com/your-org/earthguard-ai.git
cd earthguard-ai

# Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create `config/.env` from the template below:

```ini
# ── Database ─────────────────────────────
DB_USER=postgres
DB_PASSWORD=your_db_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=earthguard_db

# ── Flask ────────────────────────────────
FLASK_SECRET_KEY=change_me_to_a_long_random_string

# ── Region (Eastern Kenya reference point) ──
REGIONAL_LAT=-1.2921
REGIONAL_LONG=37.9942

# ── Africa's Talking (SMS) ───────────────
AT_USERNAME=sandbox
AT_API_KEY=your_at_api_key
AT_SENDER_ID=EARTHGUARD
AT_IS_SANDBOX=true

# ── SMTP (Email) ─────────────────────────
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
GMAIL_SENDER=alerts@earthguard.ai
GMAIL_APP_PASSWORD=your_app_password

# ── Safaricom Daraja (M-PESA) ────────────
MPESA_ENVIRONMENT=sandbox
MPESA_CONSUMER_KEY=your_consumer_key
MPESA_CONSUMER_SECRET=your_consumer_secret
MPESA_SHORTCODE=174379
MPESA_PASSKEY=bfb279f0929bdb0511d07142f21a0c28bea5ea772543fe284637c5fe96397383
MPESA_CALLBACK_URL=https://yourdomain.com/api/v1/mpesa/callback
```

### 3. Initialize the Database

```bash
python core/db_init.py
```

This creates the `earthguard_db` database (if missing), enables **PostGIS**, and builds all tables:

- `climate_records` — daily temperature, precipitation, evapotranspiration (+ geometry)
- `health_records` — county-level disease cases (Malaria, Cholera)
- `space_weather_records` — planetary Kp-index readings
- `users` — citizen accounts, subscriptions, trial & payment status
- `risk_alerts` — historical AI risk calculations per county
- `unsubscriptions` — opt-out feedback with reasons

### 4. Run the Application

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

### 5. Optional: Run Data Ingestion Pipelines

These populate the database with live/simulated data and can be scheduled via cron:

```bash
# Fetch and store recent climate metrics (Open-Meteo)
python -m services.climate_ingestion
python -m core.processor  # (used internally by ingestion helpers)

# Generate weekly health surveillance payload
python -m services.health_ingestion

# Fetch recent geomagnetic Kp indices (NOAA SWPC)
python -m services.space_weather_ingestion
```

---

## 🔬 How the AI Engine Works

### Anomaly Detection — Isolation Forest

`core/analytics.py` uses `sklearn.ensemble.IsolationForest` to detect multidimensional anomalies across five features:

1. Max temperature
2. Precipitation
3. Evapotranspiration
4. Reported disease cases
5. Geomagnetic Kp index

Raw anomaly scores are inverted and normalized to a **0 (safe) → 1 (extreme anomaly)** scale.

### Multi-Domain Fusion

Each sub-domain is normalized to 0–1 and combined using operational weights:

| Domain                 | Weight |
| ---------------------- | ------ |
| Climate severity       | 40%    |
| Health severity        | 40%    |
| Space weather severity | 20%    |

```
Composite Score = (0.40 × Climate) + (0.40 × Health) + (0.20 × Space) + (0.15 × Anomaly)
```

### Risk Classification

| Composite Score | Risk Level |
| --------------- | ---------- |
| < 0.35          | 🟢 Low     |
| 0.35 – 0.70     | 🟡 Medium  |
| ≥ 0.70          | 🔴 High    |

The engine persists every calculation to `risk_alerts` and triggers notification dispatch for Medium/High levels.

---

## 📡 API Endpoints

| Method   | Endpoint                       | Description                                           |
| -------- | ------------------------------ | ----------------------------------------------------- |
| GET      | `/`                            | Public landing page                                   |
| GET      | `/dashboard`                   | Authenticated user risk dashboard                     |
| GET      | `/telemetry`                   | Public live regional risk board                       |
| GET      | `/register` / POST             | Create citizen account                                |
| GET      | `/login` / POST                | Authenticate                                          |
| GET      | `/logout`                      | Clear session                                         |
| GET/POST | `/subscribe`                   | Configure alert channels & manage trial               |
| GET      | `/unsubscribe`                 | Email opt-out                                         |
| GET/POST | `/unsubscribe/reason`          | Collect unsubscribe feedback                          |
| GET      | `/api/v1/health`               | System health check                                   |
| GET      | `/api/v1/risk-status?county=X` | Live composite risk for a county                      |
| GET      | `/api/v1/alerts/history`       | Latest risk record per covered county                 |
| POST     | `/api/v1/mpesa/callback`       | Safaricom Daraja payment webhook                      |
| POST     | `/api/v1/sms/callback`         | Africa's Talking inbound SMS webhook (STOP / reasons) |
| GET      | `/api/v1/check-trial-expiry`   | Cron-friendly trial expiration sweeper                |

---

## 🧪 Testing & Validation

```bash
# Verify Python syntax
python -m py_compile app.py services/alert_service.py core/county_registry.py

# Run the analytics engine standalone
python core/analytics.py
```

---

## 📁 Project Structure

```
earthguard-ai/
├── app.py                          # Flask application & all routes
├── requirements.txt                # Python dependencies
├── config/
│   └── .env                        # Environment secrets (git-ignored)
├── core/
│   ├── analytics.py                # Isolation Forest + risk fusion engine
│   ├── county_registry.py          # Hazard blueprints & local advisories
│   ├── db_helper.py                # SQLAlchemy engine factory
│   ├── db_init.py                  # Database & schema initializer
│   └── processor.py                # Data normalization & storage pipeline
├── services/
│   ├── alert_service.py            # SMS/Email tiered alert dispatch
│   ├── climate_ingestion.py        # Open-Meteo climate fetcher
│   ├── health_ingestion.py         # Weekly health surveillance generator
│   ├── mpesa_service.py            # Safaricom Daraja STK Push
│   └── space_weather_ingestion.py  # NOAA Kp-index fetcher
├── static/
│   └── js/
│       └── dashboard.js            # Dashboard chart & API interactions
├── templates/
│   ├── _logo.html                  # Shared logo partial
│   ├── index.html                  # Authenticated dashboard
│   ├── landing.html                # Public marketing page
│   ├── login.html                  # Sign-in form
│   ├── register.html               # Registration form
│   ├── subscribe.html              # Subscription & payment page
│   ├── telemetry.html              # Public live risk board
│   └── unsubscribe_reason.html     # Opt-out feedback form
└── TODO.md                         # Refactor tracker & improvement ideas
```

---

## 🔒 Security Notes

- **Password Security:** Passwords are hashed with Werkzeug's `generate_password_hash` (PBKDF2/scrypt based); raw passwords are never stored.
- **Session Protection:** Server-side Flask sessions guarded by `FLASK_SECRET_KEY`; sessions cleared on logout.
- **SQL Injection Defense:** All queries use SQLAlchemy bound parameters (`:param`) instead of string concatenation.
- **Secrets Management:** API keys and DB credentials live in `config/.env`, excluded by `.gitignore`.
- **Webhook Verification:** Payment status is only mutated server-side via the authenticated Daraja callback flow.
- **Phone Normalization:** Inbound SMS phone numbers are normalized before lookup to prevent spoofing mismatches.

> ⚠️ **Development caveats:** `CORS(app)` is enabled globally and some HTTP clients use `verify=False` for sandbox SSL — review these before production deployment.

---

## 📈 Roadmap / Improvement Ideas

1. Centralize all user-facing copy into a single localization module (EN/SW).
2. Replace synthetic fallback baselines with seeded historical data.
3. Add dynamic weight tuning and time-series (LSTM/Prophet) forecasting.
4. Compile Tailwind CSS instead of using the CDN for rural low-bandwidth performance.
5. Add accessibility and contrast audits across all pages.
6. Introduce rate-limiting and stricter CORS policies in production.

---

## 📄 License

Proprietary / All Rights Reserved — EarthGuard AI. For demonstration and evaluation purposes.
