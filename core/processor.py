import os
import sys
import json
import pandas as pd
try:
    from db_helper import get_db_engine
except ModuleNotFoundError:
    from core.db_helper import get_db_engine
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class EarthGuardDataProcessor:
    def __init__(self):
        self.engine = get_db_engine()

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

        # Append structured alphanumeric columns using Pandas to_sql
        df.to_sql('climate_records', con=self.engine, if_exists='append', index=False)

        # Update the PostGIS geometry point columns for new coordinates using native SQL
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE climate_records 
                SET geom = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                WHERE geom IS NULL;
            """), {"lon": lon, "lat": lat})
            
        print(f"Processor: Appended {len(df)} climate metrics via SQLAlchemy.")

    def process_and_store_space_weather(self, raw_kp_list: list):
        if not raw_kp_list: 
            return
        
        df = pd.DataFrame(raw_kp_list)
        df['timestamp'] = pd.to_datetime(df['time_tag'])
        df['kp_index'] = pd.to_numeric(df['kp_index'], errors='coerce')
        df = df[['timestamp', 'kp_index']].dropna()

        # Stream directly to database table using SQLAlchemy connection pool
        df.to_sql('space_weather_records', con=self.engine, if_exists='append', index=False)
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
        
        df.to_sql('health_records', con=self.engine, if_exists='append', index=False)
        print(f"Processor: Parsed {len(df)} health observations via SQLAlchemy.")

if __name__ == "__main__":
    print("Database processing pipelines refactored for SQLAlchemy execution.")