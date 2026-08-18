import os
import re
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env from config directory with robust path resolution
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
load_dotenv(dotenv_path)

# PostgreSQL identifiers may contain only lowercase/uppercase letters, digits,
# underscores, and dollar signs (and must not start with a digit). Enforcing
# this prevents SQL-injection / broken DDL when DB_NAME comes from the .env.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _validate_db_name(db_name: str) -> str:
    """Validates the DB_NAME value so it is safe to interpolate into DDL."""
    db_name = (db_name or "AthGad_db").strip()
    if not _IDENTIFIER_RE.match(db_name):
        raise ValueError(
            f"DB_NAME '{db_name}' is not a valid PostgreSQL identifier. "
            "Use only letters, digits, underscores or '$', and do not start with a digit."
        )
    return db_name


def _table_columns(cursor, table: str) -> dict:
    """Returns {column_name: data_type} for a table (used for idempotent DDL)."""
    cursor.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s",
        (table,),
    )
    rows = cursor.fetchall()
    return {row[0]: row[1] for row in rows}


def _backfill_codes(cursor, table: str, col: str, extra_cols: list, generator) -> int:
    """
    Replaces integer-looking primary-key values on existing rows with freshly
    generated domain codes. Already-coded rows (idempotent re-runs) are left
    untouched. `generator` receives a dict of the row's extra columns.
    """
    extra_expr = "".join(f', "{c}"' for c in extra_cols)
    select_sql = f'SELECT "{col}"{extra_expr} FROM {table}'
    cursor.execute(select_sql)
    rows = cursor.fetchall()
    col_index = {c: i for i, c in enumerate([col] + extra_cols)}
    updated = 0
    for row in rows:
        current = row[col_index[col]]
        if current is None or not re.fullmatch(r"[0-9]+", str(current)):
            continue
        data = {c: row[col_index[c]] for c in extra_cols}
        code = generator(data)
        cursor.execute(
            f'UPDATE {table} SET "{col}" = %s WHERE "{col}" = %s',
            (code, current),
        )
        updated += 1
    return updated


def _ensure_code_pk(cursor, table: str, col: str, col_type: str, pk_name: str):
    """Verifies a code column is correctly typed and keyed (idempotent)."""
    current_type = _table_columns(cursor, table).get(col)
    if current_type and current_type != "character varying":
        cursor.execute(
            f'ALTER TABLE {table} ALTER COLUMN "{col}" TYPE {col_type} USING "{col}"::text'
        )
    cursor.execute(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'",
        (table,),
    )
    pk_cols = cursor.fetchall()
    if not pk_cols or pk_cols[0][0] != col:
        cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {pk_name}')
        cursor.execute(f'ALTER TABLE {table} ADD PRIMARY KEY ("{col}")')


def _migrate_one(cursor, spec: dict):
    """
    Converts a legacy `id SERIAL PRIMARY KEY` column into a domain-specific
    prefixed alphanumeric code column, back-filling existing rows. Idempotent.
    """
    table = spec["table"]
    new = spec["new"]
    new_type = spec["type"]
    pk_name = f"{table}_pkey"

    cols = _table_columns(cursor, table)
    if new in cols and "id" not in cols:
        # Already migrated (or created fresh with the new schema). Ensure the
        # column type and primary key are correct, then stop.
        _ensure_code_pk(cursor, table, new, new_type, pk_name)
        return
    if "id" not in cols:
        return  # No legacy id column to migrate

    # 1. Rename the legacy SERIAL id column into the domain code column.
    cursor.execute(f'ALTER TABLE {table} RENAME COLUMN "id" TO "{new}"')
    # 2. Drop the auto-increment default so integers stop being generated.
    cursor.execute(f'ALTER TABLE {table} ALTER COLUMN "{new}" DROP DEFAULT')
    # 3. Retype the column to a human-readable code string.
    cursor.execute(
        f'ALTER TABLE {table} ALTER COLUMN "{new}" TYPE {new_type} USING "{new}"::text'
    )
    # 4. Back-fill generated codes for every existing row.
    updated = _backfill_codes(
        cursor, table, new,
        [c for c in spec["extra_cols"] if c in cols],
        spec["generator"],
    )
    # 5. Re-key the table on the new code column.
    cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {pk_name}')
    cursor.execute(f'ALTER TABLE {table} ALTER COLUMN "{new}" SET NOT NULL')
    cursor.execute(f'ALTER TABLE {table} ADD PRIMARY KEY ("{new}")')
    # 6. Drop the orphaned SERIAL sequence (owned by the old column).
    cursor.execute(f'DROP SEQUENCE IF EXISTS {table}_id_seq CASCADE')
    print(f"Migrated {table}.id -> {table}.{new} ({updated} row(s) re-keyed).")


