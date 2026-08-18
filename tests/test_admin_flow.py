import requests, re, os, sys

BASE = "http://127.0.0.1:5111"
s = requests.Session()

# Login as admin (need to first register or find an existing admin)
# Try to register
r = s.get(BASE + "/register", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    r = s.post(BASE + "/register", data={
        "csrf_token": csrf.group(1),
        "full_name": "Admin Test",
        "email": "admin@test.com",
        "phone_number": "0712345678",
        "password": "password123",
        "receive_email": "on",
        "receive_sms": "on",
    }, timeout=15, allow_redirects=False)
    print("Register:", r.status_code, r.headers.get("Location", ""))

# Promote to admin using create_admin.py
os.system("python create_admin.py admin@test.com")

# Logout
s.get(BASE + "/logout", timeout=15)

# Login as admin
r = s.get(BASE + "/login", timeout=15)
csrf = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
if csrf:
    r = s.post(BASE + "/login", data={
        "csrf_token": csrf.group(1),
        "email": "admin@test.com",
        "password": "password123",
    }, timeout=15, allow_redirects=False)
    print("Login:", r.status_code, r.headers.get("Location", ""))

# Test admin endpoints
for url in [
    "/admin",
    "/admin/analytics",
    "/admin/reports",
    "/admin/users",
    "/admin/sms-delivery",
    "/api/v1/admin/risk-trend",
    "/api/v1/admin/telemetry/refresh",
    "/api/v1/admin/analytics/summary",
    "/api/v1/admin/sms-delivery",
    "/api/v1/admin/dispatch-logs",
    "/api/v1/admin/report-snapshot",
]:
    r = s.get(BASE + url, timeout=30)
    print(f"{url}: {r.status_code}")
    if r.status_code >= 400:
        print("  ERROR:", r.text[:500])
    elif r.headers.get("Content-Type", "").startswith("application/json"):
        try:
            d = r.json()
            if isinstance(d, dict):
                print("  Keys:", list(d.keys()))
        except:
            pass
