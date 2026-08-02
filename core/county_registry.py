# core/county_registry.py

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