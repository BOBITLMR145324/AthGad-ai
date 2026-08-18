# core/county_registry.py

# Single source of truth for the Eastern Kenya counties covered by the early
# warning system. Import this everywhere instead of redefining the list.
COVERED_COUNTIES = [
    "Kitui", "Machakos", "Makueni", "Marsabit",
    "Isiolo", "Meru", "Embu", "Tharaka-Nithi",
]

HAZARD_BLUEPRINTS = {
    "Flooding & Landslides": {
        "vulnerability_drivers": "Areas near rivers or on steep, worn-down slopes that can flood or slide during heavy rain.",
        "cascading_effects": [
            "Farmland near rivers can be destroyed, causing food shortages.",
            "Malaria and waterborne diseases can spread quickly in standing water.",
            "Community wells and water sources can be contaminated, causing stomach illnesses.",
            "Mud and flash floods can block roads and cut off emergency help."
        ],
        "proactive_solutions": [
            "Build and reinforce sandbag dykes along riverbanks at risk of flooding.",
            "Give out insecticide-treated bed nets and spray homes to keep mosquitoes away.",
            "Build raised toilets and seal water wells in flood-prone areas to stop sewage leaking into them.",
            "Use terracing and plant deep-rooted grass on steep slopes to stop the ground from washing away."
        ]
    },
    "Severe Drought": {
        "vulnerability_drivers": "Dry regions that have gone several seasons without enough rain.",
        "cascading_effects": [
            "Crops that depend on rain can fail, leading to food shortages.",
            "Grazing land dries up, forcing livestock to move far and sometimes causing conflict.",
            "Children and families in rural areas are at higher risk of malnutrition.",
            "Households face water shortages and must walk long distances to unsafe water points."
        ],
        "proactive_solutions": [
            "Send farmers text-message advice on switching to crops that survive drought.",
            "Provide subsidized animal feed and vaccines before livestock become weak.",
            "Track underground water reserves and place water tankers at key drying points.",
            "Pay out livestock insurance and give emergency cash to affected families."
        ]
    }
}

# ---------------------------------------------------------------------------
# Centralized county risk-profile classification (single source of truth).
# Previously this logic was duplicated across app.py and admin_reports.py with
# DIFFERENT hardcoded county lists, causing inconsistent threat labels between
# the telemetry board, the dashboard and the admin reports.
# ---------------------------------------------------------------------------

# Counties whose dominant threat profile is health/waterborne (vector outbreaks).
_HEALTH_THREAT_COUNTIES = frozenset({"Marsabit", "Isiolo"})

# Counties whose dominant calamity profile is flooding/landslides.
_FLOOD_CALAMITY_COUNTIES = frozenset({"Makueni", "Machakos", "Isiolo"})


def threat_category_for(county: str, risk_level: str = "Low") -> tuple:
    """
    Returns (threat_category, primary_threat) for a county based on its live
    risk level. Mirrors the assignments used across the telemetry board so the
    admin analytics page stays consistent with the dashboard.

    - High risk counties and the health-prone counties (Marsabit, Isiolo) are
      classified as health threats (water contamination & vector outbreaks).
    - Everything else is a climate threat (rainfall deficit & soil moisture loss).
    """
    if risk_level == "High" or county in _HEALTH_THREAT_COUNTIES:
        return "health", "Water Contamination & Vector Outbreak"
    return "climate", "Rainfall Deficit & Soil Moisture Loss"


def calamity_for(county: str, risk_level: str = "Low") -> str:
    """
    Returns the primary calamity label for a county based on its live risk
    level. High-risk counties and the flood-prone counties (Makueni, Machakos,
    Isiolo) are classified as flash-flood/landslide/waterborne; everything else
    is classified as multi-season drought.
    """
    if risk_level == "High" or county in _FLOOD_CALAMITY_COUNTIES:
        return "Flash Floods, Severe Landslides & Waterborne Outbreaks"
    return "Severe Multi-Season Drought & Agricultural Deficits"


def proactive_actions_for_risk(county: str, risk_level: str = "Low") -> list:
    """
    Returns the proactive mitigation actions appropriate for a county's
    predicted calamity at the given risk level. Used by the forecast endpoints
    so every future prediction carries actionable advice.
    """
    calamity = calamity_for(county, risk_level)
    advisory = get_county_advisory(county, calamity)
    return advisory.get("proactive_solutions", [])


def get_county_advisory(county_name: str, calculated_calamity: str) -> dict:
    """
    Dynamically generates actionable blueprints and cascading risks based 
    on the specific county location combined with the live predicted calamity type.
    """
    # Normalize the matching key
    calamity_key = "Flooding & Landslides"
    if "Drought" in calculated_calamity or "Aridity" in calculated_calamity:
        calamity_key = "Severe Drought"

    # Deep-copy the blueprint layout safely
    base_blueprint = HAZARD_BLUEPRINTS.get(calamity_key)
    
    localized_solutions = []
    for sol in base_blueprint["proactive_solutions"]:
        # Perform dynamic context injection safely using clear structural hooks
        if "sandbag dykes" in sol:
            river_asset = "local seasonal river beds"
            if county_name == "Kitui" or county_name == "Isiolo":
                river_asset = "the Ewaso Ng'iro River channels"
            elif county_name == "Makueni":
                river_asset = "the Athi River sub-basin tracks"
            sol = f"Build and reinforce sandbag dykes along vulnerable bends of {river_asset}."
        localized_solutions.append(sol)

    return {
        "primary_calamity": calculated_calamity,
        "vulnerability_drivers": f"{county_name} County: {base_blueprint['vulnerability_drivers']}",
        "cascading_effects": list(base_blueprint["cascading_effects"]),
        "proactive_solutions": localized_solutions
    }