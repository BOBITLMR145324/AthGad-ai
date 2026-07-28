import os
import sys
import httpx
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class SpaceWeatherIngestionService:
    def __init__(self):
        # Target NOAA 3-Day Planetary Kp Index Forecast Endpoint
        self.api_url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

    def fetch_geomagnetic_indices(self) -> list:
        """
        Pulls real-time 1-minute tracking interval planetary Kp-indices.
        """
        try:
            print(f"[{datetime.now()}] Ingesting Solar Dynamics data fields from NOAA SWPC...")
            response = httpx.get(self.api_url, timeout=15.0)
            
            if response.status_code == 200:
                all_records = response.json()
                # Return the latest 5 timeline records for staging window verification
                print(f"Space Weather Engine: Ingested {len(all_records)} structural array elements.")
                return all_records[-5:]
            else:
                print(f"Space Weather Ingestion Failure: Server status code {response.status_code}")
                return []
                
        except httpx.RequestError as exc:
            print(f"An infrastructure transmission exception occurred while requesting {exc.request.url!r}.")
            return []

if __name__ == "__main__":
    space_service = SpaceWeatherIngestionService()
    recent_kp_array = space_service.fetch_geomagnetic_indices()
    print("Latest Telemetry Samples Ingested:\n", recent_kp_array)