import pytest

import app as app_module


@pytest.fixture(scope="module")
def flask_app():
    return app_module.app


@pytest.fixture(scope="module")
def client(flask_app):
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


ROUTE_CONTRACT = frozenset({
    ("/", ("GET",)),
    ("/admin", ("GET",)),
    ("/admin/analytics", ("GET",)),
    ("/admin/reports", ("GET",)),
    ("/admin/reports/<report_type>/pdf", ("GET",)),
    ("/admin/sms-delivery", ("GET",)),
    ("/admin/users", ("GET",)),
    ("/admin/users/<string:user_code>/demote", ("POST",)),
    ("/admin/users/<string:user_code>/promote", ("POST",)),
    ("/api/v1/admin/analytics/summary", ("GET",)),
    ("/api/v1/admin/dispatch-logs", ("GET",)),
    ("/api/v1/admin/report-snapshot", ("GET",)),
    ("/api/v1/admin/risk-trend", ("GET",)),
    ("/api/v1/admin/sms-delivery", ("GET",)),
    ("/api/v1/admin/telemetry/refresh", ("GET",)),
    ("/api/v1/alerts/history", ("GET",)),
    ("/api/v1/check-trial-expiry", ("GET",)),
    ("/api/v1/health", ("GET",)),
    ("/api/v1/ingest/all", ("POST",)),
    ("/api/v1/live-summary", ("GET",)),
    ("/api/v1/mpesa/callback", ("POST",)),
    ("/api/v1/risk-status", ("GET",)),
    ("/api/v1/sms/callback", ("POST",)),
    ("/api/v1/sms/preferences", ("GET", "POST")),
    ("/api/v1/telemetry/refresh", ("GET",)),
    ("/dashboard", ("GET",)),
    ("/favicon.ico", ("GET",)),
    ("/login", ("GET",)),
    ("/login", ("POST",)),
    ("/logout", ("GET",)),
    ("/profile", ("GET", "POST")),
    ("/profile/delete", ("POST",)),
    ("/register", ("GET",)),
    ("/register", ("POST",)),
    ("/subscribe", ("GET", "POST")),
    ("/telemetry", ("GET",)),
    ("/unsubscribe", ("GET", "POST")),
    ("/unsubscribe/reason", ("GET", "POST")),
})


def test_route_contract_unchanged(flask_app):
    actual = frozenset({
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in flask_app.url_map.iter_rules()
        if rule.rule != "/static/<path:filename>"
    })
    assert actual == ROUTE_CONTRACT


def test_no_logout_endpoint_name(flask_app):
    rules = [r.rule for r in flask_app.url_map.iter_rules()]
    assert "/logout" in rules


def test_logout_endpoint_actually_named_handle_logout(flask_app):
    endpoint = flask_app.url_map._rules_by_endpoint.get("handle_logout")
    assert endpoint is not None
    assert any(r.rule == "/logout" for r in endpoint)


def test_landing_page(client):
    response = client.get("/")
    assert response.status_code == 200


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_register_page(client):
    response = client.get("/register")
    assert response.status_code == 200


def test_dashboard_redirects_when_anonymous(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_admin_redirects_when_anonymous(client):
    response = client.get("/admin")
    assert response.status_code == 302


def test_security_headers_present(client):
    response = client.get("/")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in response.headers.get("Content-Security-Policy", "")


def test_static_assets_served(client):
    for asset in (
        "/static/css/style.css",
        "/static/css/admin.css",
        "/static/js/dashboard.js",
        "/static/js/landing.js",
        "/static/js/flash-alerts.js",
        "/static/js/profile.js",
        "/static/js/telemetry.js",
        "/static/js/chart.umd.min.js",
        "/static/js/password-toggle.js",
        "/static/js/admin-dashboard.js",
        "/static/js/admin-sms-console.js",
    ):
        response = client.get(asset)
        assert response.status_code == 200
