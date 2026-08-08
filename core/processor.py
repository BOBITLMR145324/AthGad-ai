import os
import sys
import json
import pandas as pd
try:
    from db_helper import get_db_engine
except ModuleNotFoundError:
    from core.db_helper import get_db_engine
from sqlalchemy import text
from core.id_codes import (
    new_climate_code,
    new_health_code,
    new_space_weather_code,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class AthGadDataProcessor:
    def __init__(self):
        self.engine = get_db_engine()

    def _to_sql(self, df: pd.DataFrame, table: str):
        """
        Safely appends a DataFrame to a database table.
        Returns True on success, False on any database error.
        """
        try:
            df.to_sql(table, con=self.engine, if_exists='append', index=False)
            return True
        except Exception as e:
            print(f"Processor: Could not write to '{table}': {e}")
            return False

    def process_and_store_climate(self, raw_climate_json: dict):
        if not raw_climate_json or 'daily' not in raw_climate_json:
            print("Processor Warning: Empty climate payload received.")
            return

        daily_data = raw_climate_json['daily']
        lat = raw_climate_json.get('latitude')
        lon = raw_climate_json.get('longitude')

        df = pd.DataFrame({
            'timestamp': pd.to_datetime(daily_data['time']),
            'temperature_max': daily_data['temperature_2m_max'],
            'temperature_min': daily_data['temperature_2m_min'],
            'precipitation_sum': daily_data['precipitation_sum'],
            'evapotranspiration': daily_data['et0_fao_evapotranspiration']
        })
        df.dropna(subset=['timestamp'], inplace=True)
        df.fillna(df.mean(numeric_only=True), inplace=True)

        if df.empty:
            print("Processor: No climate rows to store after cleaning.")
            return

        # Assign a human-readable, domain-specific primary key per row
        df['climate_code'] = [new_climate_code(ts) for ts in df['timestamp']]

        # Append structured alphanumeric columns using Pandas to_sql
        if not self._to_sql(df, 'climate_records'):
            return

        # Update the PostGIS geometry point columns for new coordinates using native SQL
        try:
            with self.engine.begin() as connection:
                connection.execute(text("""
                    UPDATE climate_records 
                    SET geom = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                    WHERE geom IS NULL;
                """), {"lon": lon, "lat": lat})
        except Exception as e:
            print(f"Processor: Geometry update warning (PostGIS may be unavailable): {e}")

        print(f"Processor: Appended {len(df)} climate metrics via SQLAlchemy.")

    def process_and_store_space_weather(self, raw_kp_list: list):
        if not raw_kp_list:
            return

        df = pd.DataFrame(raw_kp_list)
        df['timestamp'] = pd.to_datetime(df['time_tag'])
        df['kp_index'] = pd.to_numeric(df['kp_index'], errors='coerce')
        df = df[['timestamp', 'kp_index']].dropna()

        if df.empty:
            return

        # Assign a human-readable, domain-specific primary key per row
        df['space_weather_code'] = [new_space_weather_code(ts) for ts in df['timestamp']]

        # Stream directly to database table using SQLAlchemy connection pool
        if not self._to_sql(df, 'space_weather_records'):
            return
        print(f"Processor: Synchronized {len(df)} space telemetry records via SQLAlchemy.")

    def process_and_store_health(self, raw_health_json_str: str):
        try:
            records = json.loads(raw_health_json_str)
        except json.JSONDecodeError:
            return

        df = pd.DataFrame(records)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['reported_cases'] = pd.to_numeric(df['reported_cases'], errors='coerce').fillna(0).astype(int)

        # Rename dataframe columns to exactly match database table definitions
        df.rename(columns={'facility_reporting_rate_pct': 'reporting_rate'}, inplace=True)

        # Drop the 'source' column — it does NOT exist in the health_records
        # table schema and would cause an INSERT failure at write time.
        if 'source' in df.columns:
            df = df.drop(columns=['source'])

        if df.empty:
            return

        # Assign a human-readable, domain-specific primary key per row that
        # encodes the county, disease and observation date.
        df['health_code'] = [
            new_health_code(county, disease, ts)
            for county, disease, ts in zip(df['county'], df['disease_type'], df['timestamp'])
        ]

        if not self._to_sql(df, 'health_records'):
            return
        print(f"Processor: Parsed {len(df)} health observations via SQLAlchemy.")

if __name__ == "__main__":
    print("Database processing pipelines refactored for SQLAlchemy execution.")