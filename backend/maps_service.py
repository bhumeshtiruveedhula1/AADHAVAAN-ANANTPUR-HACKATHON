"""URL handoff only: no route, fare, ETA or origin calculation."""
from urllib.parse import urlencode


def directions_url(destination, origin=None, mode=None, place_id=None, origin_id=None):
    if not isinstance(destination, str) or not destination.strip():
        return None
    params = {"api": "1", "destination": destination}
    if origin:
        params["origin"] = origin
        if origin_id:
            params["origin_place_id"] = origin_id
    if place_id:
        params["destination_place_id"] = place_id
    if mode in {"transit", "walking", "driving", "bicycling"}:
        params["travelmode"] = mode
    url = "https://www.google.com/maps/dir/?" + urlencode(params)
    return url if len(url) <= 2048 else None


def travel_mode(preference):
    text = (preference or "").casefold()
    if any(word in text for word in ("metro", "bus", "transit", "train", "public transport")):
        return "transit"
    if text in ("walk", "walking", "on foot"):
        return "walking"
    if text in ("car", "driving", "drive"):
        return "driving"
    if text in ("cycle", "cycling", "bicycle", "bicycling"):
        return "bicycling"
    return None
