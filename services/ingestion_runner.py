import os
import sys
import json
from datetime import datetime, timedelta

from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.processor import AthGadDataProcessor
from core.db_helper import get_db_engine
from services.health_ingestion import HealthSurveillanceDataEngine
from services.climate_ingestion import ClimateIngestionService
from services.space_weather_ingestion import SpaceWeatherIngestionService


def _store_health_payload(processor: AthGadDataProcessor, payload_json: str) -> int:
    """
    Persists a weekly health surveillance payload after removing any
    same-day rows for the affected counties, so repeated refreshes do not
    double-count reported cases in the daily aggregates.
    """
    try:
        records = json.loads(payload_json)
    except json.JSONDecodeError:
        records = []

    if not records:
        return 0

    counties = {r.get("county") for r in records if r.get("county")}

    try:
        ts = datetime.strptime(records[0]["timestamp"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        ts = datetime.now()

    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    try:
        placeholders = ",".join(f":c{i}" for i in range(len(counties)))
        params = {f"c{i}": c for i, c in enumerate(counties)}
        with get_db_engine().begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM health_records "
                    "WHERE timestamp >= :day_start AND timestamp < :day_end "
                    f"AND county IN ({placeholders})"
                ),
                {"day_start": day_start, "day_end": day_end, **params},
            )
    except Exception as e:
        print(f"Ingestion runner: same-day health dedup skipped: {e}")

    processor.process_and_store_health(payload_json)
    return len(records)


def run_all_ingestion() -> dict:
    """
    Runs the health, climate and space-weather ingestion pipelines and
    returns a summary of what was fetched and persisted per domain.
    """
    processor = AthGadDataProcessor()
    results = {}

    try:
        engine = HealthSurveillanceDataEngine()
        payload = engine.generate_weekly_surveillance_payload()
        count = _store_health_payload(processor, payload)
        results["health"] = {
            "status": "ok" if count else "empty",
            "source": engine.provider.source,
            "records": count,
        }
    except Exception as e:
        print(f"Ingestion runner: health error: {e}")
        results["health"] = {"status": "error", "message": str(e)}

    try:
        climate = ClimateIngestionService()
        raw = climate.fetch_daily_climate_metrics()
        count = len(raw.get("daily", {}).get("time", []))
        processor.process_and_store_climate(raw)
        results["climate"] = {"status": "ok" if count else "empty", "records": count}
    except Exception as e:
        print(f"Ingestion runner: climate error: {e}")
        results["climate"] = {"status": "error", "message": str(e)}

    try:
        space = SpaceWeatherIngestionService()
        kp = space.fetch_geomagnetic_indices()
        processor.process_and_store_space_weather(kp)
        results["space"] = {"status": "ok" if kp else "empty", "records": len(kp)}
    except Exception as e:
        print(f"Ingestion runner: space weather error: {e}")
        results["space"] = {"status": "error", "message": str(e)}

    return results
