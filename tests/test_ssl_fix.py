"""Test script to verify the urllib3 SSL context fix for Python 3.14."""
import ssl
import urllib3

# Create a custom SSL context (this works around the urllib3 2.7.0 bug on Python 3.14)
ctx = ssl.create_default_context()
http = urllib3.PoolManager(ssl_context=ctx)

try:
    r = http.request(
        'GET',
        'https://api.sandbox.africastalking.com/version1/user?username=sandbox',
        headers={'apiKey': 'test', 'Accept': 'application/json'},
        timeout=15
    )
    print(f"STATUS: {r.status}")
    print(f"BODY: {r.data[:200]}")
    print("SUCCESS: urllib3 with custom SSL context works!")
except Exception as e:
    print(f"FAILED: {e}")