import sys
import os
import random
from datetime import datetime, timedelta
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class HealthSurveillanceDataEngine:
    def __init__(self):
        self.target_counties = ["Machakos", "Kitui", "Makueni", "Marsabit", "Isiolo", "Meru", "Tharaka-Nithi", "Embu"]
        self.diseases = ["Malaria", "Cholera"]

    def generate_weekly_surveillance_payload(self) -> str:
        """
        Generates production-grade anonymized public health report snapshots.
        Includes anomalous variance spikes to evaluate downstream Isolation Forest models.
        """
        payload = []
        current_date = datetime.now()

        for county in self.target_counties:
            for disease in self.diseases:
                # Introduce structural baseline logic 
                # (e.g., higher baseline values for Malaria overall, rare spikes for Cholera)
                base_cases = random.randint(15, 60) if disease == "Malaria" else random.randint(0, 4)
                
                # Anomaly injection logic (10% chance to simulate outbreak threshold trigger)
                if random.random() < 0.10:
                    base_cases *= random.randint(4, 8)
                
                record = {
                    "timestamp": current_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "county": county,
                    "disease_type": disease,
                    "reported_cases": base_cases,
                    "facility_reporting_rate_pct": round(random.uniform(85.0, 99.9), 1)
                }
                payload.append(record)
                
        return json.dumps(payload, indent=4)

if __name__ == "__main__":
    health_engine = HealthSurveillanceDataEngine()
    mock_payload = health_engine.generate_weekly_surveillance_payload()
    print("Generated Production Diagnostic Ingestion Output:\n", mock_payload)