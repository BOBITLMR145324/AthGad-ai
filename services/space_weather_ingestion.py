import os
import sys
import logging
import httpx
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# Storage pipeline (wired lazily in __main__ to avoid a hard import-time
# dependency and keep the ingestion services importable in any context).
try:
    from core.processor import AthGadDataProcessor
except ImportError:
    AthGadDataProcessor = None


class SpaceWeatherIngestionService:
    def __init__(self):
        # Target NOAA 3-Day Planetary Kp Index Forecast Endpoint
        self.api_url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

    def fetch_geomagnetic_indices(self) -> list:
        """
        Pulls real-time 1-minute tracking interval planetary Kp-indices.
        Returns the latest records covering roughly a 30-day window so the
        analytics engine's observation vector is populated with real data
        instead of falling back to deterministic baselines.
        """
        try:
            logger.info("Ingesting Solar Dynamics data fields from NOAA SWPC...")
            response = httpx.get(self.api_url, timeout=15.0)
            
            if response.status_code == 200:
                all_records = response.json()
                # The NOAA endpoint returns ~1 record per 3 hours. A 30-day
                # window is ~240 records; keep the latest 240 so the analytics
                # engine has a full month of real geomagnetic data.
                logger.info("Space Weather Engine: Ingested %d structural array elements.", len(all_records))
                return all_records[-240:]
            else:
                logger.warning("Space Weather Ingestion Failure: Server status code %s", response.status_code)
                return []
                
        except httpx.RequestError as exc:
            logger.error("An infrastructure transmission exception occurred while requesting %r.", exc.request.url)
            return []


if __name__ == "__main__":
    space_service = SpaceWeatherIngestionService()
    recent_kp_array = space_service.fetch_geomagnetic_indices()
    print("Latest Telemetry Samples Ingested:\n", recent_kp_array)

    # Persist the fetched space-weather records so the analytics engine has
    # real geomagnetic data instead of only baseline fallbacks.
    if recent_kp_array and AthGadDataProcessor is not None:
        processor = AthGadDataProcessor()
        processor.process_and_store_space_weather(recent_kp_array)
        print("Space Weather Ingestion Pipeline: data persisted to space_weather_records.")
    else:
        print("Space Weather Ingestion Pipeline: no data to store (or processor unavailable).")

