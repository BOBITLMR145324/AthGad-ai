import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "athgad.log")
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

# Third-party loggers that are chatty at DEBUG/INFO and add noise.
_LIBRARY_LOG_LEVELS = {
    "urllib3": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "sqlalchemy": logging.WARNING,
    "flask_limiter": logging.WARNING,
    "werkzeug": logging.INFO,
}

_configured = False


def configure_logging():
    """
    Configures the root logger once with a console handler and a rotating
    file handler (logs/athgad.log). Idempotent so it is safe to call from
    gunicorn preload, tests, and the WSGI module alike.
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("Could not attach persistent log file %s: %s", _LOG_FILE, exc)

    for name, level in _LIBRARY_LOG_LEVELS.items():
        logging.getLogger(name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
