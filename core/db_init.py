import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env from config directory with robust path resolution
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
load_dotenv(dotenv_path)

def initialize_database():
    # Connection parameters to default postgres db to create the new database
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME", "earthguard_db")

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

    # Reconnect to the target EarthGuard DB to initialize extensions and tables
    conn = psycopg2.connect(host=host, port=port, user=user, password=password, database=db_name)
    cursor = conn.cursor()

    try:
        # 1. Enable PostGIS Extension
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        print("Geospatial Extension (PostGIS) enabled.")

        # 2. Climate Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS climate_records (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                kp_index NUMERIC(3,1) NOT NULL
            );
        """)

        # 5. Users Table (Registration, Authentication & Subscription Management)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                email VARCHAR(120) NOT NULL UNIQUE,
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
                created_at TIMESTAMP DEFAULT NOW()
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
                suggested_answer VARCHAR(255) GENERATED ALWAYS AS (
                    CASE
                        WHEN reason IS NOT NULL AND reason <> '' THEN reason
                        ELSE NULL
                    END
                ) STORED,
                unsubscribed_at TIMESTAMP DEFAULT NOW()
            );
        """)

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

        # 6. Risk Alerts Staging Table (For Phase 4 downstream alerts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_alerts (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                county VARCHAR(50) NOT NULL,
                calculated_score NUMERIC(4,3) NOT NULL,
                risk_level VARCHAR(15) NOT NULL,
                notified BOOLEAN DEFAULT FALSE
            );
        """)

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