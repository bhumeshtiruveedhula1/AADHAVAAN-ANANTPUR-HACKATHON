"""Fixed real-world regressions using mocked extraction; live scores are separate."""

import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_intents import DATASET, score, summarize
from groq_provider import MODELS, SYSTEM_PROMPT
from intents import understand
from service import TextRequest, process_text
from test_intents import provider_for
from city_http import CityServiceError


def entity(kind, value, evidence=None):
    return {"type": kind, "value": value, "evidence": evidence or value}


def compact(primary, entities, secondary=None):
    return {"primary_intent": primary, "secondary_intents": secondary or [], "entities": entities}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        weather = patch("city_services._WEATHER.forecast", side_effect=CityServiceError("timeout"))
        weather.start()
        self.addCleanup(weather.stop)

    def test_five_core_regressions_and_python_clarification(self):
        cases = json.loads(DATASET.read_text(encoding="utf-8"))[:5]
        payloads = [
            compact("accommodation", [entity("area", "Majestic"), entity("budget", "1500", "under 1500 rupees"),
                    entity("currency", "INR", "1500 rupees"), entity("time", "4 AM")], ["food_places"]),
            compact("transport", [entity("destination", "Majestic"), entity("transport_preference", "metro")]),
            compact("civic_report", [entity("civic_category", "pothole"), entity("exact_location", "outside my college")]),
            compact("weather_conditions", [entity("city", "Bengaluru"), entity("time", "this evening")]),
            compact("tourism", [entity("city", "Bengaluru"), entity("duration", "one-day")]),
        ]
        for case, payload in zip(cases, payloads):
            with self.subTest(case=case["id"]):
                provider, calls = provider_for(payload)
                with patch.dict("os.environ", {"GROQ_API_KEY": "test-key", "GROQ_MODEL": MODELS[0]}):
                    result = process_text(TextRequest(case["message"], case["city"], "android"), provider)
                self.assertTrue(score(case, result)["passed"])
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["places"], [])

    def test_normalized_budget_and_clock_do_not_discard_other_entities(self):
        payload = compact("accommodation", [entity("area", "Majestic"),
            entity("budget", "1500 INR", "under 1500 rupees"), entity("time", "04:00", "4 AM")], ["food_places"])
        provider, _ = provider_for(payload)
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = understand("I reached Majestic at 4 AM. I need a hotel under 1500 rupees and something to eat.", "Bengaluru", provider=provider)
        self.assertEqual(result["budget"]["amount"], 1500)
        self.assertEqual(result["budget"]["currency"], "INR")
        self.assertEqual(result["budget"]["constraint"], "maximum")
        self.assertEqual(result["time_constraint"], "4 AM")
        self.assertEqual(result["secondary_intents"], ["food_places"])

    def test_malformed_optional_entity_is_reported_and_other_entities_survive(self):
        payload = compact("transport", [entity("destination", "Majestic"), {"type": "invented_type", "value": "x", "evidence": "x"}])
        provider, _ = provider_for(payload)
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = understand("How do I get to Majestic?", "Bengaluru", provider=provider)
        self.assertEqual(result["destination"], "Majestic")
        self.assertEqual(result["missing_information"], ["origin"])
        self.assertTrue(result["validation_warnings"])

    def test_invalid_schema_diagnostics_contain_paths_not_private_values(self):
        payload = compact("invented_primary", [])
        provider, _ = provider_for(payload)
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = process_text(TextRequest("private-text-sentinel", "Bengaluru", "android"), provider)
        self.assertEqual(result["interpretation_status"], "invalid_schema")
        self.assertEqual(result["validation_diagnostics"][0]["path"], "primary_intent")
        self.assertNotIn("private-text-sentinel", json.dumps(result))

    def test_dataset_is_held_out_from_instructions_and_covers_25_cases(self):
        cases = json.loads(DATASET.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 25)
        self.assertEqual(len({case["message"] for case in cases}), 25)
        self.assertEqual(sum(case.get("core", False) for case in cases), 5)
        self.assertTrue(all(case["message"] not in SYSTEM_PROMPT for case in cases))

    def test_both_supported_models_use_the_same_contract(self):
        for model in MODELS:
            provider, calls = provider_for(compact("accommodation", []))
            provider.model = model
            with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
                understand("Find a room", "Bengaluru", provider=provider)
            self.assertEqual(calls[0]["model"], model)
            self.assertFalse(calls[0]["include_reasoning"])
            self.assertTrue(calls[0]["response_format"]["json_schema"]["strict"])

    def test_empty_benchmark_has_no_latency_or_accuracy_claim(self):
        summary = summarize([])
        self.assertTrue(all(not row["quality_gate_passed"] and row["median_seconds"] is None for row in summary.values()))

    def test_quoted_live_evidence_is_normalized_without_losing_destination(self):
        provider, _ = provider_for(compact("transport", [entity("destination", "Majestic", '"How do I get to Majestic by metro?"'),
                                                       entity("transport_preference", "metro", '"How do I get to Majestic by metro?"')]))
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = understand("How do I get to Majestic by metro?", "Bengaluru", provider=provider)
        self.assertEqual(result["destination"], "Majestic")
        self.assertEqual(result["transport_preference"], "metro")
        self.assertEqual(result["missing_information"], ["origin"])

    def test_literal_city_is_grounded_while_landmark_alone_does_not_supply_city(self):
        provider, _ = provider_for(compact("weather_conditions", [entity("time", "this evening")]))
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = understand("Will it rain in Bengaluru this evening?", provider=provider)
        self.assertEqual(result["city"], "Bengaluru")
        self.assertEqual(result["missing_information"], [])
        provider, _ = provider_for(compact("accommodation", [entity("area", "Majestic")]))
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            result = understand("Find a hotel near Majestic", provider=provider)
        self.assertIsNone(result["city"])
        self.assertEqual(result["missing_information"], ["city"])

    def test_literal_duration_normalization_generalizes_without_changing_intent(self):
        for phrase in ("one-day", "six hours", "3 weeks", "half an hour"):
            provider, _ = provider_for(compact("tourism", []))
            with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
                result = understand(f"I have {phrase} in Bengaluru for sightseeing.", provider=provider)
            # The simple grammar preserves its matched text, not a fabricated duration.
            self.assertEqual(result["duration"], phrase)
            self.assertEqual(result["primary_intent"], "tourism")

    def test_literal_time_phrases_are_preserved_with_normalizer_provenance(self):
        for phrase in ("this evening", "tomorrow morning", "right now", "10:30 PM"):
            provider, _ = provider_for(compact("weather_conditions", []))
            with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
                result = understand(f"Weather in Bengaluru {phrase}?", provider=provider)
            self.assertEqual(result["time_constraint"], phrase)
            self.assertTrue(any(item["source"] == "literal_normalizer" and item["value"] == phrase for item in result["entities"]))

    def test_benchmark_resume_never_repeats_reserved_or_failed_calls(self):
        from benchmark_intents import run
        from groq_provider import ProviderError
        calls = []

        class FakeProvider:
            def __init__(self, model):
                self.model = model

            def extract(self, message, *_):
                calls.append((message, self.model))
                if len(calls) == 1:
                    raise ProviderError("connection_error")
                return compact("unsupported", [])

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), \
             patch("benchmark_intents.GroqIntentProvider", FakeProvider), contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "results.json"
            run(output, interval_seconds=0)
            self.assertEqual(len(calls), 1)
            run(output, interval_seconds=0, resume=True)
            self.assertEqual(len(calls), 50)
            self.assertEqual(len(set(calls)), 50)
            run(output, interval_seconds=0, resume=True)
            self.assertEqual(len(calls), 50)
            report = json.loads(output.read_text(encoding="utf-8"))
            report["stop_reason"] = "rate_limited"
            output.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(SystemExit):
                run(output, interval_seconds=0, resume=True)
            self.assertEqual(len(calls), 50)
