import unittest
import os
from unittest.mock import patch

from app import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"GROQ_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = create_app().test_client()

    def test_server_and_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "data": {"status": "ok"}, "error": None})

    def test_only_bengaluru_is_supported(self):
        self.assertEqual(self.client.get("/v1/cities").json["data"]["cities"], [{"name": "Bengaluru", "supported": True}])

    def test_chat_rejects_bad_input(self):
        for payload in ({}, {"message": " ", "city": "Bengaluru"}, {"message": "hello", "city": "Delhi"}):
            with self.subTest(payload=payload):
                response = self.client.post("/v1/chat", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertFalse(response.json["success"])
        self.assertEqual(self.client.post("/v1/chat", data="hi").status_code, 415)

    def test_unconfigured_service_has_result_shape_without_fake_places(self):
        response = self.client.post("/v1/chat", json={"message": "I need a room near Indiranagar", "city": "Bengaluru"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"]["code"], "service_not_configured")
        result = response.json["data"]
        self.assertTrue({"type", "title", "description", "details", "places", "request_id"} <= result.keys())
        self.assertEqual(result["places"], [])
        self.assertEqual(result["type"], "unavailable")

    def test_channel_neutral_processor_can_supply_a_result(self):
        seen = []

        def processor(text_request):
            seen.append(text_request)
            return {"type": "guide", "title": "Test answer", "description": "Mock provider", "details": [], "places": []}

        client = create_app(processor=processor).test_client()
        response = client.post("/v1/chat", json={"message": "  hello  ", "city": "Bengaluru"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["title"], "Test answer")
        self.assertEqual((seen[0].message, seen[0].city, seen[0].channel), ("hello", "Bengaluru", "android"))


if __name__ == "__main__":
    unittest.main()
