"""Quick smoke tests for all routes - run while the dev server is up."""
import re
import requests

BASE = "http://127.0.0.1:5111"
s = requests.Session()

def check(name, method, path, **kwargs):
    url = BASE + path
    try:
        r = s.request(method, url, timeout=15, allow_redirects=False, **kwargs)
        loc = r.headers.get("Location", "")
        print(f"{name}: {method} {path} -> {r.status_code} | loc={loc}")
        if r.status_code >= 500:
            print(f"  ERROR BODY: {r.text[:800]}")
    except Exception as e:
        print(f"{name}: {method} {path} -> EXCEPTION: {e}")

# Public GET routes
check("landing", "GET", "/")
check("health", "GET", "/api/v1/health")
check("telemetry", "GET", "/telemetry")
check("dashboard", "GET", "/dashboard")
check("login-get", "GET", "/login")
check("register-get", "GET", "/register")
check("profile-get", "GET", "/profile")
check("subscribe-get", "GET", "/subscribe")
check("unsubscribe-get", "GET", "/unsubscribe")
check("admin", "GET", "/admin")
check("admin-analytics", "GET", "/admin/analytics")
check("admin-reports", "GET", "/admin/reports")
check("admin-users", "GET", "/admin/users")
check("admin-sms-delivery", "GET", "/admin/sms-delivery")
check("admin-risk-trend", "GET", "/api/v1/admin/risk-trend")
check("admin-telemetry-refresh", "GET", "/api/v1/admin/telemetry/refresh")
check("admin-analytics-summary", "GET", "/api/v1/admin/analytics/summary")
check("admin-sms-delivery-api", "GET", "/api/v1/admin/sms-delivery")
check("admin-dispatch-logs", "GET", "/api/v1/admin/dispatch-logs")
check("admin-report-snapshot", "GET", "/api/v1/admin/report-snapshot")
check("trial-expiry", "GET", "/api/v1/check-trial-expiry")
check("live-summary", "GET", "/api/v1/live-summary")
check("risk-status", "GET", "/api/v1/risk-status?county=Kitui")
check("alerts-history", "GET", "/api/v1/alerts/history")
check("telemetry-refresh", "GET", "/api/v1/telemetry/refresh")

# Test PDF report generation (admin - should redirect)
check("pdf-predicted", "GET", "/admin/reports/predicted_calamities/pdf")
check("pdf-disease", "GET", "/admin/reports/disease_outbreaks/pdf")
check("pdf-subscribed", "GET", "/admin/reports/subscribed_members/pdf")
check("pdf-unsubscribed", "GET", "/admin/reports/unsubscribed_members/pdf")
check("pdf-dispatch", "GET", "/admin/reports/alert_dispatch_logs/pdf")

# Test registration POST
r = s.get(BASE + "/register", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    check("register-post", "POST", "/register", data={
        "csrf_token": csrf.group(1),
        "full_name": "Test User",
        "email": "test@example.com",
        "phone_number": "0712345678",
        "password": "password123",
        "receive_email": "on",
        "receive_sms": "on",
    })
else:
    print("No CSRF token found in register page")

# Test login POST
r = s.get(BASE + "/login", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    check("login-post", "POST", "/login", data={
        "csrf_token": csrf.group(1),
        "email": "test@example.com",
        "password": "password123",
    })
else:
    print("No CSRF token found in login page")

# Test profile POST
r = s.get(BASE + "/profile", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    check("profile-post", "POST", "/profile", data={
        "csrf_token": csrf.group(1),
        "full_name": "Test User",
        "phone_number": "+254712345678",
        "county": "Kitui",
        "current_password": "",
        "new_password": "",
        "confirm_password": "",
    })
else:
    print("No CSRF token found in profile page")

# Test subscribe POST
r = s.get(BASE + "/subscribe", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    check("subscribe-post", "POST", "/subscribe", data={
        "csrf_token": csrf.group(1),
        "dispatch_medium": ["sms", "email"],
    })
else:
    print("No CSRF token found in subscribe page")

# Test unsubscribe POST
check("unsubscribe-post", "POST", "/unsubscribe")
