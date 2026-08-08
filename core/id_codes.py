"""
core/id_codes.py
================
Human-readable, domain-specific primary key generators for AthGad_db.

Every table that previously used `id SERIAL PRIMARY KEY` now keys on a
prefixed alphanumeric code tailored to its domain:

    climate_records     -> CLI-20260808-3F9KQ2          (CLI = climate)
    health_records      -> HLT-KIT-MAL-20260808-A3F9    (HLT + county + disease + date)
    space_weather_records -> SPW-20260808-183012-Q2K3   (SPW = space weather)
    users               -> USR-20260808-7KQ3F9          (USR = user)
    risk_alerts         -> RA-KIT-20260808183012-F9Q2   (RA + county + datetime)
    alert_dispatch_logs -> DS-20260808183012-F9Q2       (DS + datetime + random)

The random segment uses an alphabet that omits visually-ambiguous characters
(0/O, 1/I) so codes are safe to read aloud, transcribe by hand, and use in
URLs, SMS messages and PDF reports.
"""

import random
from datetime import datetime

# Unambiguous uppercase alphanumeric alphabet (no 0/O/1/I).
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_DEFAULT_RAND_LEN = 6


def _rand(length: int = _DEFAULT_RAND_LEN) -> str:
    """Returns a cryptographically-random alphanumeric segment."""
    rng = random.SystemRandom()
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


def _slug(value, length: int = 3) -> str:
    """
    Uppercase alphanumeric slug derived from a name, with spaces and
    punctuation removed. Unknown/empty values degrade to 'UNK' so a code is
    always produced (no broken primary keys).
    """
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum())
    if not cleaned:
        return "UNK"
    return cleaned[:length].upper()


def _acronym(value, max_words: int = 3) -> str:
    """
    First-letter acronym built from the leading significant words, e.g.
    'Rift Valley Fever' -> 'RVF', 'Diarrheal Diseases' -> 'DD'. Single-word
    values fall back to their first three letters, e.g. 'Malaria' -> 'MAL'.
    """
    words = [w for w in str(value or "").replace("&", " ").split() if w.strip()]
    if not words:
        return "UNK"
    if len(words) == 1:
        return "".join(ch for ch in words[0] if ch.isalnum())[:3].upper()
    return "".join(w[0].upper() for w in words[:max_words])


def new_climate_code(ts=None) -> str:
    """Code for a daily climate observation record."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"CLI-{dt:%Y%m%d}-{_rand()}"


def new_health_code(county=None, disease_type=None, ts=None) -> str:
    """Code for a county-level disease surveillance record."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"HLT-{_slug(county)}-{_acronym(disease_type)}-{dt:%Y%m%d}-{_rand(4)}"


def new_space_weather_code(ts=None) -> str:
    """Code for a geomagnetic Kp-index telemetry record."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"SPW-{dt:%Y%m%d-%H%M%S}-{_rand(4)}"


def new_user_code(ts=None) -> str:
    """Code for a registered user account."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"USR-{dt:%Y%m%d}-{_rand()}"


def new_alert_code(county=None, ts=None) -> str:
    """Code for a persisted AI risk-alert record."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"RA-{_slug(county)}-{dt:%Y%m%d%H%M%S}-{_rand(4)}"


def new_dispatch_code(ts=None) -> str:
    """Code for a tracked SMS/email alert dispatch record."""
    dt = ts if isinstance(ts, datetime) else datetime.now()
    return f"DS-{dt:%Y%m%d%H%M%S}-{_rand(4)}"
