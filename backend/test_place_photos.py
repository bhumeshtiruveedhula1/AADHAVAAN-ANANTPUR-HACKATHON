"""Photos are optional, source-matched, bounded, attributed and never persisted."""
import json
import unittest

import httpx

from city_services import compose
from places_service import PlacesService, FIELD_MASK
from test_city_services import place_payload, intent


def photo_payload(identifier="fixture-hotel"):
    data = place_payload(identifier)
    data["places"][0]["photos"] = [{"name": f"places/{identifier}/photos/photo_token", "widthPx": 800, "heightPx": 400,
        "googleMapsUri": "https://www.google.com/maps/place/?photo=fixture", "authorAttributions": [
            {"displayName": "Fixture Photographer", "uri": "https://www.google.com/maps/contrib/123", "photoUri": "https://lh3.googleusercontent.com/avatar"}]}]
    return data


class PhotoTests(unittest.TestCase):
    def search(self, payload=None, photo_status=200, uri="https://lh3.googleusercontent.com/photo", failure=None):
        calls = []
        def handle(request):
            calls.append(request)
            if request.method == "POST":
                return httpx.Response(200, json=payload if payload is not None else photo_payload())
            if failure:
                raise failure("mock optional photo failure", request=request)
            return httpx.Response(photo_status, json={"photoUri": uri})
        client = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        service = PlacesService(client, api_key="private-sentinel")
        return service, calls

    def test_documented_photo_media_request_and_credits(self):
        service, calls = self.search()
        result = service.search(intent("accommodation", "Hotel"), "accommodation")
        place = result["places"][0]
        self.assertIn("places.photos", FIELD_MASK)
        self.assertEqual(len(calls), 2)
        request = calls[1]
        self.assertEqual(request.url.path, "/v1/places/fixture-hotel/photos/photo_token/media")
        self.assertEqual(request.url.params["skipHttpRedirect"], "true")
        self.assertEqual(request.url.params["maxWidthPx"], "800")
        self.assertEqual(request.headers["X-Goog-Api-Key"], "private-sentinel")
        self.assertNotIn("private-sentinel", str(request.url))
        self.assertEqual(request.extensions["timeout"]["read"], 1.5)
        self.assertEqual(place["photo"]["authors"][0]["name"], "Fixture Photographer")
        self.assertEqual(place["photo"]["source_url"], "https://www.google.com/maps/place/?photo=fixture")
        self.assertEqual(place["photo_status"], "available")
        self.assertEqual(place["image"], place["photo"]["url"])
        self.assertNotIn("private-sentinel", json.dumps(result))
        self.assertNotIn("photo_token", json.dumps(result))

    def test_optional_media_failures_never_discard_place_results(self):
        for kwargs, expected in (({"photo_status": 429}, "rate_limited"), ({"photo_status": 403}, "authentication_error"),
                                 ({"photo_status": 500}, "provider_error"), ({"failure": httpx.ReadTimeout}, "timeout")):
            service, calls = self.search(**kwargs)
            result = compose(intent("accommodation", "Hotel"), places=service)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["places"]), 1)
            self.assertEqual(result["places"][0]["photo_status"], expected)
            self.assertIsNone(result["places"][0]["image"])
            self.assertEqual(len(calls), 2)

    def test_photo_requests_are_bounded_to_two_per_search(self):
        payload = {"places": [photo_payload(f"fixture-{i}")["places"][0] for i in range(5)]}
        service, calls = self.search(payload)
        result = service.search(intent("accommodation", "Hotel"), "accommodation")
        self.assertEqual(len(result["places"]), 5)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(bool(place["photo"]) for place in result["places"]), 2)

    def test_photo_requests_follow_visible_limit(self):
        payload = {"places": [photo_payload(f"fixture-{i}")["places"][0] for i in range(5)]}
        service, calls = self.search(payload)
        result = service.search(intent("food_places", "Food"), "food_places", limit=1)
        self.assertEqual(len(result["places"]), 1)
        self.assertEqual(len(calls), 2)

    def test_cross_place_resource_and_path_injection_never_requested(self):
        for name in ("places/other/photos/token", "places/fixture-hotel/photos/../../token", "https://evil.test/photo", "places/fixture-hotel/photos/token?key=oops"):
            payload = photo_payload()
            payload["places"][0]["photos"][0]["name"] = name
            service, calls = self.search(payload)
            result = service.search(intent("accommodation", "Hotel"), "accommodation")
            self.assertEqual(len(calls), 1)
            self.assertIsNone(result["places"][0]["photo"])

    def test_invalid_author_or_source_metadata_omits_photo(self):
        for field, value in (("authorAttributions", None), ("authorAttributions", [{"displayName": "Missing safe link", "uri": "javascript:bad"}]),
                             ("googleMapsUri", "https://evil.test/photo")):
            payload = photo_payload()
            payload["places"][0]["photos"][0][field] = value
            service, calls = self.search(payload)
            result = service.search(intent("accommodation", "Hotel"), "accommodation")
            self.assertIsNone(result["places"][0]["photo"])
            self.assertEqual(len(calls), 1)

    def test_unsafe_photo_uri_or_key_leak_is_not_returned(self):
        for uri in ("http://lh3.googleusercontent.com/photo", "https://evil.test/photo", "https://lh3.googleusercontent.com/photo?key=private-sentinel", "https://lh3.googleusercontent.com/private-sentinel"):
            service, _ = self.search(uri=uri)
            result = service.search(intent("accommodation", "Hotel"), "accommodation")
            self.assertIsNone(result["places"][0]["image"])
            self.assertNotIn("private-sentinel", json.dumps(result))

    def test_missing_optional_photos_requires_no_media_request(self):
        service, calls = self.search(place_payload())
        result = service.search(intent("accommodation", "Hotel"), "accommodation")
        self.assertIsNone(result["places"][0]["image"])
        self.assertEqual(len(calls), 1)

    def test_mixed_request_caps_four_media_calls_and_preserves_order(self):
        payload = {"places": [photo_payload(f"fixture-{i}")["places"][0] for i in range(3)]}
        service, calls = self.search(payload)
        result = compose(intent("accommodation", "Hotel and food", secondary_intents=["food_places"]), places=service)
        self.assertEqual(len(calls), 6)  # Two searches, four photos; zero extra inference.
        self.assertEqual([section["intent"] for section in result["sections"]], ["accommodation", "food_places"])
        self.assertEqual(result["places"][3]["category"], "food_places")
        self.assertTrue(result["display_requirements"]["android_display_ready"])


if __name__ == "__main__":
    unittest.main()
