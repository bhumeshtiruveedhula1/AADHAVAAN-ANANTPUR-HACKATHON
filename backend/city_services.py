"""Deterministic read-only router and frontend-compatible response composition."""
import re

from city_http import CityServiceError
from maps_service import directions_url, travel_mode
from places_service import CATEGORIES, PlacesService
from weather_service import WeatherService

_WEATHER = WeatherService()
_PLACES = PlacesService()
TYPES = {"accommodation": "stay", "weather_conditions": "weather", "civic_report": "civic", "civic_lookup": "civic"}
LABELS = {"accommodation": "Accommodation", "food_places": "Food places", "places_services": "City places", "tourism": "Tourist attractions"}
ERRORS = {
    "not_configured": "Google Places is not configured. The team must supply a server-side Google Places API key.",
    "timeout": "The city provider timed out. Please try again.",
    "rate_limited": "The city provider's quota or rate limit was reached. Please try again later.",
    "authentication_error": "The city provider rejected the server credentials or API permissions.",
    "connection_error": "The server could not reach the city provider.",
    "busy": "City lookups are busy. Please try again shortly.",
    "invalid_response": "The city provider returned data that could not be validated.",
    "missing_data": "The provider has no complete hourly forecast for the requested interval.",
    "unsupported_time": "Please specify today, tomorrow, this evening, or a date in YYYY-MM-DD format. That time phrase cannot yet be resolved reliably.",
    "unsupported_range": "This service supports forecasts for today through the next six days only.",
    "missing_location": "Please supply an area or landmark for nearby results. Your GPS location is unknown.",
}


def base_result(intent):
    return {"type": TYPES.get(intent["primary_intent"], "guide"), "title": "Request understood",
            "description": "", "details": [], "places": [], "source": None, "sources": [],
            "status": "unavailable", "city": intent["city"], "intent": intent["primary_intent"],
            "understanding": intent, "interpretation_status": intent["processing_status"],
            "actions": [], "sections": []}


def constraint_details(intent):
    values = []
    if intent.get("budget"):
        values.append("Requested budget: " + intent["budget"]["raw_text"] + " (not verified)")
    if intent.get("time_constraint"):
        values.append("Requested time: " + intent["time_constraint"])
    for preference in intent.get("preferences", []) + intent.get("accessibility_constraints", []):
        values.append("Requested: " + preference + " (not verified)")
    return values