def _migrate_serial_pk_to_code(cursor):
    """
    Idempotent migration: converts legacy `id SERIAL PRIMARY KEY` columns on
    the core tables into domain-specific prefixed alphanumeric code columns.
    Safe to run on every startup (fresh and existing databases alike).
    """
    from core.id_codes import (
        new_climate_code,
        new_health_code,
        new_space_weather_code,
        new_user_code,
        new_alert_code,
    )

    specs = [
        {
            "table": "climate_records",
            "new": "climate_code",
            "type": "VARCHAR(40)",
            "extra_cols": ["timestamp"],
            "generator": lambda d: new_climate_code(d.get("timestamp")),
        },
        {
            "table": "health_records",
            "new": "health_code",
            "type": "VARCHAR(50)",
            "extra_cols": ["county", "disease_type", "timestamp"],
            "generator": lambda d: new_health_code(
                d.get("county"), d.get("disease_type"), d.get("timestamp")
            ),
        },
        {
            "table": "space_weather_records",
            "new": "space_weather_code",
            "type": "VARCHAR(40)",
            "extra_cols": ["timestamp"],
            "generator": lambda d: new_space_weather_code(d.get("timestamp")),
        },
        {
            "table": "users",
            "new": "user_code",
            "type": "VARCHAR(40)",
            "extra_cols": ["registered_at"],
            "generator": lambda d: new_user_code(d.get("registered_at")),
        },
        {
            "table": "risk_alerts",
            "new": "alert_code",
            "type": "VARCHAR(40)",
            "extra_cols": ["county", "timestamp"],
            "generator": lambda d: new_alert_code(d.get("county"), d.get("timestamp")),
        },
    ]

    for spec in specs:
        _migrate_one(cursor, spec)


def _ensure_indexes(cursor):
    """
    Idempotent performance indexes for the hot query paths:
      - risk_alerts(county, timestamp): latest-alert-per-county history board
      - users(trial_ends_at): scheduled trial-expiry scan
      - health_records(county, disease_type, timestamp): recent per-county disease
      - climate/space weather (timestamp): "latest record" ingestion lookups
      - unsubscriptions(email, unsubscribed_at): feedback joins + report ordering
    """
    index_specs = [
        ("idx_risk_alerts_county_ts", "risk_alerts (county, timestamp DESC)"),
        ("idx_risk_alerts_timestamp", "risk_alerts (timestamp DESC)"),
        ("idx_users_trial_ends", "users (trial_ends_at)"),
        ("idx_users_county", "users (county)"),
        ("idx_health_county_disease_ts",
         "health_records (county, disease_type, timestamp DESC)"),
        ("idx_health_timestamp", "health_records (timestamp DESC)"),
        ("idx_climate_timestamp", "climate_records (timestamp DESC)"),
        ("idx_space_timestamp", "space_weather_records (timestamp DESC)"),
        ("idx_unsub_email", "unsubscriptions (email)"),
        ("idx_unsub_unsubscribed_at", "unsubscriptions (unsubscribed_at DESC)"),
        ("idx_sms_delivery_logged_at", "sms_delivery_logs (logged_at DESC)"),
        ("idx_dispatch_dispatched_at", "alert_dispatch_logs (dispatched_at DESC)"),
        ("idx_dispatch_recipient", "alert_dispatch_logs (recipient)"),
    ]
    for name, definition in index_specs:
        cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition};")
    print(f"Ensured {len(index_specs)} performance indexes.")


