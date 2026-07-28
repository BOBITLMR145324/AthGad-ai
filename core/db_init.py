import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv('config/.env')

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

        # 5. Risk Alerts Staging Table (For Phase 4 downstream alerts)
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