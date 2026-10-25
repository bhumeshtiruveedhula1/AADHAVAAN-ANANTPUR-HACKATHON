"""Bounded ephemeral sessions. No accounts, cross-channel linking, or disk history."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import re
from secrets import token_urlsafe
from threading import Lock
from time import monotonic


class SessionError(Exception):
    pass


class SessionStore:
    def __init__(self, capacity=128, ttl=900, clock=monotonic):
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.entries, self.guard = {}, Lock()

    @contextmanager
    def turn(self, channel, identifier=None):
        with self.guard:
            now = self.clock()
            for key in list(self.entries):
                entry = self.entries[key]
                if now - entry["used"] >= self.ttl and not entry["lock"].locked():
                    del self.entries[key]
            key = (channel, identifier)
            if key not in self.entries:
                if len(self.entries) >= self.capacity:
                    raise SessionError("session_capacity")
                identifier = token_urlsafe(24)
                key = (channel, identifier)
                self.entries[key] = {"used": now, "lock": Lock(), "result": None}
            entry = self.entries[key]
            if not entry["lock"].acquire(False):
                raise SessionError("session_busy")
            entry["used"] = now
        try:
            yield identifier, entry
        finally:
            entry["used"] = self.clock()
            entry["lock"].release()


SESSIONS = SessionStore()
MODE = re.compile(r"^(?:(?:actually\s+)?(?:i(?:'ll| will)\s+)?|by\s+|switch to\s+)(metro|bus|walk|walking|transit|driving|car)[.!?]?$", re.I)


def prepare(request, previous):
    """Only explicit follow-ups get prior context; fresh main-input requests reset it."""
    old = previous.get("understanding") if previous else None
    continuing = bool(old and (request.follow_up or request.quick_reply or MODE.fullmatch(request.message.strip())))
    if not continuing:
        return request, None
    context = dict(request.context or {})
    context["recent_messages"] = [old["original_text"][:1000]]
    # History is evidence, not unconditional field defaults. The model can
    # identify a new workflow; Python merges only a matching workflow afterward.
    context["recent_messages"].append("Previous request constraints: " + "; ".join(
        f"{name}: {old[name]}" for name in ("origin", "destination", "area", "transport_preference", "budget", "time_constraint", "preferences") if old.get(name))[:900])
    itinerary = next((section.get('itinerary') for section in previous.get('sections', []) if section.get('itinerary')), None)
    if itinerary:
        context['recent_messages'].append(('Current itinerary constraints: ' + str(itinerary['constraints']))[:1000])
    return replace(request, context=context), old


def refined_intent(request, old, previous):
    if not old:
        return None
    quick = request.quick_reply
    if quick and quick not in {tile["id"] for tile in previous.get("quick_replies", []) if tile.get("kind") == "request"}:
        raise SessionError("invalid_quick_reply")
    mode = MODE.fullmatch(request.message.strip())
    if old['primary_intent'] == 'tourism' and (quick in {'tourism_shorter', 'tourism_food', 'tourism_nature'} or mode or re.match(r"^(?:make (?:it|the (?:plan|itinerary)) |add |remove |skip |replace |plan (?:it )?for |avoid )", request.message.strip(), re.I)):
        result = deepcopy(old)
        message = {'tourism_shorter': 'Shorter plan', 'tourism_food': 'Add food', 'tourism_nature': 'More nature'}.get(quick, request.message)
        result.update(original_text=message, action=None, method='session_rules')
        if mode:
            result['transport_preference'] = mode.group(1).lower()
        return result
    edit = re.fullmatch(r"Change (destination|area) to (.{1,200})", request.message.strip(), re.I)
    if not quick and not edit and not (mode and old["primary_intent"] == "transport"):
        return None
    result = deepcopy(old)
    result.update(original_text=request.message, action=None, method="session_rules")
    if edit:
        result[edit.group(1).lower()] = edit.group(2).strip()
        if edit.group(1).lower() == "area":
            result["landmark"] = None
            if result['primary_intent'] == 'tourism':
                result['origin'] = edit.group(2).strip()
    elif quick in {"mode_walk", "mode_metro", "mode_bus"} or mode:
        result["transport_preference"] = {"mode_walk": "walking", "mode_metro": "metro", "mode_bus": "bus"}.get(quick) or mode.group(1).lower()
        result["preferences"] = [result["transport_preference"]]
    elif quick == "weather_tomorrow":
        result["time_constraint"] = "tomorrow"
    elif quick in {"food_nearby", "vegetarian", "paneer"}:
        result.update(primary_intent="food_places", secondary_intents=[], origin=None, destination=None,
                      budget=None, time_constraint=None, suggested_service_handler="places_service")
        if quick == "food_nearby":
            result["preferences"] = []
        elif quick == "vegetarian":
            result["preferences"] = list(dict.fromkeys([*result["preferences"], "vegetarian"]))
        else:
            result["preferences"] = list(dict.fromkeys([*result["preferences"], "paneer"]))
    # Keep provenance aligned with refined fields instead of retaining stale
    # model entities for a former destination/mode/time in the same result.
    for field, kind in (("destination", "destination"), ("origin", "origin"), ("area", "area"),
                        ("landmark", "landmark"), ("budget", "budget"), ("transport_preference", "transport_preference"),
                        ("time_constraint", "time"), ("preferences", "preference")):
        if result.get(field) != old.get(field):
            result["entities"] = [entity for entity in result.get("entities", []) if entity["type"] != kind]
            value = result.get(field)
            for item in value if isinstance(value, list) else [value] if isinstance(value, str) else []:
                result["entities"].append({"type": kind, "value": item, "evidence": request.message, "source": "session_rules"})
    return refresh_missing(result)


def merge_followup(current, old):
    if not old:
        return current
    if current["primary_intent"] != old["primary_intent"]:
        if current.get("method") != "session_rules":
            for field in ("origin", "destination", "area", "landmark", "budget", "transport_preference", "time_constraint"):
                if current.get(field) == old.get(field) and str(current.get(field) or "").casefold() not in current["original_text"].casefold():
                    current[field] = None
            for field in ("preferences", "accessibility_constraints"):
                current[field] = [value for value in current.get(field, []) if value not in old.get(field, []) or value.casefold() in current["original_text"].casefold()]
        return refresh_missing(current)
    for name in ("origin", "destination", "area", "landmark", "transport_preference", "budget", "time_constraint"):
        if current.get(name) is None:
            current[name] = deepcopy(old.get(name))
    for name in ("preferences", "accessibility_constraints"):
        if not current.get(name):
            current[name] = deepcopy(old.get(name, []))
    return refresh_missing(current)


def refresh_missing(intent):
    missing = list(intent.get("missing_information", []))
    for field in ("city", "origin", "destination"):
        if intent.get(field) and field in missing:
            missing.remove(field)
    required = ["city"] + (["origin", "destination"] if intent["primary_intent"] == "transport" else [])
    for field in required:
        if not intent.get(field) and field not in missing:
            missing.append(field)
    intent["missing_information"] = missing
    intent["clarification_needed"] = bool(missing)
    intent["processing_status"] = "clarification_required" if missing else "understood"
    return intent


def quick_replies(result):
    intent = result.get("understanding", {})
    if result.get('status') != 'unsupported_city' and intent.get('primary_intent') in {'civic_report', 'civic_lookup'}:
        return [{'id': 'open_report', 'label': 'Open Report Issue', 'kind': 'local'}, {'id': 'my_reports', 'label': 'My reports', 'kind': 'local'}]
    if intent.get("action") or result.get("status") in {"unsupported_city", "clarification_required"}:
        return []
    category = intent.get("primary_intent")
    if category == 'tourism' and any(section.get('itinerary') for section in result.get('sections', [])):
        from tourism_service import tourism_quick_replies
        return tourism_quick_replies()
    def tile(identifier, label, kind="request"):
        return {"id": identifier, "label": label, "kind": kind}
    if category == "weather_conditions" and result.get("status") == "ok":
        return [tile("show_details", "Hourly forecast", "local"), tile("weather_tomorrow", "Tomorrow")]
    if category == "transport":
        tiles = [tile("mode_walk", "Walk"), tile("mode_metro", "Metro options"), tile("mode_bus", "Bus options"), tile("change_destination", "Change destination", "input")]
        current = (intent.get("transport_preference") or "").lower()
        return [t for t in tiles if not (t["id"] == "mode_walk" and current in {"walk", "walking"}) and not (t["id"] == "mode_metro" and current == "metro") and not (t["id"] == "mode_bus" and current == "bus")]
    if category == "accommodation":
        return [tile("change_area", "Change area", "input"), tile("food_nearby", "Food nearby"), tile("change_budget", "Change budget", "input")]
    if category == "food_places":
        tiles = [tile("vegetarian", "Vegetarian options"), tile("change_area", "Change area", "input")]
        if any("paneer" in p.casefold() for p in intent.get("preferences", [])):
            tiles.insert(0, tile("paneer", "Search paneer options"))
        return tiles
    return []
