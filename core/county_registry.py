# core/county_registry.py

HAZARD_BLUEPRINTS = {
    "Flooding & Landslides": {
        "vulnerability_drivers": "Low-lying basins or highly degraded slopes prone to saturation shifts and channel overflows.",
        "cascading_effects": [
            "Destruction of riverine agricultural fields, causing localized crop shortfalls.",
            "Spikes in vector-borne (Malaria) and waterborne diseases due to massive stagnant pooling.",
            "Contamination of community wells, sand dams, and boreholes leading to acute gastrointestinal outbreaks.",
            "Debris flows and flash floods cutting off vital transport and emergency medical routes."
        ],
        "proactive_solutions": [
            "Early Infrastructure Protection: Construct and reinforce sandbag dykes along vulnerable river sections.",
            "Vector Interventions: Pre-position and distribute long-lasting insecticidal nets (LLINs) and execute indoor residual spraying.",
            "Sanitation Guarding: Construct raised-pit latrines or seal boreholes in flood-prone zones to prevent structural sewage leaks.",
            "Slope Stabilisation: Enforce contour terracing and plant deep-rooted vetiver grass along steep, highly degraded terrains."
        ]
    },
    "Severe Drought": {
        "vulnerability_drivers": "Semi-arid climate parameters paired with extreme multi-season precipitation deficits.",
        "cascading_effects": [
            "Widespread rain-fed staple crop failures, driving acute food reliance alerts.",
            "Depletion of operational pasture lands, forcing mass livestock migration and potential regional conflicts.",
            "Severe acute malnutrition vectors scaling among vulnerable rural populations.",
            "Increased household water-scarcity stress, forcing long-distance walking vectors to unmonitored water points."
        ],
        "proactive_solutions": [
            "Climate-Smart Agriculture: Push real-time SMS advisories advising farmers to execute an absolute shift to drought-tolerant crop varieties.",
            "Strategic Destocking: Subsidize emergency animal feed and execute mass veterinary vaccinations before body conditions deteriorate.",
            "Water Resource Allocation: Activate remote sub-surface water tracking maps and pre-position water bowsers at critical drying junctions.",
            "Financial Safety Nets: Trigger index-based livestock insurance payouts or distribute localized emergency cash transfers."
        ]
    }
}

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
            sol = f"Early Infrastructure Protection: Construct and reinforce sandbag dykes along vulnerable bends of {river_asset}."
        localized_solutions.append(sol)

    return {
        "primary_calamity": calculated_calamity,
        "vulnerability_drivers": f"{county_name} Vector: {base_blueprint['vulnerability_drivers']}",
        "cascading_effects": list(base_blueprint["cascading_effects"]),
        "proactive_solutions": localized_solutions
    }