def compose(intent, weather=None, places=None, previous=None, owner=None):
    weather, places = weather or _WEATHER, places or _PLACES
    result = base_result(intent)
    # Do not let selected Bengaluru context mask an explicitly extracted other city.
    cities = [intent.get("city"), *(e["value"] for e in intent.get("entities", []) if e["type"] == "city")]
    if any(city and city.casefold() not in {"bengaluru", "bangalore"} for city in cities):
        result.update(status="unsupported_city", title="City not supported", description="Real city services currently support Bengaluru only. No Bengaluru data has been returned for another city.")
        return result
    primary = intent["primary_intent"]
    if primary in {'civic_report', 'civic_lookup'} and intent.get('action') != 'with_you':
        from civic_chat import compose_civic
        return compose_civic(intent, places, owner=owner)
    # Origin is optional for Maps handoff, but remains unknown in understanding.
    missing = [item for item in intent["missing_information"] if not (primary == "transport" and item == "origin" and not intent["action"])]
    if missing:
        names = {"city": "your city", "area": "the area or landmark", "origin": "your starting point", "destination": "your destination",
                 "exact_location": "the exact street, address or landmark", "report_id": "the report reference", "request_goal": "what you need help with"}
        result.update(type="guide", status="clarification_required", title="A little more information is needed",
                      description="Please tell me " + ", ".join(names[name] for name in missing) + ". No lookup or action has been performed.")
        return result
    if intent["action"]:
        result.update(type="guide", status="action_not_implemented", title="Action recognised",
                      description=f"I recognised the {intent['action'].replace('_', ' ')} action. Its execution is not implemented yet; no change has been made.")
        return result
    # At most primary plus one supported secondary. Sequential, bounded calls;
    # preserve order and independently handle failures, never another LLM call.
    categories = [primary]
    supported = {*CATEGORIES, "weather_conditions", "transport"}
    secondaries = intent["secondary_intents"]
    secondary = next((category for category in secondaries if category in supported and not (primary == 'tourism' and category == 'food_places')), None)
    if secondary:
        categories.append(secondary)
    result["deferred_intents"] = [category for category in secondaries if category != secondary and not (primary == 'tourism' and category == 'food_places')]
    for category in categories:
        try:
            if category == "weather_conditions":
                section = weather_section(weather.forecast(intent.get("time_constraint")), intent.get("time_constraint"))
            elif category == "tourism":
                from tourism_service import plan_itinerary
                section = plan_itinerary(intent, places, weather, previous)
            elif category in CATEGORIES:
                area = intent.get("area") or intent.get("landmark") or intent.get("exact_location")
                if re.search(r"\b(?:near me|nearby|here)\b", intent["original_text"], re.I) and (not area or re.search(r"\b(?:near me|here|my house|my college)\b", area, re.I)):
                    raise CityServiceError("missing_location")
                section = places_section(places.search(intent, category, 3 if len(categories) > 1 else 5), category)
            elif category == "transport":
                section = transport_section(intent, places, previous)
            else:
                section = {"intent": category, "status": "not_implemented", "description": "This city service is not connected yet. No civic action or lookup has been performed.", "places": [], "sources": []}
        except CityServiceError as error:
            section = {"intent": category, "status": error.code, "description": ERRORS.get(error.code, "The city provider could not complete this lookup."), "places": [], "sources": []}
        result["sections"].append(section)
    sections = result["sections"]
    successes = [section for section in sections if section["status"] in {"ok", "handoff_available"}]
    all_good = len(successes) == len(sections) and not result["deferred_intents"]
    status = ("ok" if all_good else "partial") if successes else sections[0]["status"]
    result.update(status=status, title={"weather_conditions": "Bengaluru weather forecast", "transport": "Google Maps directions handoff"}.get(primary, LABELS.get(primary, "Request understood")),
                  description="\n\n".join(section["description"] for section in sections),
                  details=constraint_details(intent), places=[place for section in sections for place in section["places"]],
                  sources=[source for section in sections for source in section["sources"]])
    if result["deferred_intents"]:
        result["description"] += "\n\nAdditional needs were retained but not looked up in this response: " + ", ".join(result["deferred_intents"]) + "."
    result["source"] = result["sources"][0] if result["sources"] else None
    result["summary"] = " ".join(section.get("summary", section["description"]) for section in sections)
    result["map_markers"] = [marker for section in sections for marker in section.get("map_markers", [])][:6]
    for section in sections:
        result["details"].extend(section.get("details", []))
    result["channel_text"] = result["title"] + "\n" + result["description"] + "\n" + "\n".join(result["details"])
    if result["places"] and primary != 'tourism':
        area = intent.get("area") or intent.get("landmark")
        subject = "hotel and food options" if primary == "accommodation" and "food_places" in categories else "accommodation options" if primary == "accommodation" else "food options" if primary == "food_places" else "places"
        result["summary"] = "Here are " + subject + (f" around {area}" if area else " in Bengaluru") + ". "
        if primary == "accommodation":
            budget = f"Your requested budget ({intent['budget']['raw_text']})" if intent.get("budget") else "Room prices"
            result["summary"] += budget + " and arrival/check-in availability still need confirmation with the property."
        elif primary == "food_places" and intent.get("preferences"):
            result["summary"] += "Your food preferences were included in the search; menu availability is unverified."
        else:
            result["summary"] += "Check your preferences with the place before travelling."
        result["display_requirements"] = {"google_maps_attribution": True, "android_display_ready": True}
    return result


def weather_section(data, requested_time=None):
    interval = data["interval"]
    source = data["source"]
    description = (f"Bengaluru city-level forecast for {interval['start']} to {interval['end_exclusive']} (end exclusive, Asia/Kolkata): "
                   f"temperature {data['temperature_min']:g}–{data['temperature_max']:g} {data['units']['temperature_2m']}; "
                   f"highest hourly precipitation probability {data['precipitation_probability_max']:g}{data['units']['precipitation_probability']}; "
                   f"total forecast precipitation {data['precipitation_total']:g} {data['units']['precipitation']}. "
                   "Rain is not certain. This is a forecast, not an official weather warning or a street-level reading. "
                   f"{source['attribution']}. {source['url']} Retrieved {source['retrieved_at']}.")
    chance = data["precipitation_probability_max"]
    rain = data["precipitation_total"]
    summary = "For " + (requested_time or "today") + ", " + ("rain looks likely. Carry an umbrella." if chance >= 60 else
               "the forecast looks mostly dry." if chance <= 20 and rain == 0 else
               "there is a chance of rain. Keep an umbrella handy.")
    return {"intent": "weather_conditions", "status": "ok", "summary": summary, "description": description, "places": [], "sources": [source],
            "details": ["Asia/Kolkata", "City-level forecast", "Cached forecast" if data["cache_hit"] else "Fresh retrieval"], "weather": data}


