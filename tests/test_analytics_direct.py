"""Test the analytics engine directly."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.analytics import AthGadAnalyticsEngine

try:
    engine = AthGadAnalyticsEngine()
    print("Engine created OK")
    
    for county in ["Kitui", "Machakos", "Makueni"]:
        try:
            result = engine.calculate_composite_risk(county)
            print(f"{county}: score={result.get('composite_risk_score')}, level={result.get('risk_level')}")
            forecast = result.get("forecast", {})
            print(f"  forecast scores: {forecast.get('scores')[:3]}..., trend={forecast.get('trend')}")
        except Exception as e:
            print(f"{county}: ERROR - {type(e).__name__}: {e}")
    
    # Test forecast
    try:
        fc = engine.forecast_risk(county="Kitui", horizon=7)
        print(f"forecast_risk: {len(fc.get('scores', []))} scores, trend={fc.get('trend')}")
    except Exception as e:
        print(f"forecast_risk ERROR - {type(e).__name__}: {e}")
        
except Exception as e:
    print(f"FATAL: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
