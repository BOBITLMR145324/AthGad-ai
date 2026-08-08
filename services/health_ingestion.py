import sys
import os
import json
import hashlib
import random
from datetime import datetime, timedelta

import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Forecastable disease catalog (environmental / climate-sensitive diseases)
# ---------------------------------------------------------------------------
# Beyond the original Malaria & Cholera, the following diseases are strongly
# correlated with climate/rainfall signals and can be forecast by the same
# multi-domain pipeline:
#   - Dengue Fever   : spread by Aedes mosquitoes, surges after rainfall/warmth
#   - Rift Valley Fever: mosquito-borne, outbreaks follow heavy rains & flooding
#   - Typhoid Fever  : waterborne, spikes after flooding contaminates water
#   - Diarrheal Diseases: waterborne, common after rain/flood events
#   - Meningitis (meningococcal): arid/semi-arid dry-season dust transmission
FORECASTABLE_DISEASES = [
    "Malaria",
    "Cholera",
    "Dengue Fever",
    "Rift Valley Fever",
    "Typhoid Fever",
    "Diarrheal Diseases",
    "Meningitis",
]

# Per-disease baseline case ranges (min, max) used for deterministic synthetic
# generation. Seeded by county + date so output is reproducible.
DISEASE_BASELINES = {
    "Malaria": (15, 60),
    "Cholera": (0, 4),
    "Dengue Fever": (2, 12),
    "Rift Valley Fever": (0, 6),
    "Typhoid Fever": (3, 18),
    "Diarrheal Diseases": (8, 40),
    "Meningitis": (1, 8),
}


class HealthDataProvider:
    """Base abstraction for health-surveillance data sources."""

    source = "base"

    def fetch_weekly_payload(self) -> list:
        """Returns a list of normalized health observation records."""
        raise NotImplementedError


class SyntheticHealthProvider(HealthDataProvider):
    """
    Deterministic synthetic provider (legacy fallback).
    Uses a stable seed derived from (county, disease, ISO week) so repeated
    runs produce identical payloads — no more misleading random spikes.
    """

    source = "synthetic"

    def __init__(self, target_counties=None, diseases=None):
        self.target_counties = target_counties or [
            "Machakos", "Kitui", "Makueni", "Marsabit",
            "Isiolo", "Meru", "Tharaka-Nithi", "Embu",
        ]
        self.diseases = diseases or FORECASTABLE_DISEASES

    def _seed_for(self, county: str, disease: str, week_key: str) -> int:
        """Derives a stable integer seed from county + disease + week."""
        raw = f"{county}|{disease}|{week_key}".encode("utf-8")
        return int(hashlib.sha256(raw).hexdigest(), 16) % (2 ** 32)

    def generate_weekly_surveillance_payload(self) -> str:
        """Generates a deterministic weekly surveillance snapshot (JSON string)."""
        payload = []
        current_date = datetime.now()
        week_key = current_date.strftime("%Y-%W")

        for county in self.target_counties:
            for disease in self.diseases:
                lo, hi = DISEASE_BASELINES.get(disease, (0, 10))
                rng = random.Random(self._seed_for(county, disease, week_key))
                base_cases = rng.randint(lo, hi)

                # Occasional structural spike (10% chance) to exercise anomaly
                # detection — also deterministic thanks to the seeded RNG.
                if rng.random() < 0.10:
                    base_cases *= rng.randint(4, 8)

                record = {
                    "timestamp": current_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "county": county,
                    "disease_type": disease,
                    "reported_cases": base_cases,
                    "facility_reporting_rate_pct": round(rng.uniform(85.0, 99.9), 1),
                    "source": self.source,
                }
                payload.append(record)

        return json.dumps(payload, indent=4)

    def fetch_weekly_payload(self) -> list:
        return json.loads(self.generate_weekly_surveillance_payload())


