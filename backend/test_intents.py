"""Mocked Groq extraction tests; these are not live model accuracy tests."""

import json
import os
from threading import Event
import unittest
from unittest.mock import Mock, patch

import httpx
from groq import Groq
from twilio.request_validator import RequestValidator

from app import create_app
from groq_provider import GroqIntentProvider, MODEL
from intents import Extraction, understand
from service import TextRequest, process_text
from city_http import CityServiceError


def extraction(category="accommodation", **fields):
    # Existing regression fixtures expressed through the new provider contract.
    payload = {"primary_intent": category, "secondary_intents": fields.pop("secondary_intents", []), "entities": []}
    fields.pop("language", None)
    fields.pop("missing_information", None)  # Python owns clarification now.
    for name, value in fields.items():
        if value is None:
            continue
        if name == "budget":
            payload["entities"].append({"type": "budget", "value": str(value["amount"]), "evidence": value["raw_text"]})
            if value["currency"]:
                payload["entities"].append({"type": "currency", "value": value["currency"], "evidence": value["raw_text"]})
        elif name in {"preferences", "accessibility_constraints"}:
            payload["entities"].extend({"type": "preference" if name == "preferences" else "accessibility", "value": item, "evidence": item} for item in value)
        elif name in {"action"}:
            payload[name] = value  # Unauthorised model fields must still be rejected.
        else:
            payload["entities"].append({"type": "time" if name == "time_constraint" else name, "value": value, "evidence": value})
    return payload


