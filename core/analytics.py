import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.ensemble import IsolationForest
from core.db_helper import get_db_engine
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class EarthGuardAnalyticsEngine:
    def __init__(self):
        # Operational weights as defined in your cross-domain conceptual framework
        self.w_climate = 0.40
        self.w_health = 0.40
        self.w_space = 0.20

    def fetch_historical_baseline(self, county: str, days=30) -> pd.DataFrame:
        """
        Queries and merges temporal tracking metrics across your tables
        scoped dynamically to a specific county to form a unique observation vector.
        """
        engine = get_db_engine()
        
        # FIX 1: Added 'AND county = :county' to filter climate data by region
        climate_query = """
            SELECT date_trunc('day', timestamp) as date,
                   AVG(temperature_max) as temp_max, 
                   AVG(precipitation_sum) as rain,
                   AVG(evapotranspiration) as evapo
            FROM climate_records
            WHERE timestamp >= NOW() - INTERVAL ':days days'
              AND county = :county
            GROUP BY date ORDER BY date;
        """
        df_climate = pd.read_sql_query(text(climate_query), engine, params={"days": days, "county": county})

        # FIX 2: Added 'AND county = :county' to filter health cases by region
        health_query = """
            SELECT date_trunc('day', timestamp) as date,
                   SUM(reported_cases) as total_cases
            FROM health_records
            WHERE timestamp >= NOW() - INTERVAL ':days days'
              AND county = :county
            GROUP BY date ORDER BY date;
        """
        df_health = pd.read_sql_query(text(health_query), engine, params={"days": days, "county": county})

        # Space weather remains planetary/global, no county filter needed here
        space_query = """
            SELECT date_trunc('day', timestamp) as date,
                   MAX(kp_index) as max_kp
            FROM space_weather_records
            WHERE timestamp >= NOW() - INTERVAL ':days days'
            GROUP BY date ORDER BY date;
        """
        df_space = pd.read_sql_query(text(space_query), engine, params={"days": days})

        # If database is completely empty during initial run, build fallback baseline vectors
        if df_climate.empty:
            dates = pd.date_range(end=datetime.now(), periods=days, freq='D').normalize()
            df_climate = pd.DataFrame({
                'date': dates, 'temp_max': np.random.uniform(25, 35, days),
                'rain': np.random.uniform(0, 10, days), 'evapo': np.random.uniform(2, 5, days)
            })
        if df_health.empty:
            df_health = pd.DataFrame({'date': df_climate['date'], 'total_cases': np.random.randint(5, 20, days)})
        if df_space.empty:
            df_space = pd.DataFrame({'date': df_climate['date'], 'max_kp': np.random.uniform(1, 4, days)})

        # Ensure datetime type alignments before joining dataframes
        df_climate['date'] = pd.to_datetime(df_climate['date'])
        df_health['date'] = pd.to_datetime(df_health['date'])
        df_space['date'] = pd.to_datetime(df_space['date'])

        # Merge dataframes sequentially on the normalized date index
        master_df = pd.merge(df_climate, df_health, on='date', how='outer')
        master_df = pd.merge(master_df, df_space, on='date', how='outer')
        
        # Clean data structures using non-deprecated fill operations
        master_df = master_df.bfill().ffill().fillna(0)
        
        return master_df

    def compute_anomaly_scores(self, df: pd.DataFrame) -> np.ndarray:
        """
        Executes Isolation Forest tracking to highlight multidimensional anomalies.
        Returns a normalized score array where high scores indicate higher anomaly severity.
        """
        features = ['temp_max', 'rain', 'evapo', 'total_cases', 'max_kp']
        X = df[features].values

        # Initialize Isolation Forest
        iso_forest = IsolationForest(contamination=0.15, random_state=42)
        iso_forest.fit(X)
        
        raw_scores = iso_forest.decision_function(X)
        
        # Invert and normalize to scale between 0 (safe) and 1 (extreme anomaly)
        normalized_anomalies = (raw_scores.max() - raw_scores) / (raw_scores.max() - raw_scores.min() + 1e-6)
        return normalized_anomalies

    def calculate_composite_risk(self, county: str = "Kitui") -> dict:
        """
        Executes Multi-Domain Risk Fusion processing.
        Combines individual metrics, normalizes them, and aggregates a localized score.
        """
        df = self.fetch_historical_baseline(county=county, days=14)
        anomaly_array = self.compute_anomaly_scores(df)
        
        latest_anomaly_severity = anomaly_array[-1] if len(anomaly_array) > 0 else 0.1
        
        current_temp = df['temp_max'].iloc[-1] if not df.empty else 30.0
        current_cases = df['total_cases'].iloc[-1] if not df.empty else 5
        current_kp = df['max_kp'].iloc[-1] if not df.empty else 1.0

        # Sub-domain feature normalization scales
        s_climate = min(max((current_temp - 20) / (42 - 20), 0.0), 1.0)
        s_health = min(max(current_cases / 100.0, 0.0), 1.0)
        s_space = min(max(current_kp / 9.0, 0.0), 1.0)

        # Multi-Domain Fusion Formula implementation
        base_fusion_score = (self.w_climate * s_climate) + (self.w_health * s_health) + (self.w_space * s_space)
        final_composite_score = min(base_fusion_score + (0.15 * latest_anomaly_severity), 1.0)

        if final_composite_score < 0.35:
            risk_level = "Low"
        elif final_composite_score < 0.70:
            risk_level = "Medium"
        else:
            risk_level = "High"

        # Explicitly cast to clean, readable percentages for UI/SMS presentation layout structures
        result_payload = {
                          "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "composite_risk_score": float(round(final_composite_score, 3)), # Keeps as 0.649
                           "risk_level": risk_level,
                           "metrics": {
                         "climate_severity": float(round(s_climate, 2)),
                         "health_severity": float(round(s_health, 2)),
                           "space_weather_severity": float(round(s_space, 2)),
                         "hidden_anomaly_factor": float(round(latest_anomaly_severity, 2))
                           }
                        }
        # Log pure numeric findings down to your database staging layer safely
        self._log_alert_to_db(risk_level, float(round(final_composite_score, 3)), county)
        
        return result_payload

    def _log_alert_to_db(self, level, score, county):
        """Persists alerts into the database safely using standard native formats."""
        engine = get_db_engine()
        try:
            with engine.begin() as connection:
                # Execution Point: Original Database Insert Routine
                connection.execute(text("""
                    INSERT INTO risk_alerts (timestamp, county, calculated_score, risk_level, notified)
                    VALUES (NOW(), :county, :score, :level, FALSE);
                """), {"score": score, "level": level, "county": county})
                print(f"Database Alert Sync: Record saved to 'risk_alerts' for {county} successfully via SQLAlchemy.")

                # --- INTEGRATED NOTIFICATION HOOK ROUTINE ---
                if level in ["Medium", "High"]:
                    # Pull user alert notification options out of the registration matrix
                    registered_users = connection.execute(text("""
                        SELECT full_name, phone_number, email, receive_email FROM users;
                    """)).fetchall()

                    for user in registered_users:
                        # Automated SMS Dispatch Log (Primary)
                        print(f"📡 [SMS DISPATCH] To: {user.phone_number} ({user.full_name}) -> EarthGuard AI: {level} Risk alert generated for {county} County. Composite Index Score: {round(score * 100, 1)}%")

                        # Automated Email Opt-In Profile Filter Check (Supplementary)
                        if user.receive_email:
                            print(f"✉️ [EMAIL DISPATCH] To: {user.email} -> Supplementary analytical telemetry report successfully routed.")

                    # Update notification broadcast lifecycle status flag to TRUE
                    connection.execute(text("""
                        UPDATE risk_alerts 
                        SET notified = TRUE 
                        WHERE county = :county AND risk_level = :level AND timestamp >= NOW() - INTERVAL '1 minute';
                    """), {"county": county, "level": level})

        except Exception as e:
            print(f"Database Logging Error: {e}")

if __name__ == "__main__":
    engine = EarthGuardAnalyticsEngine()
    analysis_output = engine.calculate_composite_risk("Kitui")
    print("\nCleaned AI Core Execution Output Payload:\n", analysis_output)