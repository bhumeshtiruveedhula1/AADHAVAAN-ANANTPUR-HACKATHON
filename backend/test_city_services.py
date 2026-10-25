"""City services use mocked HTTP only; fixtures are synthetic, not real listings."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from app import create_app
from city_http import CityServiceError, fetch_json, _SLOTS
from city_services import compose
from intents import understand
from maps_service import directions_url
from places_service import PlacesService, URL as PLACES_URL, FIELD_MASK
from service import TextRequest, process_text
from test_intents import extraction
from weather_service import WeatherService, IST, URL as WEATHER_URL

NOW = datetime(2026, 10, 24, 20, tzinfo=timezone.utc)  # 25 October 01:30 IST


def forecast_payload():
    start = NOW.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"timezone": "Asia/Kolkata", "utc_offset_seconds": 19800,
            "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation_probability": "%", "precipitation": "mm"},
            "hourly": {"time": [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(168)],
                       "temperature_2m": [22.5] * 168, "precipitation_probability": [70] * 168, "precipitation": [0.2] * 168}}


def place_payload(identifier="fixture-hotel", name="Synthetic test listing"):
    return {"places": [{"id": identifier, "displayName": {"text": name}, "formattedAddress": "Test road, Majestic, Bengaluru, Karnataka, India",
                        "location": {"latitude": 12.98, "longitude": 77.57}, "googleMapsUri": "https://maps.google.com/?cid=123",
                        "attributions": [{"provider": "Synthetic attribution", "providerUri": "https://example.test/credit"}]}]}


def http_client(payload=None, status=200, failure=None):
    calls = []
    def handler(request):
        calls.append(request)
        if failure:
            raise failure("mock failure", request=request)
        return httpx.Response(status, json=payload if payload is not None else {})
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def intent(category, text, city="Bengaluru", **fields):
    return understand(text, city, provider=Mock(extract=Mock(return_value=extraction(category, **fields))))


class WeatherTests(unittest.TestCase):
    def service(self, payload=None, **kwargs):
        client, calls = http_client(payload if payload is not None else forecast_payload(), **kwargs)
        self.addCleanup(client.close)
        return WeatherService(client, clock=lambda: NOW), calls

    def test_today_uses_bengaluru_day_across_utc_boundary(self):
        service, calls = self.service()
        data = service.forecast("today")
        self.assertEqual(data["interval"]["start"], "2026-10-25T00:00:00+05:30")
        self.assertEqual(len(data["hourly"]), 24)
        self.assertEqual(str(calls[0].url).split("?")[0], WEATHER_URL)
        self.assertEqual(calls[0].url.params["timezone"], "Asia/Kolkata")

    def test_evening_uses_only_18_to_22(self):
        payload = forecast_payload()
        payload["hourly"]["precipitation_probability"][0] = 99
        service, _ = self.service(payload)
        data = service.forecast("this evening")
        self.assertEqual(len(data["hourly"]), 4)
        self.assertEqual(data["precipitation_probability_max"], 70)
        self.assertEqual(data["precipitation_total"], 0.8)
        self.assertIn("T18:00:00", data["interval"]["start"])

    def test_precipitation_uses_preceding_hour_semantics(self):
        payload = forecast_payload()
        payload["hourly"]["precipitation"][18] = 20  # 17–18: outside window
        payload["hourly"]["precipitation"][22] = 3   # 21–22: inside window
        payload["hourly"]["precipitation_probability"][18] = 99
        payload["hourly"]["precipitation_probability"][22] = 81
        service, _ = self.service(payload)
        data = service.forecast("this evening")
        self.assertEqual(data["precipitation_total"], 3.6)
        self.assertEqual(data["precipitation_probability_max"], 81)
        self.assertTrue(data["hourly"][0]["precipitation_interval_end"].endswith("T19:00:00+05:30"))

    def test_last_supported_day_has_next_midnight_for_rain_totals(self):
        payload = forecast_payload()
        final = datetime.fromisoformat(payload["hourly"]["time"][-1])
        payload["hourly"]["time"].extend([(final + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(1, 25)])
        for field, value in (("temperature_2m", 22.5), ("precipitation_probability", 70), ("precipitation", 0.2)):
            payload["hourly"][field].extend([value] * 24)
        service, calls = self.service(payload)
        data = service.forecast("2026-10-31")
        self.assertEqual(data["precipitation_total"], 4.8)
        self.assertEqual(data["hourly"][-1]["precipitation_interval_end"], "2026-11-01T00:00:00+05:30")
        self.assertEqual(calls[0].url.params["forecast_days"], "8")

    def test_tomorrow_calendar_and_tomorrow_evening(self):
        service, _ = self.service()
        for phrase, hours in (("tomorrow", 24), ("tomorrow evening", 4)):
            with self.subTest(phrase=phrase):
                data = service.forecast(phrase)
                self.assertTrue(data["interval"]["start"].startswith("2026-10-26"))
                self.assertEqual(len(data["hourly"]), hours)

    def test_forecast_units_source_reference_and_timestamp(self):
        service, _ = self.service()
        data = service.forecast()
        self.assertEqual(data["temperature_min"], 22.5)
        self.assertEqual(data["units"]["temperature_2m"], "°C")
        self.assertEqual(data["source"]["retrieved_at"], NOW.isoformat())
        self.assertIn("Open-Meteo", data["source"]["attribution"])
        self.assertIn("not user GPS", data["reference_location"]["scope"])

    def test_unsupported_time_and_range_never_call_provider(self):
        service, calls = self.service()
        for phrase, code in (("next month", "unsupported_time"), ("2026-11-30", "unsupported_range"), ("2026-10-24", "unsupported_range"), ("2026-99-99", "unsupported_time")):
            with self.subTest(phrase=phrase), self.assertRaises(CityServiceError) as caught:
                service.forecast(phrase)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(calls, [])

    def test_missing_hourly_and_nulls_are_not_zero_weather(self):
        for mutate, code in ((lambda p: p.pop("hourly"), "invalid_response"),
                             (lambda p: p["hourly"]["precipitation"].__setitem__(19, None), "missing_data"),
                             (lambda p: p["hourly"]["time"].pop(), "invalid_response")):
            payload = forecast_payload()
            mutate(payload)
            service, _ = self.service(payload)
            with self.assertRaises(CityServiceError) as caught:
                service.forecast("this evening")
            self.assertEqual(caught.exception.code, code)

    def test_wrong_units_timezone_nonfinite_and_duplicate_hours_rejected(self):
        mutations = [lambda p: p["hourly_units"].update(temperature_2m="°F"),
                     lambda p: p.update(timezone="UTC"),
                     lambda p: p["hourly"]["temperature_2m"].__setitem__(0, "hot"),
                     lambda p: p["hourly"]["precipitation_probability"].__setitem__(0, 101),
                     lambda p: p["hourly"]["time"].__setitem__(1, p["hourly"]["time"][0])]
        for mutate in mutations:
            payload = forecast_payload()
            mutate(payload)
            service, _ = self.service(payload)
            with self.assertRaises(CityServiceError):
                service.forecast()

    def test_timeout_and_http_failure(self):
        for kwargs, code in (({"failure": httpx.ReadTimeout}, "timeout"), ({"status": 500}, "provider_error"), ({"status": 429}, "rate_limited")):
            service, calls = self.service(**kwargs)
            with self.assertRaises(CityServiceError) as caught:
                service.forecast()
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(calls), 1)

    def test_five_minute_bounded_cache_preserves_original_retrieved_time(self):
        service, calls = self.service()
        timer = [0]
        service.timer = lambda: timer[0]
        first = service.forecast("today")
        second = service.forecast("tomorrow")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["source"]["retrieved_at"], second["source"]["retrieved_at"])
        self.assertEqual(len(calls), 1)
        timer[0] = 301
        service.forecast()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(service._cache), 1)

    def test_failed_request_does_not_poison_cache_or_next_request(self):
        count = [0]
        def handler(_):
            count[0] += 1
            return httpx.Response(500, json={}) if count[0] == 1 else httpx.Response(200, json=forecast_payload())
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            service = WeatherService(client, clock=lambda: NOW)
            with self.assertRaises(CityServiceError):
                service.forecast()
            self.assertEqual(service.forecast()["temperature_min"], 22.5)

    def test_expired_failed_refresh_never_returns_stale_as_fresh(self):
        service, _ = self.service()
        service.timer = lambda: 0
        service.forecast()
        service.timer = lambda: 301
        with patch("weather_service.fetch_json", side_effect=CityServiceError("timeout")):
            with self.assertRaises(CityServiceError):
                service.forecast()


class PlacesTests(unittest.TestCase):
    def service(self, payload=None, key="synthetic-secret", **kwargs):
        client, calls = http_client(payload if payload is not None else place_payload(), **kwargs)
        self.addCleanup(client.close)
        return PlacesService(client, api_key=key, clock=lambda: NOW), calls

    def test_food_area_query_field_mask_and_bounded_scope(self):
        service, calls = self.service()
        parsed = intent("food_places", "Find restaurants near Indiranagar", area="Indiranagar")
        result = service.search(parsed, "food_places")
        body = json.loads(calls[0].content)
        self.assertIn("Indiranagar", body["textQuery"])
        self.assertIn("Bengaluru", body["textQuery"])
        self.assertEqual(body["pageSize"], 5)
        self.assertIn("locationRestriction", body)
        self.assertEqual(str(calls[0].url), PLACES_URL)
        self.assertEqual(calls[0].headers["X-Goog-FieldMask"], FIELD_MASK)
        self.assertNotIn("*", FIELD_MASK)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_hotel_budget_and_time_are_constraints_not_verified_prices(self):
        service, calls = self.service()
        parsed = intent("accommodation", "Hotel at Majestic under 1500 rupees at 4 AM", area="Majestic", time_constraint="4 AM",
                        budget={"amount": 1500, "currency": "INR", "raw_text": "under 1500 rupees"})
        data = compose(parsed, places=service)
        self.assertEqual(data["type"], "stay")
        self.assertIn("have not been confirmed", data["description"])
        self.assertIn("Requested budget", data["details"][0])
        for field in ("price", "availability", "immediate_check_in", "open_now", "accessibility", "image"):
            self.assertIsNone(data["places"][0][field])
        self.assertNotIn("1500", json.loads(calls[0].content)["textQuery"])

    def test_missing_key_is_safe_without_http(self):
        service, calls = self.service(key="")
        with self.assertRaises(CityServiceError) as caught:
            service.search(intent("accommodation", "Find a hotel"), "accommodation")
        self.assertEqual(caught.exception.code, "not_configured")
        self.assertEqual(calls, [])

    def test_empty_response_is_no_results_not_error(self):
        service, _ = self.service({})
        result = compose(intent("food_places", "Find food"), places=service)
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["places"], [])

    def test_quota_auth_server_connection_and_timeout(self):
        for kwargs, code in (({"status": 429}, "rate_limited"), ({"status": 403}, "authentication_error"),
                             ({"status": 500}, "provider_error"), ({"failure": httpx.ConnectError}, "connection_error"),
                             ({"failure": httpx.ReadTimeout}, "timeout")):
            service, calls = self.service(**kwargs)
            result = compose(intent("food_places", "Find food"), places=service)
            self.assertEqual(result["status"], code)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["places"], [])

    def test_stable_records_attribution_and_source_links(self):
        service, _ = self.service()
        result = service.search(intent("accommodation", "Find a hotel"), "accommodation")
        record = result["places"][0]
        self.assertEqual(record["id"], "fixture-hotel")
        self.assertEqual(record["name"], "Synthetic test listing")
        self.assertEqual(record["location"], {"latitude": 12.98, "longitude": 77.57})
        self.assertEqual(record["attributions"][0]["provider"], "Synthetic attribution")
        self.assertEqual(record["retrieved_at"], NOW.isoformat())
        self.assertIn("destination_place_id=fixture-hotel", record["directions_url"])

    def test_missing_coordinates_and_photo_stay_null(self):
        payload = place_payload()
        payload["places"][0].pop("location")
        service, _ = self.service(payload)
        record = service.search(intent("food_places", "Find food"), "food_places")["places"][0]
        self.assertIsNone(record["location"])
        self.assertIsNone(record["image"])

    def test_invalid_coordinates_and_outside_city_records_rejected(self):
        mutations = [lambda p: p.update(location={"latitude": True, "longitude": 77.57}),
                     lambda p: p.update(location={"latitude": 28.61, "longitude": 77.21}),
                     lambda p: p.update(formattedAddress="Delhi, India"), lambda p: p.update(displayName={})]
        for mutate in mutations:
            payload = place_payload()
            mutate(payload["places"][0])
            service, _ = self.service(payload)
            with self.assertRaises(CityServiceError):
                service.search(intent("food_places", "Find food"), "food_places")

    def test_unsafe_link_is_not_returned(self):
        payload = place_payload()
        payload["places"][0]["googleMapsUri"] = "javascript:alert(1)"
        service, _ = self.service(payload)
        self.assertIsNone(service.search(intent("food_places", "Food"), "food_places")["places"][0]["source_url"])

    def test_near_me_requires_authorised_area(self):
        service, calls = self.service()
        data = compose(intent("food_places", "Food near me"), places=service)
        self.assertEqual(data["status"], "missing_location")
        self.assertEqual(calls, [])

    def test_preferences_do_not_become_accessibility_claims(self):
        service, calls = self.service()
        result = compose(intent("places_services", "Find a hospital with wheelchair access", accessibility_constraints=["wheelchair access"]), places=service)
        self.assertIn("wheelchair access", json.loads(calls[0].content)["textQuery"])
        self.assertIsNone(result["places"][0]["accessibility"])
        self.assertIn("emergency capacity", result["description"])

    def test_tourism_reuses_places_adapter_with_estimated_itinerary(self):
        service, calls = self.service()
        weather = Mock(forecast=Mock(side_effect=CityServiceError('timeout')))
        del weather.clock
        result = compose(intent("tourism", "Plan a one-day visit in Bengaluru"), places=service, weather=weather)
        self.assertEqual(result["type"], "guide")
        self.assertIn("tourist attractions", json.loads(calls[0].content)["textQuery"])
        self.assertIn("not road-route or traffic optimisation", result["description"])
        self.assertTrue(result['sections'][0]['itinerary']['duration_estimates'])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        resolver = patch("city_services._PLACES.resolve", side_effect=CityServiceError("not_configured"))
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_old_intent_benchmark_and_replay_do_not_call_city_providers(self):
        from benchmark_intents import process_text as benchmark_process
        with patch("city_services._WEATHER.forecast") as weather, patch("city_services._PLACES.search") as places:
            for category in ("weather_conditions", "accommodation"):
                provider = Mock(extract=Mock(return_value=extraction(category)))
                result = benchmark_process(TextRequest("Find city information", "Bengaluru", "evaluation"), provider)
                self.assertEqual(result["understanding"]["primary_intent"], category)
            weather.assert_not_called()
            places.assert_not_called()

    def test_metro_missing_origin_preserves_destination_with_transit_url(self):
        result = compose(intent("transport", "How do I get to Majestic by metro?", destination="Majestic", transport_preference="metro"))
        section = result["sections"][0]
        params = parse_qs(urlsplit(section["directions_url"]).query)
        self.assertNotIn("origin", params)
        self.assertEqual(params["destination"], ["Majestic, Bengaluru"])
        self.assertEqual(params["travelmode"], ["transit"])
        self.assertEqual(result["understanding"]["missing_information"], ["origin"])
        self.assertIn("https://english.bmrc.co.in/", result["description"])
        self.assertIn("has not calculated", result["description"])

    def test_origin_destination_unicode_and_reserved_characters_roundtrip(self):
        url = directions_url("A&B / ಕನ್ನಡ", "C+D # gate", "transit")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params, {"api": ["1"], "destination": ["A&B / ಕನ್ನಡ"], "origin": ["C+D # gate"], "travelmode": ["transit"]})
        self.assertIsNone(directions_url(""))
        self.assertIsNone(directions_url("x" * 3000))
        self.assertNotIn("travelmode", parse_qs(urlsplit(directions_url("Majestic", mode="flying")).query))

    def test_missing_destination_requests_it_without_route(self):
        result = compose(intent("transport", "I need a bus"))
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["sections"], [])

    def test_both_origin_and_destination_bus_mode(self):
        result = compose(intent("transport", "Bus from Indiranagar to Majestic", origin="Indiranagar", destination="Majestic", transport_preference="Bus"))
        params = parse_qs(urlsplit(result["sections"][0]["directions_url"]).query)
        self.assertEqual(params["origin"], ["Indiranagar, Bengaluru"])
        self.assertEqual(params["travelmode"], ["transit"])
        self.assertNotIn("BMRCL", result["description"])

    def test_multi_intent_primary_first_and_bounded_results(self):
        calls = []
        def handle(request):
            body = json.loads(request.content)
            calls.append(body)
            return httpx.Response(200, json=place_payload("stay" if len(calls) == 1 else "food"))
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            service = PlacesService(client, api_key="test")
            result = compose(intent("accommodation", "I need a hotel and food near Majestic", area="Majestic", secondary_intents=["food_places", "tourism"]), places=service)
        self.assertEqual([s["intent"] for s in result["sections"]], ["accommodation", "food_places"])
        self.assertEqual([p["id"] for p in result["places"]], ["stay", "food"])
        self.assertEqual([call["pageSize"] for call in calls], [3, 3])
        self.assertEqual(result["deferred_intents"], ["tourism"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["actions"], [])

    def test_partial_provider_success_in_either_direction(self):
        for failed_call in (1, 2):
            count = [0]
            def handle(_):
                count[0] += 1
                return httpx.Response(429 if count[0] == failed_call else 200, json={} if count[0] == failed_call else place_payload())
            with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                result = compose(intent("accommodation", "Hotel and food", secondary_intents=["food_places"]), places=PlacesService(client, api_key="test"))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["places"]), 1)
            self.assertIn("rate limit", result["description"])

    def test_weather_works_without_google_key(self):
        client, calls = http_client(forecast_payload())
        self.addCleanup(client.close)
        result = compose(intent("weather_conditions", "Weather today"), weather=WeatherService(client, clock=lambda: NOW), places=PlacesService(api_key=""))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 1)

    def test_unsupported_city_and_conflicting_message_do_not_lookup(self):
        weather, places = Mock(), Mock()
        for parsed in (intent("weather_conditions", "Weather in Delhi", city="Delhi"),
                       intent("weather_conditions", "Weather in Delhi", city="Bengaluru", **{"secondary_intents": []})):
            # Actual conflicting extracted city is preserved in evidence even when
            # selected city takes precedence in the established internal field.
            if parsed["city"] == "Bengaluru":
                parsed["entities"].append({"type": "city", "value": "Delhi", "evidence": "Delhi", "source": "groq"})
            self.assertEqual(compose(parsed, weather=weather, places=places)["status"], "unsupported_city")
        weather.forecast.assert_not_called()
        places.search.assert_not_called()

    def test_unknown_whatsapp_city_stays_unknown(self):
        parsed = intent("accommodation", "Hotel near Majestic", city=None, area="Majestic")
        places = Mock()
        result = compose(parsed, places=places)
        self.assertEqual(result["status"], "clarification_required")
        self.assertIsNone(result["city"])
        places.search.assert_not_called()

    def test_deterministic_actions_never_call_any_provider(self):
        llm, weather, places = Mock(), Mock(), Mock()
        result = process_text(TextRequest("with you", "Bengaluru", "android", context={"report_id": "demo"}), llm, weather, places)
        self.assertEqual(result["status"], "action_not_implemented")
        self.assertEqual(result["actions"], [])
        llm.extract.assert_not_called()
        weather.forecast.assert_not_called()
        places.search.assert_not_called()

    def test_city_provider_concurrency_is_bounded_and_released(self):
        client, calls = http_client({})
        self.addCleanup(client.close)
        for _ in range(4):
            self.assertTrue(_SLOTS.acquire(False))
        try:
            with self.assertRaises(CityServiceError) as caught:
                fetch_json("GET", WEATHER_URL, client)
            self.assertEqual(caught.exception.code, "busy")
            self.assertEqual(calls, [])
        finally:
            for _ in range(4):
                _SLOTS.release()
        self.assertEqual(fetch_json("GET", WEATHER_URL, client), {})

    def test_android_envelope_real_adapter_and_no_second_llm_call(self):
        http, calls = http_client(forecast_payload())
        self.addCleanup(http.close)
        provider = Mock(extract=Mock(return_value=extraction("weather_conditions", time_constraint="this evening")))
        weather = WeatherService(http, clock=lambda: NOW)
        client = create_app(processor=lambda req: process_text(req, provider, weather, PlacesService(api_key=""))).test_client()
        response = client.post("/v1/chat", json={"message": "Will it rain in Bengaluru this evening?", "city": "Bengaluru"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
        provider.extract.assert_called_once()
        self.assertEqual(len(calls), 1)
        data = response.json["data"]
        self.assertEqual(data["type"], "weather")
        self.assertIn("request_id", data)
        places_http, _ = http_client(place_payload())
        self.addCleanup(places_http.close)
        place_result = compose(intent("accommodation", "Find a hotel"), places=PlacesService(places_http, api_key="test"))
        script = Path(__file__).parent / "test_frontend_contract.cjs"
        completed = subprocess.run(["node", str(script)], input=json.dumps([data, place_result]), capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("adapter contract passed", completed.stdout)

    def test_google_unconfigured_preserves_error_envelope(self):
        provider = Mock(extract=Mock(return_value=extraction("accommodation")))
        client = create_app(processor=lambda req: process_text(req, provider, places=PlacesService(api_key=""))).test_client()
        response = client.post("/v1/chat", json={"message": "Find a hotel", "city": "Bengaluru"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json["success"])
        self.assertEqual(response.json["data"]["places"], [])
        self.assertEqual(response.json["data"]["type"], "stay")


if __name__ == "__main__":
    unittest.main()
