import os
import sys
import httpx
from datetime import datetime
from dotenv import load_dotenv

# Ensure configuration boundaries map accurately
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env from config directory with robust path resolution
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
load_dotenv(dotenv_path)

# Storage pipeline (wired lazily in __main__ to avoid a hard import-time
# dependency and keep the ingestion services importable in any context).
try:
    from core.processor import AthGadDataProcessor
except ImportError:
    AthGadDataProcessor = None


class ClimateIngestionService:
    def __init__(self):
        self.base_url = "https://api.open-meteo.com/v1/forecast"
        self.lat = os.getenv("REGIONAL_LAT", "-1.2921")
        self.lon = os.getenv("REGIONAL_LONG", "37.9942")

    def fetch_daily_climate_metrics(self) -> dict:
        """
        Extracts environmental variables matching project scope: 
        Temperature, Rainfall, and Evapotranspiration as drought indicators.
        """
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "et0_fao_evapotranspiration"],
            "timezone": "Africa/Nairobi",
            "past_days": 7,        # Fetch recent historical rolling window
            "forecast_days": 1
        }
        
        try:
            print(f"[{datetime.now()}] Initiating Climate Ingestion request for Coordinates: ({self.lat}, {self.lon})...")
            response = httpx.get(self.base_url, params=params, timeout=15.0)
            
            if response.status_code == 200:
                print("Climate Ingestion Engine: Extraction successful.")
                return response.json()
            else:
                print(f"Climate Ingestion Engine Warning: Received status code {response.status_code}")
                return {}
                
        except httpx.ConnectTimeout:
            print("Critical Connection Failure: Open-Meteo API timed out. Isolation fallback logged.")
            return {}
        except Exception as e:
            print(f"Unexpected Ingestion Error occurred: {str(e)}")
            return {}


if __name__ == "__main__":
    service = ClimateIngestionService()
    raw_data = service.fetch_daily_climate_metrics()
    print("Sample Ingested JSON Structure looks like:\n", list(raw_data.keys()))

    # Persist the fetched climate metrics to the database so the analytics
    # engine has real data instead of always relying on baseline fallbacks.
    if raw_data and AthGadDataProcessor is not None:
        processor = AthGadDataProcessor()
        processor.process_and_store_climate(raw_data)
        print("Climate Ingestion Pipeline: data persisted to climate_records.")
    else:
        print("Climate Ingestion Pipeline: no data to store (or processor unavailable).")