class MinistryHealthProvider(HealthDataProvider):
    """
    Real Ministry-of-Health feed adapter.
    Reads surveillance records from a REST endpoint (or a local CSV file),
    normalizing them into the same schema used by the storage pipeline.

    Environment variables:
      MOH_API_URL   - base URL of the Ministry-of-Health (or partner) API
      MOH_API_KEY   - optional bearer/API key
      MOH_ENDPOINT  - path to the weekly surveillance endpoint (default '/weekly')
      MOH_CSV_PATH  - optional local CSV file path (alternative to live API)
    """

    source = "ministry"

    def __init__(self):
        self.api_url = os.getenv("MOH_API_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("MOH_API_KEY", "").strip()
        self.endpoint = os.getenv("MOH_ENDPOINT", "/weekly").strip()
        self.csv_path = os.getenv("MOH_CSV_PATH", "").strip()

    def _normalize_row(self, raw: dict) -> dict:
        """Maps a ministry-provider row to the canonical ingestion schema."""
        ts = raw.get("timestamp") or raw.get("date") or raw.get("report_date")
        county = raw.get("county") or raw.get("county_name") or raw.get("sub_county")
        disease = raw.get("disease_type") or raw.get("disease") or raw.get("condition")
        cases = raw.get("reported_cases") or raw.get("cases") or raw.get("total_cases")
        rate = raw.get("facility_reporting_rate_pct") or raw.get("reporting_rate") or 0.0

        return {
            "timestamp": ts,
            "county": str(county).strip(),
            "disease_type": str(disease).strip(),
            "reported_cases": int(cases or 0),
            "facility_reporting_rate_pct": float(rate or 0.0),
            "source": self.source,
        }

    def _fetch_from_csv(self) -> list:
        import csv
        if not self.csv_path or not os.path.exists(self.csv_path):
            return []
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            return [self._normalize_row(row) for row in reader if row.get("county")]

    def _fetch_from_api(self) -> list:
        if not self.api_url:
            return []
        url = f"{self.api_url}{self.endpoint}"
        headers = {"User-Agent": "AthGadAI/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = httpx.get(url, headers=headers, timeout=20.0)
            if resp.status_code != 200:
                print(f"MinistryHealthProvider: API returned status {resp.status_code}")
                return []
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", data.get("records", []))
            return [self._normalize_row(r) for r in rows if r.get("county")]
        except Exception as e:
            print(f"MinistryHealthProvider: API fetch error: {e}")
            return []

    def fetch_weekly_payload(self) -> list:
        if self.csv_path:
            rows = self._fetch_from_csv()
        else:
            rows = self._fetch_from_api()
        if not rows:
            print(
                "MinistryHealthProvider: No records available. Falling back to "
                "deterministic synthetic data for graceful degradation."
            )
            return SyntheticHealthProvider().fetch_weekly_payload()
        return rows


def get_health_provider() -> HealthDataProvider:
    """
    Factory that selects the active health provider based on the
    HEALTH_DATA_SOURCE env var ('synthetic' | 'ministry'). Defaults to
    'synthetic' to preserve existing behaviour.
    """
    mode = os.getenv("HEALTH_DATA_SOURCE", "synthetic").strip().lower()
    if mode == "ministry":
        return MinistryHealthProvider()
    return SyntheticHealthProvider()


class HealthSurveillanceDataEngine:
    """Backwards-compatible facade that delegates to the active provider."""

    def __init__(self, provider: HealthDataProvider = None):
        self.provider = provider or get_health_provider()
        self.target_counties = getattr(self.provider, "target_counties", None) or [
            "Machakos", "Kitui", "Makueni", "Marsabit",
            "Isiolo", "Meru", "Tharaka-Nithi", "Embu",
        ]
        self.diseases = getattr(self.provider, "diseases", None) or FORECASTABLE_DISEASES

    def generate_weekly_surveillance_payload(self) -> str:
        """Returns the weekly payload as a JSON string (legacy signature)."""
        return json.dumps(self.provider.fetch_weekly_payload(), indent=4)


if __name__ == "__main__":
    engine = HealthSurveillanceDataEngine()
    mock_payload = engine.generate_weekly_surveillance_payload()
    print(
        f"Generated Diagnostic Ingestion Output (source={engine.provider.source}):\n",
        mock_payload,
    )

    # Persist the generated surveillance records so the analytics engine has
    # real health data per county instead of only baseline fallbacks.
    try:
        from core.processor import AthGadDataProcessor
        processor = AthGadDataProcessor()
        processor.process_and_store_health(mock_payload)
        print("Health Ingestion Pipeline: data persisted to health_records.")
    except Exception as e:
        print(f"Health Ingestion Pipeline: could not persist records: {e}")