def places_section(data, category):
    records = data["places"]
    lines = [LABELS[category] + " — Google Maps"]
    for place in records:
        lines.append(f"{place['name']} — {place['address']}")
        for credit in place["attributions"]:
            lines.append("Additional attribution: " + credit["provider"] + (" " + credit["provider_uri"] if credit["provider_uri"] else ""))
    if not records:
        lines.append("No matching places were returned by the provider.")
    if category == "accommodation":
        lines.append("Place discovery only: room availability, exact rates, budget match and immediate check-in have not been confirmed. No reservation has been made.")
    elif category == "tourism":
        lines.append("Place discovery only, not an optimised itinerary. Opening hours and access have not been confirmed.")
    else:
        lines.append("Operating status, accessibility and availability of services, including emergency capacity, have not been confirmed.")
    limitation = lines[-1]
    lines.append("Source: Google Maps. Retrieved " + data["source"]["retrieved_at"] + ".")
    return {"intent": category, "status": "ok" if records else "no_results", "description": "\n".join(lines), "places": records,
            "summary": f"{len(records)} {LABELS[category].lower()} listings returned." if records else "No matching places were returned.",
            "limitations": [limitation], "sources": [data["source"]], "rejected_records": data["rejected_records"]}


def transport_section(intent, places=None, previous=None):
    destination = intent.get("destination")
    if not destination:
        raise CityServiceError("missing_location")
    origin = intent.get("origin")
    if origin and re.fullmatch(r"(?:here|there|near me|my (?:current )?location|current location)", origin.strip(), re.I):
        origin = None
    mode = travel_mode(intent.get("transport_preference")) or "transit"
    url = directions_url(destination + ", Bengaluru", origin + ", Bengaluru" if origin else None, mode)
    if not url:
        raise CityServiceError("invalid_response")
    description = "Google Maps can plan this journey; Smart Nagrik has not calculated a route, fare, ETA or live arrival. "
    if not origin:
        description += "Your origin is unknown. Supply your starting point, or allow Google Maps to request it. "
    description += "Google Maps directions: " + url
    references = [{"name": "Google Maps", "url": url, "kind": "directions_handoff"}]
    if "metro" in (intent.get("transport_preference") or "").casefold():
        description += " Official Bengaluru metro information: https://english.bmrc.co.in/ (BMRCL; reference only, no live schedule retrieved)."
        references.append({"name": "BMRCL", "url": "https://english.bmrc.co.in/", "kind": "official_reference"})
    resolved, markers = {}, []
    old_sections = previous.get("sections", []) if previous else []
    old_resolved = next((section.get("resolved_locations", {}) for section in old_sections if section["intent"] == "transport"), {})
    for role, text in (("origin", origin), ("destination", destination)):
        if not text:
            continue
        old = old_resolved.get(role)
        try:
            resolution = old if old and old.get("status") == "resolved" and old.get("original_text") == text else places.resolve(text) if places and hasattr(places, "resolve") else {"status": "not_configured", "place": None, "original_text": text}
        except CityServiceError as error:
            resolution = {"status": error.code, "original_text": text, "place": None}
        resolved[role] = resolution
        record = resolution.get("place")
        if record:
            markers.append({**record, "id": role + ":" + record["id"], "place_id": record["id"], "marker_type": role,
                            "category": "transport", "name": role.title() + ": " + record["name"], "original_text": text})
    summary = (f"Explore walking directions from {origin} to {destination}." if mode == "walking" and origin else
               f"Explore public transport from {origin} to {destination}." if mode == "transit" and origin else
               f"Explore directions to {destination}.")
    if not origin:
        summary += " What is your starting point?"
    unresolved = [role for role, value in resolved.items() if value["status"] in {"ambiguous", "no_results", "missing_location"}]
    if unresolved:
        summary += " Please give a more specific " + " and ".join(unresolved) + "."
    if any(value["status"] not in {"resolved", "ambiguous", "no_results", "missing_location"} for value in resolved.values()):
        summary += " Location lookup is unavailable; Directions can still open in Google Maps."
    summary += " The map shows confirmed locations only; Google Maps calculates the route."
    from transport_demo import demo_connection
    demo = demo_connection(origin, destination, intent.get("transport_preference"))
    if demo:
        description += " " + demo["notice"]
        references.append({"name": demo["static_label"], "url": demo["source"], "kind": "static_reference"})
    destination_place = resolved.get("destination", {}).get("place")
    origin_place = resolved.get("origin", {}).get("place")
    if destination_place:
        resolved_url = directions_url(destination_place["name"] + ", Bengaluru", origin_place["name"] + ", Bengaluru" if origin_place else origin + ", Bengaluru" if origin else None,
                             mode, destination_place["id"], origin_place["id"] if origin_place else None)
        description = description.replace(url, resolved_url)
        url = resolved_url
        references[0]["url"] = url
    for marker in markers:
        if marker["marker_type"] == "destination":
            marker["directions_url"] = url
    return {"intent": "transport", "status": "handoff_available", "summary": summary, "description": description, "places": [], "sources": references,
            "directions_url": url, "origin": origin, "destination": destination, "travel_mode": mode,
            "resolved_locations": resolved, "map_markers": markers, "route_geometry": None,
            "geometry_status": "endpoints_only", "demo": demo}
