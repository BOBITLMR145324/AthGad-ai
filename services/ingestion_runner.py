import os
import sys
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

from core.processor import AthGadDataProcessor
from core.db_helper import get_db_engine
from services.health_ingestion import HealthSurveillanceDataEngine
from services.climate_ingestion import ClimateIngestionService
from services.space_weather_ingestion import SpaceWeatherIngestionService


def _store_health_payload(processor: AthGadDataProcessor, payload_json: str) -> int:
    """
    Persists a health surveillance payload after removing any rows for the
    affected counties inside the payload's time span, so repeated refreshes do
    not double-count reported cases in the daily aggregates.
    """
    try:
        records = json.loads(payload_json)
    except json.JSONDecodeError:
        records = []

    if not records:
        return 0

    counties = {r.get("county") for r in records if r.get("county")}

    # Determine the full time span covered by the payload (the synthetic
    # provider now generates a 30-day window).
    timestamps = []
    for r in records:
        try:
            timestamps.append(datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, TypeError, ValueError):
            continue
    if timestamps:
        span_start = min(timestamps).replace(hour=0, minute=0, second=0, microsecond=0)
        span_end = (max(timestamps) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        span_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        span_end = span_start + timedelta(days=1)

    try:
        placeholders = ",".join(f":c{i}" for i in range(len(counties)))
        params = {f"c{i}": c for i, c in enumerate(counties)}
        with get_db_engine().begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM health_records "
                    "WHERE timestamp >= :span_start AND timestamp < :span_end "
                    f"AND county IN ({placeholders})"
                ),
                {"span_start": span_start, "span_end": span_end, **params},
            )
    except Exception as e:
        logger.warning("Ingestion runner: health dedup skipped: %s", e)

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
        logger.error("Ingestion runner: health error: %s", e)
        results["health"] = {"status": "error", "message": str(e)}

    try:
        climate = ClimateIngestionService()
        raw = climate.fetch_daily_climate_metrics()
        count = len(raw.get("daily", {}).get("time", []))
        processor.process_and_store_climate(raw)
        results["climate"] = {"status": "ok" if count else "empty", "records": count}
    except Exception as e:
        logger.error("Ingestion runner: climate error: %s", e)
        results["climate"] = {"status": "error", "message": str(e)}

    try:
        space = SpaceWeatherIngestionService()
        kp = space.fetch_geomagnetic_indices()
        processor.process_and_store_space_weather(kp)
        results["space"] = {"status": "ok" if kp else "empty", "records": len(kp)}
    except Exception as e:
        logger.error("Ingestion runner: space weather error: %s", e)
        results["space"] = {"status": "error", "message": str(e)}

    return results
