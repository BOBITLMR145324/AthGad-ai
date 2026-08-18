from datetime import datetime, timedelta, timezone

import pytest

from services import mpesa_service


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def reset_cache():
    mpesa_service._token_cache["token"] = None
    mpesa_service._token_cache["expires_at"] = None
    yield
    mpesa_service._token_cache["token"] = None
    mpesa_service._token_cache["expires_at"] = None


@pytest.fixture(autouse=True)
def set_credentials(monkeypatch):
    monkeypatch.setattr(mpesa_service, "CONSUMER_KEY", "test-key")
    monkeypatch.setattr(mpesa_service, "CONSUMER_SECRET", "test-secret")


def test_missing_credentials_raise():
    mpesa_service.CONSUMER_KEY = None
    mpesa_service.CONSUMER_SECRET = None
    with pytest.raises(Exception, match="not configured"):
        mpesa_service.get_mpesa_access_token()


def test_token_cached_across_calls(monkeypatch):
    calls = []

    def fake_get(url, auth=None, timeout=None):
        calls.append(url)
        return FakeResponse({"access_token": "tok-abc", "expires_in": 3600})

    monkeypatch.setattr("services.mpesa_service.requests.get", fake_get)

    t1 = mpesa_service.get_mpesa_access_token()
    t2 = mpesa_service.get_mpesa_access_token()
    t3 = mpesa_service.get_mpesa_access_token()

    assert t1 == t2 == t3 == "tok-abc"
    assert len(calls) == 1


def test_token_refreshes_after_expiry(monkeypatch):
    calls = []

    def fake_get(url, auth=None, timeout=None):
        calls.append(url)
        return FakeResponse({"access_token": f"tok-{len(calls)}", "expires_in": 3600})

    monkeypatch.setattr("services.mpesa_service.requests.get", fake_get)

    first = mpesa_service.get_mpesa_access_token()

    now = datetime.now(timezone.utc)
    mpesa_service._token_cache["expires_at"] = now - timedelta(seconds=1)

    second = mpesa_service.get_mpesa_access_token()

    assert first != second
    assert len(calls) == 2


def test_token_refreshes_near_expiry_within_margin(monkeypatch):
    calls = []

    def fake_get(url, auth=None, timeout=None):
        calls.append(url)
        return FakeResponse({"access_token": "tok-margin", "expires_in": 3600})

    monkeypatch.setattr("services.mpesa_service.requests.get", fake_get)

    mpesa_service.get_mpesa_access_token()

    now = datetime.now(timezone.utc)
    mpesa_service._token_cache["expires_at"] = now + timedelta(seconds=100)

    mpesa_service.get_mpesa_access_token()

    assert len(calls) == 2


def test_missing_access_token_raises(monkeypatch):
    def fake_get(url, auth=None, timeout=None):
        return FakeResponse({"expires_in": 3600})

    monkeypatch.setattr("services.mpesa_service.requests.get", fake_get)
    with pytest.raises(Exception, match="access_token"):
        mpesa_service.get_mpesa_access_token()


def test_failed_oauth_raises(monkeypatch):
    def fake_get(url, auth=None, timeout=None):
        return FakeResponse({"error": "boom"}, status_code=401)

    monkeypatch.setattr("services.mpesa_service.requests.get", fake_get)
    with pytest.raises(Exception, match="Status: 401"):
        mpesa_service.get_mpesa_access_token()
