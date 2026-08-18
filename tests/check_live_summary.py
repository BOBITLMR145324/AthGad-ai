import requests, json
r = requests.get('http://127.0.0.1:5111/api/v1/live-summary', timeout=15)
data = r.json()
print('status:', r.status_code)
print('avg_climate:', data.get('avg_climate'))
print('avg_health:', data.get('avg_health'))
print('high_risk_count:', data.get('high_risk_count'))
print('hidden_pattern:', data.get('hidden_pattern'))
for c in data.get('counties', []):
    print('  %s: score=%s, level=%s, threat=%s' % (c['county'], c['score_pct'], c['risk_level'], c['threat_category']))
