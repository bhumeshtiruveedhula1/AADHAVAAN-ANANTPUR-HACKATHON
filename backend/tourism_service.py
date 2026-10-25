"""Bounded source-grounded sightseeing plans, with explicit planning estimates.

No routes, ticket prices, opening hours, bookings or menu facts are calculated.
Previous results may be reused within the existing short-lived conversation only.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from secrets import token_urlsafe

from city_http import CityServiceError
from maps_service import directions_url, travel_mode
from weather_service import time_window


INTERESTS = {
    "nature": r"\b(?:nature|park|parks|garden|gardens|greenery|outdoor)\b",
    "heritage": r"\b(?:heritage|history|historic|historical|palace|palaces|museum|museums)\b",
    "temples": r"\b(?:temple|temples|religious)\b",
    "shopping": r"\b(?:shopping|market|markets)\b",
}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
LIMITATIONS = [
    "Visit durations and the time buffer are planning estimates, not measured travel times.",
    "Stop order uses straight-line geography where coordinates are available, not road-route or traffic optimisation.",
    "Opening hours, ticket prices, budget match, menu availability and accessibility have not been confirmed. No reservation has been made.",
]


def _duration(text):
    match = re.search(r"\b(\d+(?:\.\d+)?|" + "|".join(NUMBERS) + r")[ -]*(?:hours?|hrs?)\b", text, re.I)
    if match:
        value = NUMBERS.get(match[1].lower())
        return max(1, min(12, float(value if value is not None else match[1])))
    if re.search(r"\b(?:half[ -]day|afternoon|morning)\b", text, re.I):
        return 4
    if re.search(r"\b(?:full[ -]day|one[ -]day|1[ -]day|for the day|a day|day trip)\b", text, re.I):
        return 8
    return None


def _previous_section(previous):
    if previous and previous.get("intent") == "tourism" and previous.get("itinerary"):
        return previous
    return next((section for section in (previous or {}).get("sections", []) if section.get("intent") == "tourism" and section.get("itinerary")), None)


def constraints_for(intent, previous=None):
    old = deepcopy((previous or {}).get("itinerary", {}).get("constraints", {}))
    text = intent.get("original_text", "")
    lower = text.casefold()
    explicit_duration = _duration(text)
    duration = explicit_duration or (_duration(intent.get("duration") or "") if not old else None)
    if duration is None and re.search(r"\b(?:shorter|shorten)\b", lower):
        duration = max(1, old.get("duration_hours", 6) / 2)
    hours = duration or old.get("duration_hours", 6)
    interests = [name for name, pattern in INTERESTS.items() if re.search(pattern, lower)]
    # A request to remove a museum is not a request for more heritage museums.
    if re.match(r"\s*(?:remove|skip|replace)\b", lower):
        interests = []
    preferences = list(dict.fromkeys([*old.get("preferences", []), *intent.get("preferences", [])]))[:8]
    food_preferences = list(dict.fromkeys([*old.get("food_preferences", []),
        *(term for term in ("vegetarian", "vegan", "paneer", "halal") if re.search(r"\b" + term + r"\b", lower + " " + " ".join(preferences), re.I))]))
    include_food = old.get("include_food", False) or "food_places" in intent.get("secondary_intents", []) or bool(re.search(r"\b(?:food|lunch|dinner|breakfast|eat|restaurant|vegetarian|vegan)\b", lower))
    if re.search(r"\b(?:no|without|remove|skip) (?:a |the )?food\b", lower):
        include_food = False
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|tomorrow|today)(?:\s+(morning|afternoon|evening))?\b", lower)
    date_time = date_match.group() if date_match else old.get("date_time", "today")
    if not date_match and re.search(r"\b(?:this |rainy )?(morning|afternoon|evening)\b", lower):
        date_time = "today " + re.search(r"\b(morning|afternoon|evening)\b", lower)[1]
    people = re.search(r"\b(\d{1,2}|one|two|three|four|five|six)\s+(?:people|persons|adults|of us)\b", lower)
    group_size = old.get("group_size")
    if people:
        group_size = NUMBERS.get(people[1], int(people[1]) if people[1].isdigit() else None)
    origin = intent.get("origin") or intent.get("area") or intent.get("landmark") or old.get("origin")
    area_change = re.fullmatch(r"Change (?:area|starting area|origin) to (.{1,150})", text.strip(), re.I)
    if area_change:
        origin = area_change[1].strip()
    if origin and re.fullmatch(r"(?:here|near me|my location|current location)", origin, re.I):
        origin = None
    include = re.search(r"\badd\s+(.{2,120}?)(?:\s+(?:to (?:the |my )?(?:plan|itinerary)|please)|[.!?]|$)", text, re.I)
    include_place = include[1].strip() if include else None
    if include_place and re.search(r"\b(?:food|nature|temples|shopping|heritage|indoor)\b", include_place, re.I):
        include_place = None
    remove = re.search(r"\b(?:remove|skip)\s+(?:the\s+)?(.{2,120}?)(?:\s+from\s+|[.!?]|$)", text, re.I)
    replace = re.search(r"\breplace\s+(?:the\s+)?(.{2,80}?)\s+with\s+(.{2,120}?)[.!?]?$", text, re.I)
    excluded = list(old.get("excluded", []))
    if remove:
        excluded.append(remove[1].strip())
    if replace:
        excluded.append(replace[1].strip())
        include_place = replace[2].strip()
    return {"duration_hours": hours, "duration_defaulted": explicit_duration is None and not old and duration is None,
            "origin": origin, "date_time": date_time, "interests": interests or old.get("interests", []),
            "preferences": preferences, "food_preferences": food_preferences, "include_food": include_food,
            "budget": deepcopy(intent.get("budget") or old.get("budget")), "budget_verified": False,
            "travel_mode": intent.get("transport_preference") or old.get("travel_mode"), "group_size": group_size,
            "limited_walking": old.get("limited_walking", False) or bool(re.search(r"\b(?:avoid|less|not much|limited|too much)\b.{0,25}\bwalking\b", lower)),
            "lower_cost_requested": old.get("lower_cost_requested", False) or bool(re.search(r"\b(?:cheaper|low[ -]cost|budget|affordable)\b", lower)) or bool(intent.get("budget")),
            "indoor_preferred": old.get("indoor_preferred", False) or bool(re.search(r"\b(?:rainy|indoor|indoors)\b", lower)),
            "include_place": include_place, "excluded": list(dict.fromkeys(excluded))[-8:]}


def _distance(first, second):
    lat1, lat2 = math.radians(first["latitude"]), math.radians(second["latitude"])
    dlat = lat2 - lat1
    dlon = math.radians(second["longitude"] - first["longitude"])
    return 2 * math.asin(min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)))


def _order(records, origin=None):
    remaining = records[:]
    ordered = []
    point = origin
    while remaining:
        if point and any(item.get("location") for item in remaining):
            chosen = min(remaining, key=lambda item: _distance(point, item["location"]) if item.get("location") else float("inf"))
        else:
            chosen = remaining[0]
        remaining.remove(chosen)
        ordered.append(chosen)
        point = chosen.get("location") or point
    return ordered


def tourism_quick_replies():
    return [{"id": "tourism_shorter", "label": "Shorter plan", "kind": "request"},
            {"id": "tourism_food", "label": "Add food", "kind": "request"},
            {"id": "tourism_nature", "label": "More nature", "kind": "request"},
            {"id": "change_area", "label": "Change starting area", "kind": "input"}]


def plan_itinerary(intent, places, weather=None, previous=None, itinerary_id=None):
    old = _previous_section(previous)
    constraints = constraints_for(intent, old)
    identifier = itinerary_id or (old or {}).get("itinerary", {}).get("id") or "IT-" + token_urlsafe(18)
    hours = constraints["duration_hours"]
    max_stops = 1 if hours <= 1.5 else 2 if hours <= 3 else 3 if hours <= 6 else 5
    if constraints["limited_walking"] or constraints["lower_cost_requested"]:
        max_stops = min(max_stops, 3)  # Fewer transfers, never a price/accessibility guarantee.
    notices = list(LIMITATIONS)
    if constraints["duration_defaulted"]:
        notices.append("Six hours is a planning default; tell me your actual available time.")
    if not constraints["origin"]:
        notices.append("What is your starting area? Stop order is not anchored to your location yet.")
    if constraints["budget"] or constraints["lower_cost_requested"]:
        notices.append("Fewer stops can reduce transfers, but this plan cannot confirm ticket, meal or travel costs or fit your budget.")
    if constraints["limited_walking"]:
        notices.append("The plan uses fewer stops; actual walking distances and accessible routes remain unverified.")
    forecast = None
    sources = []
    weather_status = "not_requested"
    if weather is not None:
        try:
            # Reject dates outside the existing forecast horizon without HTTP.
            time_window(constraints["date_time"], weather.clock() if hasattr(weather, "clock") else datetime.now(timezone.utc))
            forecast = weather.forecast(constraints["date_time"])
            weather_status = "ok"
            sources.append(forecast["source"])
            if forecast["precipitation_probability_max"] >= 60:
                constraints['indoor_preferred'] = True
                notices.append("Rain is likely in the requested period. Carry an umbrella and consider indoor alternatives; the forecast is not an official warning.")
            else:
                notices.append("Check the forecast before leaving; city-level weather is not a guarantee at each stop.")
        except CityServiceError as error:
            weather_status = error.code
            notices.append("Weather forecast unavailable for this plan; no weather conditions have been assumed.")
    old_constraints = (old or {}).get("itinerary", {}).get("constraints", {})
    unchanged_search = old and hours <= old_constraints.get("duration_hours", hours) and all(constraints.get(key) == old_constraints.get(key) for key in ("origin", "interests", "preferences", "food_preferences", "indoor_preferred", "date_time"))
    attractions, food = [], []
    search_intent = deepcopy(intent)
    search_intent.update(area=constraints["origin"], landmark=None, exact_location=None, original_text="Bengaluru sightseeing plan")
    preferences = [*constraints["interests"], *constraints["preferences"]]
    if constraints["indoor_preferred"]:
        preferences.append("indoor attractions and museums for a rainy visit")
    search_intent["preferences"] = preferences
    if unchanged_search:
        for item in old.get("places", []):
            (food if item.get("itinerary_kind") == "food" else attractions).append(deepcopy(item))
        sources.extend(source for source in old.get("sources", []) if source.get("name") != "Open-Meteo")
    else:
        found = places.search(search_intent, "tourism", 5)
        attractions = [deepcopy(place) for place in found["places"]]
        sources.append(found["source"])
    if constraints["include_food"] and not food:
        search_intent["preferences"] = constraints["food_preferences"]
        try:
            found = places.search(search_intent, "food_places", 1)
            food = [deepcopy(place) for place in found["places"][:1]]
            sources.append(found["source"])
        except CityServiceError:
            notices.append("The food search was unavailable. No restaurant or menu result was invented.")
    if constraints["include_place"]:
        try:
            resolved = places.resolve(constraints["include_place"])
            if resolved["status"] == "resolved":
                requested = deepcopy(resolved["place"])
                attractions = [requested, *[item for item in attractions if item["id"] != requested["id"]]]
            else:
                notices.append("Please identify the added stop more precisely; it could not be resolved uniquely.")
        except CityServiceError:
            notices.append("The added stop could not be looked up. Existing valid stops are retained.")
    exclusions = [name.casefold() for name in constraints["excluded"]]
    records, seen = [], set()
    for item in attractions:
        if item["id"] in seen or any(name in item["name"].casefold() for name in exclusions):
            continue
        seen.add(item["id"])
        item.update(category="tourism", itinerary_kind="attraction")
        records.append(item)
    reserve_food = bool(constraints["include_food"] and food and max_stops > 1)
    records = records[:max_stops - int(reserve_food)]
    if reserve_food:
        candidate = next((item for item in food if item["id"] not in seen and not any(name in item["name"].casefold() for name in exclusions)), None)
        if candidate:
            candidate.update(category="tourism", itinerary_kind="food")
            records.append(candidate)
    origin_location = None
    origin_resolution = None
    if constraints["origin"]:
        old_origin = (old or {}).get("itinerary", {}).get("origin_resolution")
        try:
            origin_resolution = old_origin if old_origin and old_origin.get("status") == "resolved" and old_origin.get("original_text") == constraints["origin"] else places.resolve(constraints["origin"])
            if origin_resolution["status"] == "resolved":
                origin_location = origin_resolution["place"]["location"]
            else:
                notices.append("Please give a more specific starting area for origin-based ordering.")
        except CityServiceError:
            notices.append("Starting-point lookup is unavailable; no origin coordinate was invented.")
    records = _order(records, origin_location)
    # Move the optional meal into the middle without asserting a meal time or
    # introducing fabricated opening hours. Food remains an actual provider hit.
    if len(records) > 2 and reserve_food:
        meal = next((item for item in records if item["itinerary_kind"] == "food"), None)
        if meal:
            records.remove(meal)
            records.insert(len(records) // 2 + 1, meal)
    minutes = min(90, max(20, int(hours * 60 * .65 / max(1, len(records)) / 5) * 5))
    stops = []
    for index, item in enumerate(records, 1):
        reason = "A food stop returned for your requested preferences; menu availability is unverified." if item["itinerary_kind"] == "food" else "Returned by Google Places for this sightseeing search" + (" with " + ", ".join(constraints["interests"]) + " interests." if constraints["interests"] else ".")
        item.update(itinerary_order=index, visit_minutes=minutes, planning_reason=reason)
        # All Directions stay attached to the selected provider place identity.
        item["directions_url"] = directions_url(item["name"] + ", Bengaluru", mode=travel_mode(constraints["travel_mode"]), place_id=item["id"])
        stops.append({"place_id": item["id"], "order": index, "visit_minutes": minutes, "reason": reason})
    summary = f"Here's a {hours:g}-hour Bengaluru plan with {len(records)} {'stop' if len(records) == 1 else 'stops'}." if records else "No matching stops were returned for this plan. Try another interest or area."
    if constraints["origin"] and records:
        summary += f" Starting area: {constraints['origin']}."
    elif records:
        summary += " Tell me your starting area to refine the order."
    if constraints["include_food"] and not any(item["itinerary_kind"] == "food" for item in records):
        notices.append("No separate food stop fits this returned plan; food availability is not confirmed.")
    details = [f"{minutes} minutes per stop is an indicative visit estimate.", "Time remaining is a planning buffer for journeys and breaks, not a route duration."]
    if constraints["group_size"]:
        details.append(f"Requested group size: {constraints['group_size']}; capacity and group pricing are unverified.")
    return {"intent": "tourism", "status": "ok" if records else "no_results", "summary": summary,
            "description": summary + " " + " ".join(notices), "details": details, "places": records,
            "sources": list({(source['name'], source['url']): source for source in sources}.values()), "limitations": notices,
            "itinerary": {"id": identifier, "constraints": constraints, "stops": stops, "origin_resolution": origin_resolution,
                          "weather_status": weather_status, "weather": forecast, "route_geometry": None,
                          "ordering": "straight_line_heuristic", "duration_estimates": True}}
