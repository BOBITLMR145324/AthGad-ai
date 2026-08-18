import hashlib

import pytest
from flask import Flask


def _make_response():
    app = Flask(__name__)
    with app.test_request_context("/"):
        from flask import Response
        return Response("ok")


@pytest.fixture
def app():
    return Flask(__name__)


def test_apply_security_headers_no_hsts_when_not_enforced(monkeypatch, app):
    monkeypatch.setenv("ENFORCE_HTTPS", "false")
    from core.security import apply_security_headers

    with app.test_request_context("/"):
        from flask import Response
        response = apply_security_headers(Response("ok"))

    assert "Strict-Transport-Security" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "geolocation=()" in response.headers["Permissions-Policy"]


def test_apply_security_headers_hsts_when_enforced(monkeypatch, app):
    monkeypatch.setenv("ENFORCE_HTTPS", "true")
    from core.security import apply_security_headers

    with app.test_request_context("/"):
        from flask import Response
        response = apply_security_headers(Response("ok"))

    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
    assert "includeSubDomains" in response.headers["Strict-Transport-Security"]


def test_get_secret_key_from_env(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "my-secret")
    from core.security import get_secret_key
    assert get_secret_key() == "my-secret"


def test_get_secret_key_override_wins(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "my-secret")
    from core.security import get_secret_key
    assert get_secret_key(override="override") == "override"


def test_client_identifier_hashes_ip(app):
    from core.security import client_identifier

    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "192.168.1.50"}):
        identifier = client_identifier()

    expected = "ip:" + hashlib.sha256(b"192.168.1.50").hexdigest()[:16]
    assert identifier == expected


def test_client_identifier_deterministic(app):
    from core.security import client_identifier

    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.1"}):
        assert client_identifier() == client_identifier()
