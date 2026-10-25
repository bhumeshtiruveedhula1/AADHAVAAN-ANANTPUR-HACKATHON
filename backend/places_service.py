"""Google Places (New) discovery, never availability/booking verification."""
from datetime import datetime, timezone
import math
import os
import re
from urllib.parse import urlsplit, parse_qs

from city_http import CityServiceError, fetch_json
from maps_service import directions_url

URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.attributions,places.photos"
# Deliberately bounded search viewport, not a claimed municipal boundary or GPS.
VIEWPORT = {"low": {"latitude": 12.8, "longitude": 77.4}, "high": {"latitude": 13.2, "longitude": 77.8}}
CATEGORIES = {"accommodation": "hotels and lodges", "food_places": "restaurants and food",
              "places_services": None, "tourism": "tourist attractions"}


def safe_https(value, google_only=False):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        if google_only and parsed.hostname not in {"maps.google.com", "www.google.com", "maps.app.goo.gl"}:
            return None
        return value
    except ValueError:
        return None


class PlacesService:
    def __init__(self, client=None, api_key=None, clock=None):
        self.client = client
        self.api_key = api_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(self, text):
        """Resolve a user-named Bengaluru location, never use the first vague hit."""
        if not text or re.fullmatch(r"(?:here|there|near me|my location|current location)", text.strip(), re.I):
            return {"status": "missing_location", "original_text": text, "place": None, "candidates": []}
        key = self.api_key if self.api_key is not None else os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        if not key or key.startswith("YOUR_"):
            raise CityServiceError("not_configured")
        mask = "places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.attributions"
        payload = fetch_json("POST", URL, self.client, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask},
                             json={"textQuery": f"{text}, Bengaluru", "pageSize": 3, "languageCode": "en", "regionCode": "IN",
                                   "locationRestriction": {"rectangle": VIEWPORT}})
        raw = payload.get("places", [])
        if not isinstance(raw, list):
            raise CityServiceError("invalid_response")
        candidates = []
        for item in raw[:3]:
            place = self._place(item, "places_services", self.clock().isoformat())
            if place and place["location"] and place["id"] not in {p["id"] for p in candidates}:
                candidates.append(place)
        def normal(value):
            value = re.sub(r"\bm\.?\s*g\.?\s+road\b", "mahatma gandhi road", value.casefold())
            return re.sub(r"[^\w]+", " ", value).strip()
        query = normal(text)
        compact = lambda value: normal(value).replace(" ", "")
        exact = [p for p in candidates if compact(p["name"]) == compact(text)]
        # A unique locality address "<name>, Bengaluru, ..." distinguishes a
        # whole neighbourhood from same-named businesses/sub-areas elsewhere.
        # Never pick a top-ranked hit solely because Google returned it first.
        localities = []
        for place in exact:
            parts = [part.strip() for part in place["address"].split(",")]
            if len(parts) >= 2 and compact(parts[0]) == compact(text) and normal(parts[1]) in {"bengaluru", "bangalore"}:
                localities.append(place)
        matching = [p for p in candidates if set(query.split()) <= set(normal(p["name"]).split())]
        chosen = exact[0] if len(exact) == 1 else localities[0] if len(localities) == 1 else matching[0] if len(matching) == 1 else None
        return {"status": "resolved" if chosen else "ambiguous" if candidates else "no_results", "original_text": text,
                "confidence": ("locality_address_match" if len(exact) > 1 else "name_match") if chosen else "unresolved", "place": chosen,
                "candidates": [] if chosen else [{"id": p["id"], "name": p["name"], "address": p["address"]} for p in candidates]}

    def search(self, intent, category, limit=5):
        key = self.api_key if self.api_key is not None else os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        if not key or key.startswith("YOUR_"):
            raise CityServiceError("not_configured")
        if category not in CATEGORIES:
            raise CityServiceError("unsupported_service")
        area = intent.get("area") or intent.get("landmark") or intent.get("exact_location")
        if re.search(r"\bnear me\b|\bnearby\b", intent["original_text"], re.I) and not area:
            raise CityServiceError("missing_location")
        subject = CATEGORIES[category] or intent["original_text"][:500]
        query = f"{subject} {'near ' + area if area else ''} in Bengaluru"
        preferences = intent.get("preferences", []) + intent.get("accessibility_constraints", [])
        if preferences:
            query += "; requested preferences: " + ", ".join(preferences)[:500]
        # Budget/time stay in understanding: text search cannot verify rates or check-in.
        limit = min(5, max(1, limit))
        data = fetch_json("POST", URL, self.client,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
                          json={"textQuery": query, "pageSize": limit, "languageCode": "en",
                                "regionCode": "IN", "locationRestriction": {"rectangle": VIEWPORT}})
        raw_places = data.get("places", [])
        if not isinstance(raw_places, list):
            raise CityServiceError("invalid_response")
        retrieved = self.clock().isoformat()
        places, rejected, photo_attempts = [], 0, 0
        for raw in raw_places[:5]:
            place = self._place(raw, category, retrieved)
            if place is None:
                rejected += 1
            elif place["id"] not in {p["id"] for p in places}:
                # At most two photo-media requests per search, four per mixed
                # response. Optional media has a shorter timeout and never fails
                # the underlying listing. Names are used once, never cached.
                if len(places) < limit and photo_attempts < 2:
                    photo = self._photo_metadata(raw, place["id"])
                    if photo:
                        photo_attempts += 1
                        try:
                            media = fetch_json("GET", "https://places.googleapis.com/v1/" + photo.pop("resource") + "/media", self.client,
                                               headers={"X-Goog-Api-Key": key},
                                               params={"maxWidthPx": 800, "maxHeightPx": 400, "skipHttpRedirect": "true"}, timeout=1.5)
                            uri = safe_photo_uri(media.get("photoUri"), key)
                            if not uri:
                                raise CityServiceError("invalid_response")
                            place.update(image=uri, photo={**photo, "url": uri}, photo_status="available")
                        except CityServiceError as error:
                            place["photo_status"] = error.code
                places.append(place)
        if raw_places and not places:
            raise CityServiceError("invalid_response")
        return {"places": places[:limit], "rejected_records": rejected,
                "source": {"name": "Google Maps", "url": "https://maps.google.com/", "attribution": "Google Maps", "retrieved_at": retrieved},
                "query": query, "cached": False}

    @staticmethod
    def _photo_metadata(raw, identifier):
        photos = raw.get("photos")
        if not isinstance(photos, list):
            return None
        for photo in photos[:10]:
            if not isinstance(photo, dict):
                continue
            resource = photo.get("name", "")
            if not isinstance(resource, str) or not re.fullmatch(r"places/" + re.escape(identifier) + r"/photos/[A-Za-z0-9_-]{1,4096}", resource):
                continue
            credits = photo.get("authorAttributions")
            if not isinstance(credits, list) or len(credits) > 20:
                continue
            authors = []
            for credit in credits:
                if not isinstance(credit, dict) or not isinstance(credit.get("displayName"), str) or not credit["displayName"].strip():
                    break
                uri = safe_https(credit.get("uri"))
                if credit.get("uri") and not uri:
                    break
                authors.append({"name": credit["displayName"], "uri": uri,
                                "avatar_url": safe_photo_uri(credit.get("photoUri"))})
            else:
                source = safe_https(photo.get("googleMapsUri"), google_only=True)
                if photo.get("googleMapsUri") and not source:
                    continue
                return {"resource": resource, "authors": authors, "source_url": source}
        return None

    @staticmethod
    def _place(raw, category, retrieved):
        try:
            identifier, name, address = raw["id"], raw["displayName"]["text"], raw["formattedAddress"]
            if not all(isinstance(v, str) and 0 < len(v.strip()) <= 1000 for v in (identifier, name, address)):
                return None
            if not re.search(r"\b(?:Bengaluru|Bangalore)\b", address, re.I):
                return None
            location = raw.get("location")
            if location is not None:
                lat, lon = location["latitude"], location["longitude"]
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lon)):
                    return None
                if not (12.8 <= lat <= 13.2 and 77.4 <= lon <= 77.8):
                    return None
                location = {"latitude": lat, "longitude": lon}
            attributions = raw.get("attributions", [])
            if not isinstance(attributions, list):
                return None
            credits = []
            for credit in attributions:
                if not isinstance(credit, dict) or not isinstance(credit.get("provider"), str):
                    return None
                uri = safe_https(credit.get("providerUri"))
                if credit.get("providerUri") and not uri:
                    return None
                credits.append({"provider": credit["provider"], "provider_uri": uri})
            return {"id": identifier, "name": name, "address": address, "location": location,
                    "category": category, "source": "Google Maps", "retrieved_at": retrieved,
                    "source_url": safe_https(raw.get("googleMapsUri"), google_only=True),
                    "directions_url": directions_url(f"{name}, {address}", place_id=identifier),
                    "attributions": credits, "image": None, "photo": None, "photo_status": "unavailable", "price": None, "availability": None,
                    "immediate_check_in": None, "open_now": None, "accessibility": None}
        except (KeyError, TypeError, AttributeError):
            return None


def safe_photo_uri(value, key=None):
    uri = safe_https(value)
    if not uri or (key and key in uri):
        return None
    parsed = urlsplit(uri)
    if any(name.casefold() in {"key", "api_key", "x-goog-api-key"} for name in parse_qs(parsed.query)):
        return None
    if not any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in ("googleusercontent.com", "ggpht.com")):
        return None
    return uri
