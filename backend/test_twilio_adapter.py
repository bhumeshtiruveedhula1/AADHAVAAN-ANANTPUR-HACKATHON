import os
from threading import Event
import unittest
from unittest.mock import patch

from twilio.request_validator import RequestValidator

from app import create_app
from twilio_adapter import _connector


TOKEN = "local_test_auth_token"
URL = "https://example.trycloudflare.com/twilio/webhook"
SID = "SM" + "a" * 32
FORM = {"Body": "I need a room", "From": "whatsapp:+919876543210", "MessageSid": SID}


def signed_post(client, form=FORM, signature=None):
    signature = signature or RequestValidator(TOKEN).compute_signature(URL, form)
    return client.post("/twilio/webhook", data=form, headers={"X-Twilio-Signature": signature})


class TwilioAdapterTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "TWILIO_AUTH_TOKEN": TOKEN,
            "TWILIO_PUBLIC_WEBHOOK_URL": URL,
            "TWILIO_ACCOUNT_SID": "AC" + "a" * 32,
            "TWILIO_WHATSAPP_NUMBER": "whatsapp:+14155238886",
            "TWILIO_CONTENT_SID": "HX" + "b" * 32,
            "TWILIO_CONTENT_VARIABLES": "{}",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_android_and_verified_whatsapp_use_same_processor(self):
        seen, sent, completed, delivered = [], [], Event(), Event()

        def processor(incoming):
            seen.append(incoming)
            if incoming.channel == "whatsapp":
                completed.set()
            return {"type": "unavailable", "title": "Unavailable", "description": "Not configured", "details": [], "places": [], "status": "service_not_configured"}

        def sender(to, text):
            sent.append((to, text))
            delivered.set()
            return {"status": "queued"}

        app = create_app(processor=processor, twilio_sender=sender)
        client = app.test_client()
        android = client.post("/v1/chat", json={"message": "I need a room", "city": "Bengaluru"})
        whatsapp = signed_post(client)
        self.assertEqual(android.status_code, 503)
        self.assertEqual(whatsapp.status_code, 200)
        self.assertTrue(completed.wait(2))
        self.assertEqual([item.channel for item in seen], ["android", "whatsapp"])
        self.assertEqual(seen[1].message, "I need a room")
        self.assertIsNone(seen[1].city)
        self.assertEqual(seen[1].provider_message_id, SID)
        self.assertEqual(seen[1].sender_ref, FORM["From"])
        self.assertEqual(seen[1].session_ref, FORM["From"])
        self.assertEqual(android.json["error"]["code"], "service_not_configured")
        self.assertTrue(delivered.wait(2))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "Smart Nagrik received your message.")

    def test_invalid_and_missing_signatures_are_rejected(self):
        seen = []
        client = create_app(processor=lambda incoming: seen.append(incoming), twilio_sender=lambda *_: None).test_client()
        self.assertEqual(signed_post(client, signature="invalid").status_code, 403)
        self.assertEqual(client.post("/twilio/webhook", data=FORM).status_code, 403)
        self.assertEqual(seen, [])

    def test_duplicate_message_sid_is_processed_once(self):
        seen, completed = [], Event()

        def processor(incoming):
            seen.append(incoming)
            return {"status": "service_not_configured"}

        def sender(*_):
            completed.set()
            return {"status": "queued"}

        client = create_app(processor=processor, twilio_sender=sender).test_client()
        self.assertEqual(signed_post(client).status_code, 200)
        self.assertEqual(signed_post(client).status_code, 200)
        self.assertTrue(completed.wait(2))
        self.assertEqual(len(seen), 1)

    def test_missing_configuration_and_invalid_message_fail_safely(self):
        client = create_app(processor=lambda _: {}, twilio_sender=lambda *_: None).test_client()
        with patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": ""}):
            self.assertEqual(signed_post(client).status_code, 503)
        with patch.dict(os.environ, {"TWILIO_PUBLIC_WEBHOOK_URL": ""}):
            self.assertEqual(signed_post(client).status_code, 503)
        bad = {**FORM, "MessageSid": "missing"}
        self.assertEqual(signed_post(client, bad).status_code, 400)

    def test_missing_outbound_template_does_not_fall_back_to_freeform(self):
        processed, completed = Event(), Event()

        def processor(_):
            processed.set()
            return {"status": "service_not_configured"}

        def sender(*_):
            completed.set()

        client = create_app(processor=processor, twilio_sender=sender).test_client()
        with patch.dict(os.environ, {"TWILIO_CONTENT_SID": ""}):
            self.assertEqual(signed_post(client).status_code, 200)
            self.assertTrue(processed.wait(2))
            # The worker has no permitted outbound path in this configuration.
            self.assertFalse(completed.wait(0.1))

    def test_existing_sender_uses_content_sid_without_freeform_body(self):
        with patch.object(_connector, "Client") as client_class, patch.object(_connector.time, "sleep"):
            message = client_class.return_value.messages.create.return_value
            message.sid, message.status = "SM" + "c" * 32, "queued"
            updated = client_class.return_value.messages.return_value.fetch.return_value
            updated.sid, updated.status, updated.error_code = message.sid, "sent", None
            outcome = _connector._send_twilio_whatsapp_reply(FORM["From"], "Static acknowledgement")
            self.assertEqual(outcome["status"], "sent")
            kwargs = client_class.return_value.messages.create.call_args.kwargs
            self.assertEqual(kwargs["content_sid"], os.environ["TWILIO_CONTENT_SID"])
            self.assertNotIn("body", kwargs)
            self.assertEqual(kwargs["content_variables"], "{}")

    def test_original_connector_forwarded_url_validation_still_works(self):
        with _connector.app.test_request_context(
            "/twilio/webhook",
            method="POST",
            data=FORM,
            headers={
                "X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(URL, FORM),
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "example.trycloudflare.com",
            },
        ):
            self.assertTrue(_connector._verify_twilio_signature(TOKEN))

    def test_full_queue_returns_retryable_error_and_does_not_mark_sid_processed(self):
        entered, release, drained = Event(), Event(), Event()
        processed = []
        sent = []

        def processor(incoming):
            entered.set()
            release.wait(3)
            processed.append(incoming.provider_message_id)
            return {"status": "service_not_configured"}

        def sender(*_):
            sent.append(True)
            if len(sent) >= 65:
                drained.set()
            return {}

        def form(number):
            return {**FORM, "MessageSid": "SM" + format(number, "032x")}

        client = create_app(processor=processor, twilio_sender=sender).test_client()
        with patch("twilio_adapter._log"):
            try:
                self.assertEqual(signed_post(client, form(0)).status_code, 200)
                self.assertTrue(entered.wait(1))
                for index in range(1, 65):
                    self.assertEqual(signed_post(client, form(index)).status_code, 200)
                self.assertEqual(signed_post(client, form(65)).status_code, 503)
            finally:
                release.set()
            self.assertTrue(drained.wait(2))
            drained.clear()
            self.assertEqual(signed_post(client, form(65)).status_code, 200)
            self.assertTrue(drained.wait(2))
        self.assertEqual(len(processed), 66)

    def test_worker_recovers_from_exception_without_logging_private_payload(self):
        sent = Event()
        attempts = []

        def processor(incoming):
            attempts.append(incoming.provider_message_id)
            if len(attempts) == 1:
                raise RuntimeError("private-message-and-key-sentinel")
            return {"status": "service_not_configured"}

        client = create_app(processor=processor, twilio_sender=lambda *_: sent.set() or {}).test_client()
        with self.assertLogs("smart_nagrik.twilio", level="ERROR") as logs:
            self.assertEqual(signed_post(client).status_code, 200)
            second = {**FORM, "MessageSid": "SM" + "d" * 32}
            self.assertEqual(signed_post(client, second).status_code, 200)
            self.assertTrue(sent.wait(2))
        self.assertEqual(len(attempts), 2)
        self.assertNotIn("private-message-and-key-sentinel", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