def initialize_database():
    # Connection parameters to default postgres db to create the new database
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD")
    db_name = _validate_db_name(os.getenv("DB_NAME", "AthGad_db"))

    # Connect to default postgres database to execute CREATE DATABASE
    conn = psycopg2.connect(host=host, port=port, user=user, password=password, database="postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    try:
        cursor.execute(f"CREATE DATABASE {db_name};")
        print(f"Database '{db_name}' created successfully.")
    except psycopg2.errors.DuplicateDatabase:
        print(f"Database '{db_name}' already exists. Proceeding with table generation...")
    finally:
        cursor.close()
        conn.close()

    # Reconnect to the target AthGad DB to initialize extensions and tables
    conn = psycopg2.connect(host=host, port=port, user=user, password=password, database=db_name)
    cursor = conn.cursor()

    try:
        # 1. Enable PostGIS Extension
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        print("Geospatial Extension (PostGIS) enabled.")

        # 2. Climate Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS climate_records (
                climate_code VARCHAR(40) PRIMARY KEY,  -- human-readable key: CLI-YYYYMMDD-XXXXXX
                timestamp TIMESTAMP NOT NULL,
                temperature_max NUMERIC(5,2),
                temperature_min NUMERIC(5,2),
                precipitation_sum NUMERIC(5,2),
                evapotranspiration NUMERIC(5,2),
                geom GEOMETRY(Point, 4326) -- Stores spatial latitude/longitude coordinates
            );
        """)

        # 3. Public Health Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_records (
                health_code VARCHAR(50) PRIMARY KEY,  -- human-readable key: HLT-<county>-<disease>-YYYYMMDD-XXXX
                timestamp TIMESTAMP NOT NULL,
                county VARCHAR(50) NOT NULL,
                disease_type VARCHAR(30) NOT NULL,
                reported_cases INT NOT NULL,
                reporting_rate NUMERIC(4,1)
            );
        """)

        # 4. Space Weather Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS space_weather_records (
                space_weather_code VARCHAR(40) PRIMARY KEY,  -- human-readable key: SPW-YYYYMMDD-HHMMSS-XXXX
                timestamp TIMESTAMP NOT NULL,
                kp_index NUMERIC(3,1) NOT NULL
            );
        """)

        # 5. Users Table (Registration, Authentication & Subscription Management)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_code VARCHAR(40) PRIMARY KEY,  -- human-readable key: USR-YYYYMMDD-XXXXXX
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
        """)

        # 5b. Unsubscriptions Table (Tracks opt-outs + optional reasons)
        # Primary key uses the person's full name; if the same name appears more
        # than once, a numeric suffix (1, 2, ...) is appended to keep it unique.
        # ── Migration: Drop old-format table if it lacks the new `unsub_ref` column ──
        # Old-format variants used either `user_id` OR `id`/`full_name`/`raw_reply`;
        # both are detected here and safely rebuilt with legacy rows preserved.
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'unsubscriptions'
        """)
        existing_unsub_columns = [row[0] for row in cursor.fetchall()]

        if existing_unsub_columns and 'unsub_ref' not in existing_unsub_columns:
            # Preserve legacy feedback rows before dropping the old-format table
            try:
                cursor.execute("""
                    SELECT full_name, channel, reason, unsubscribed_at
                    FROM unsubscriptions
                    WHERE full_name IS NOT NULL
                """)
                legacy_rows = cursor.fetchall()
            except Exception as e:
                print(f"Could not read legacy unsubscriptions rows: {e}")
                legacy_rows = []

            cursor.execute("DROP TABLE unsubscriptions")
            print(f"Dropped old-format unsubscriptions table (detected {len(existing_unsub_columns)} legacy columns).")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unsubscriptions (
                unsub_ref VARCHAR(150) PRIMARY KEY,  -- person's full name (+ numeric suffix on duplicates)
                channel VARCHAR(10) NOT NULL,        -- 'sms' or 'email'
                reason VARCHAR(255),
                email VARCHAR(120),                  -- unique identity used to join feedback to users
                suggested_answer VARCHAR(255) GENERATED ALWAYS AS (
                    CASE
                        WHEN reason IS NOT NULL AND reason <> '' THEN reason
                        ELSE NULL
                    END
                ) STORED,
                unsubscribed_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # 5c. Idempotent migration: add the email column to existing databases so
        # feedback rows can be joined to users on the unique email address
        # instead of the non-unique display name.
        cursor.execute("ALTER TABLE unsubscriptions ADD COLUMN IF NOT EXISTS email VARCHAR(120);")

        # Re-insert any legacy feedback rows under the new schema with unique unsub_ref keys
        if existing_unsub_columns and 'unsub_ref' not in existing_unsub_columns:
            seen_names = {}
            inserted = 0
            for row in legacy_rows:
                legacy_full_name, legacy_channel, legacy_reason, legacy_unsub_at = row
                base_name = (legacy_full_name or "Unknown").strip()
                if base_name in seen_names:
                    seen_names[base_name] += 1
                    unsub_ref = f"{base_name}{seen_names[base_name]}"
                else:
                    seen_names[base_name] = 0
                    unsub_ref = base_name
                cursor.execute(
                    "INSERT INTO unsubscriptions (unsub_ref, channel, reason, unsubscribed_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (unsub_ref, legacy_channel or 'email', legacy_reason, legacy_unsub_at)
                )
                inserted += 1
            if inserted:
                print(f"Preserved {inserted} legacy unsubscription feedback record(s) under the new schema.")

        # 5c. Idempotent migrations for existing databases
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMP;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dispatch_preference VARCHAR(10) DEFAULT 'sms';")

        # 5d. Admin role + subscription tracking (idempotent, for existing DBs)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'citizen';")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_started_at TIMESTAMP;")
        # 5e. Per-county alert targeting (idempotent). Stores the county a user
        #     is most interested in so alerts can be scoped to their area.
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS county VARCHAR(50);")

        # 6. Risk Alerts Staging Table (For Phase 4 downstream alerts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_alerts (
                alert_code VARCHAR(40) PRIMARY KEY,  -- human-readable key: RA-<county>-YYYYMMDDHHMMSS-XXXX
                timestamp TIMESTAMP NOT NULL,
                county VARCHAR(50) NOT NULL,
                calculated_score NUMERIC(4,3) NOT NULL,
                risk_level VARCHAR(15) NOT NULL,
                notified BOOLEAN DEFAULT FALSE,
                dispatched BOOLEAN DEFAULT FALSE  -- set TRUE when SMS/email dispatch has run for this row
            );
        """)
        # 6a. Dispatch-dedup column for existing databases (idempotent).
        # Tracks whether a Medium/High alert for a county+level has already been
        # dispatched so repeat /api/v1/risk-status polls do not re-notify users.
        cursor.execute("ALTER TABLE risk_alerts ADD COLUMN IF NOT EXISTS dispatched BOOLEAN DEFAULT FALSE;")

        # 6b. SMS Delivery Reports Table
        # Records every outbound SMS attempt and its Africa's Talking outcome so
        # failures/successes can be debugged from the admin workspace.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sms_delivery_logs (
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
        """)

        # 6c. Alert Dispatch Tracking Table
        # Tracks every dispatched SMS/email alert with the recipient identifier
        # (phone number for SMS, email address for email), the exact message that
        # was sent, and the recipient's subscription status at dispatch time so
        # admins can audit and debug exactly what was sent to whom.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_dispatch_logs (
                id SERIAL PRIMARY KEY,
                dispatch_code VARCHAR(40) UNIQUE,      -- human-readable key: DS-YYYYMMDDHHMMSS-XXXX
                dispatched_at TIMESTAMP DEFAULT NOW(),
                channel VARCHAR(10) NOT NULL,          -- 'sms' or 'email'
                recipient VARCHAR(120) NOT NULL,       -- phone number (sms) or email address
                message_type VARCHAR(30) DEFAULT 'alert',
                message_content TEXT,                  -- the exact message that was sent
                subscription_status VARCHAR(30) DEFAULT 'unknown',  -- premium / baseline / none
                status VARCHAR(20) NOT NULL,           -- SUCCESS / FAILED / SIMULATED
                error_detail VARCHAR(500)
            );
        """)

        # 6d. M-PESA STK Payment Tracking Table
        # Records every STK push we initiate so the callback webhook can verify
        # the CheckoutRequestID, amount, phone, and replay state before marking
        # a user as paid. Prevents forged callbacks from activating premium.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mpesa_stk_requests (
                id SERIAL PRIMARY KEY,
                checkout_id VARCHAR(100) NOT NULL UNIQUE,
                user_email VARCHAR(255) NOT NULL,
                phone_number VARCHAR(20) NOT NULL,
                amount NUMERIC(10, 0) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',  -- pending / success / failed
                initiated_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                mpesa_receipt VARCHAR(40)
            );
        """)

        # 7. Human-readable primary-key migration: converts any legacy
        #    `id SERIAL PRIMARY KEY` columns into domain-specific prefixed
        #    alphanumeric codes, preserving all existing rows.
        _migrate_serial_pk_to_code(cursor)

        # 8. Performance indexes for the hot query paths (idempotent).
        _ensure_indexes(cursor)

        conn.commit()
        print("All database relations, schemas, and structural constraints committed successfully.")

    except Exception as e:
        print(f"Error initializing DB schema: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    initialize_database()