def provider_for(payload=None, status=200, content=None, finish="stop", failure=None):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if failure:
            raise failure("simulated failure", request=request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "simulated provider error"}})
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1,
            "model": MODEL, "choices": [{"index": 0, "finish_reason": finish,
                "message": {"role": "assistant", "content": content if content is not None else json.dumps(payload)}}],
        })

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"].read <= 8
        return Groq(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    return GroqIntentProvider(client_factory=factory), calls


class IntentTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"GROQ_API_KEY": "test-key-not-real", "GROQ_MODEL": MODEL, "GROQ_TIMEOUT_SECONDS": "6", "GOOGLE_PLACES_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        weather = patch("city_services._WEATHER.forecast", side_effect=CityServiceError("timeout"))
        weather.start()
        self.addCleanup(weather.stop)

    def parse(self, message, payload, city="Bengaluru", context=None):
        provider, calls = provider_for(payload)
        result = understand(message, city, context, provider=provider)
        self.assertEqual(len(calls), 1)
        return result

    def test_clear_categories_route_only_to_known_future_services(self):
        cases = [
            ("Find a room", "accommodation", "accommodation_service"),
            ("Find a restaurant", "food_places", "places_service"),
            ("Find a pharmacy", "places_services", "places_service"),
            ("How do I take the bus?", "transport", "transport_service"),
            ("Will it rain in Bengaluru this evening?", "weather_conditions", "weather_service"),
            ("There is a pothole outside my college", "civic_report", "report_service"),
            ("Show road problems near Indiranagar", "civic_lookup", "civic_records_service"),
            ("Plan a sightseeing day", "tourism", "itinerary_service"),
            ("I need emergency contact information", "emergency", "emergency_information_service"),
            ("What should a newcomer know?", "city_guidance", "city_guidance_service"),
            ("Write unrelated software", "unsupported", None),
        ]
        for message, category, handler in cases:
            with self.subTest(category=category):
                result = self.parse(message, extraction(category))
                self.assertEqual(result["primary_intent"], category)
                self.assertEqual(result["suggested_service_handler"], handler)
                self.assertIsNone(result["action"])

    def test_accommodation_budget_time_and_secondary_food(self):
        text = "I'm at Majestic. It's 4 a.m. I need a room under ₹1,500 and something to eat."
        result = self.parse(text, extraction(
            secondary_intents=["food_places"], area="Majestic", time_constraint="4 a.m.",
            budget={"amount": 1500, "currency": "INR", "raw_text": "under ₹1,500"},
        ), city=None)
        self.assertEqual(result["secondary_intents"], ["food_places"])
        self.assertEqual(result["budget"]["amount"], 1500)
        self.assertEqual(result["time_constraint"], "4 a.m.")
        self.assertIsNone(result["city"])
        self.assertEqual(result["missing_information"], ["city"])

    def test_metro_requires_origin_without_inventing_it(self):
        result = self.parse("How do I get to Majestic by metro?", extraction("transport", destination="Majestic"))
        self.assertEqual(result["destination"], "Majestic")
        self.assertIsNone(result["origin"])
        self.assertEqual(result["missing_information"], ["origin"])

    def test_weather_preserves_city_and_relative_time_without_forecast(self):
        provider, _ = provider_for(extraction("weather_conditions", city="Bengaluru", time_constraint="this evening"))
        result = process_text(TextRequest("Will it rain in Bengaluru this evening?", "Bengaluru", "android"), provider)
        self.assertEqual(result["understanding"]["time_constraint"], "this evening")
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["places"], [])
        self.assertIn("timed out", result["description"])

    def test_civic_report_requires_precise_location(self):
        result = self.parse("There's a pothole outside my college", extraction("civic_report", civic_category="pothole", exact_location="outside my college"))
        self.assertIsNone(result["exact_location"])
        self.assertIn("exact_location", result["missing_information"])

    def test_civic_lookup_keeps_supplied_area(self):
        result = self.parse("Show me road problems near Indiranagar", extraction("civic_lookup", area="Indiranagar"))
        self.assertEqual(result["area"], "Indiranagar")
        self.assertFalse(result["clarification_needed"])

    def test_preferences_accessibility_and_unknown_currency_remain_explicit(self):
        text = "Find a room under 1500 with wheelchair access and vegetarian breakfast"
        result = self.parse(text, extraction(
            budget={"amount": 1500, "currency": None, "raw_text": "under 1500"},
            preferences=["vegetarian breakfast"], accessibility_constraints=["wheelchair access"],
        ))
        self.assertIsNone(result["budget"]["currency"])
        self.assertEqual(result["preferences"], ["vegetarian breakfast"])
        self.assertEqual(result["accessibility_constraints"], ["wheelchair access"])

    def test_ambiguous_request_needs_clarification(self):
        result = self.parse("Help me with that", extraction("unsupported", missing_information=["request_goal"]))
        self.assertTrue(result["clarification_needed"])
        self.assertIsNone(result["suggested_service_handler"])

    def test_followup_uses_only_explicit_recent_context(self):
        payload = extraction(area="Majestic", budget={"amount": 1500, "currency": "INR", "raw_text": "under ₹1,500"})
        result = self.parse("What about near Majestic?", payload, context={"recent_messages": ["Find accommodation under ₹1,500"]})
        self.assertEqual(result["primary_intent"], "accommodation")
        self.assertEqual(result["budget"]["amount"], 1500)
        provider, _ = provider_for(payload)
        result = process_text(TextRequest("What about near Majestic?", "Bengaluru", "android"), provider)
        self.assertEqual(result["status"], "service_not_configured")
        self.assertIsNone(result["understanding"]["budget"])
        self.assertTrue(result["understanding"]["validation_warnings"])

    def test_explicit_action_bypasses_groq_and_does_not_execute(self):
        provider = Mock()
        result = process_text(TextRequest("Open it", "Bengaluru", "android", action="open_directions",
                              context={"origin": "Indiranagar", "destination": "Majestic"}), provider)
        provider.extract.assert_not_called()
        self.assertEqual(result["status"], "action_not_implemented")
        self.assertEqual(result["understanding"]["method"], "rules")
        self.assertIn("no change", result["description"])
        for phrase in ("report issue", "with you", "view report status", "open directions", "select city"):
            with self.subTest(action=phrase):
                self.assertEqual(understand(phrase, "Bengaluru", provider=provider)["method"], "rules")
        provider.extract.assert_not_called()

    def test_schema_and_sdk_parameters_are_strict(self):
        provider, calls = provider_for(extraction())
        process_text(TextRequest("Find a room", "Bengaluru", "android"), provider)
        call = calls[0]
        self.assertEqual((call["model"], call["reasoning_effort"], call["temperature"]), (MODEL, "low", 0))
        self.assertTrue(call["response_format"]["json_schema"]["strict"])

        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["properties"]), set(node["required"]))
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)

        check(Extraction.model_json_schema())

    def test_invalid_categories_actions_and_ungrounded_values_are_rejected(self):
        cases = [extraction("buy_crypto"), extraction(action="delete_report")]
        for payload in cases:
            with self.subTest(payload=payload):
                provider, _ = provider_for(payload)
                self.assertEqual(process_text(TextRequest("Find a room", "Bengaluru", "android"), provider)["status"], "intent_invalid_output")

    def test_bad_optional_entities_do_not_discard_valid_intent(self):
        cases = [extraction(civic_category="made_up"), extraction(city="Mumbai"),
                 extraction(budget={"amount": 100, "currency": "INR", "raw_text": "Find a room"}),
                 extraction(secondary_intents=["accommodation"])]
        for payload in cases:
            with self.subTest(payload=payload):
                provider, _ = provider_for(payload)
                result = process_text(TextRequest("Find a room", "Bengaluru", "android"), provider)
                self.assertEqual(result["status"], "service_not_configured")
                self.assertEqual(result["understanding"]["primary_intent"], "accommodation")
                self.assertTrue(result["understanding"]["validation_warnings"])

    def test_missing_key_does_not_make_network_request(self):
        factory = Mock()
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            result = process_text(TextRequest("Find a room", "Bengaluru", "android"), GroqIntentProvider(factory))
        factory.assert_not_called()
        self.assertEqual(result["status"], "service_not_configured")
        self.assertEqual(result["interpretation_status"], "not_configured")

    def test_malformed_incomplete_and_refused_provider_outputs(self):
        cases = [(None, "not json", "stop", "invalid_output"),
                 ({"primary_intent": "accommodation"}, None, "stop", "invalid_schema"),
                 (extraction(), None, "length", "invalid_output"),
                 (None, "", "content_filter", "refused")]
        for payload, content, finish, expected in cases:
            with self.subTest(expected=expected, finish=finish):
                provider, _ = provider_for(payload, content=content, finish=finish)
                result = process_text(TextRequest("Find a room", "Bengaluru", "android"), provider)
                self.assertEqual(result["interpretation_status"], expected)

    def test_provider_failures_are_safe_and_never_retried(self):
        cases = [(401, None, "authentication_error"), (403, None, "authentication_error"),
                 (429, None, "rate_limited"), (500, None, "provider_error"),
                 (200, httpx.ReadTimeout, "timeout"), (200, httpx.ConnectError, "connection_error")]
        for status, failure, expected in cases:
            with self.subTest(expected=expected):
                provider, calls = provider_for(extraction(), status=status, failure=failure)
                result = process_text(TextRequest("Find a room", "Bengaluru", "android"), provider)
                self.assertEqual(result["status"], "intent_" + expected)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["places"], [])
                self.assertNotIn("test-key", json.dumps(result))

    def test_wrong_model_or_unbounded_timeout_is_rejected(self):
        for setting in ({"GROQ_MODEL": "another-model"}, {"GROQ_TIMEOUT_SECONDS": "999"}):
            with patch.dict(os.environ, setting):
                result = process_text(TextRequest("Find a room", "Bengaluru", "android"))
                self.assertEqual(result["status"], "intent_configuration_error")

    def test_unicode_input_is_preserved_without_claiming_language_accuracy(self):
        for text in ("मुझे कमरा चाहिए", "ನನಗೆ ಕೊಠಡಿ ಬೇಕು"):
            result = self.parse(text, extraction(language=None))
            self.assertEqual(result["original_text"], text)
            self.assertIsNone(result["language"])

    def test_android_contract_and_context_validation(self):
        provider, _ = provider_for(extraction())
        client = create_app(processor=lambda incoming: process_text(incoming, provider)).test_client()
        response = client.post("/v1/chat", json={"message": "Find a room", "city": "Bengaluru"})
        self.assertEqual(response.status_code, 503)
        self.assertTrue({"type", "title", "description", "details", "places", "understanding", "request_id"} <= response.json["data"].keys())
        for extra in ({"action": "delete_report"}, {"context": []}, {"context": {"recent_messages": ["x"] * 4}}):
            self.assertEqual(client.post("/v1/chat", json={"message": "hello", "city": "Bengaluru", **extra}).status_code, 422)

    def test_android_timeout_and_rate_limit_preserve_error_envelope(self):
        for provider_status, failure, expected_status, code in (
            (200, httpx.ReadTimeout, 504, "intent_timeout"),
            (429, None, 503, "intent_rate_limited"),
        ):
            with self.subTest(code=code):
                provider, _ = provider_for(status=provider_status, failure=failure)
                client = create_app(processor=lambda incoming: process_text(incoming, provider)).test_client()
                response = client.post("/v1/chat", json={"message": "Find a room", "city": "Bengaluru"})
                self.assertEqual(response.status_code, expected_status)
                self.assertFalse(response.json["success"])
                self.assertEqual(response.json["error"]["code"], code)
                self.assertEqual(response.json["data"]["type"], "unavailable")

    def test_whatsapp_uses_real_shared_service_outside_webhook_request(self):
        entered, release, sent = Event(), Event(), Event()
        provider, calls = provider_for(extraction())

        def processor(incoming):
            entered.set()
            release.wait(2)
            result = process_text(incoming, provider)
            self.assertIsNone(result["understanding"]["city"])
            self.assertIn("city", result["understanding"]["missing_information"])
            return result

        url, token = "https://example.test/twilio/webhook", "test-token"
        environment = {"TWILIO_AUTH_TOKEN": token, "TWILIO_PUBLIC_WEBHOOK_URL": url,
                       "TWILIO_ACCOUNT_SID": "test-sid", "TWILIO_WHATSAPP_NUMBER": "whatsapp:+14155238886",
                       "TWILIO_CONTENT_SID": "test-content", "TWILIO_CONTENT_VARIABLES": "{}"}
        with patch.dict(os.environ, environment):
            client = create_app(processor=processor, twilio_sender=lambda *_: sent.set() or {}).test_client()
            form = {"Body": "Find a room", "From": "whatsapp:+919876543210", "MessageSid": "SM" + "c" * 32}
            try:
                response = client.post("/twilio/webhook", data=form, headers={"X-Twilio-Signature": RequestValidator(token).compute_signature(url, form)})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(entered.wait(1))
                self.assertEqual(calls, [])  # The worker is still blocked; acknowledgement completed.
            finally:
                release.set()
            self.assertTrue(sent.wait(2))
        self.assertEqual(len(calls), 1)

    def test_provider_concurrency_is_bounded(self):
        from groq_provider import _SLOTS
        self.assertTrue(_SLOTS.acquire(False))
        self.assertTrue(_SLOTS.acquire(False))
        try:
            factory = Mock()
            result = process_text(TextRequest("Find a room", "Bengaluru", "android"), GroqIntentProvider(factory))
            self.assertEqual(result["status"], "intent_busy")
            factory.assert_not_called()
        finally:
            _SLOTS.release()
            _SLOTS.release()


if __name__ == "__main__":
    unittest.main()
