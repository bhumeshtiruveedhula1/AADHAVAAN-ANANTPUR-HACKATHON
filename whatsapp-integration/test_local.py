"""
Local test script for the WhatsApp Handshake Server.

Runs automated checks against http://localhost:8000 WITHOUT needing
real WhatsApp or Twilio credentials.  Start the server first, then run this.

What it tests:
  1. Health endpoint (GET /)
  2. Webhook verification (GET /webhook - correct token)
  3. Webhook verification (GET /webhook - wrong token)
  4. Meta webhook POST with a simulated signed WhatsApp message
  5. Meta webhook POST with a bad signature
  6. Twilio signature validation -- valid signature behind Cloudflare Tunnel (200)
  7. Twilio signature validation -- bad signature rejected (403)
  8. Twilio signature validation -- missing signature rejected / fails closed (403)
  9. Twilio privacy masking check (personal phone numbers masked in logs)

These are LOCAL-ONLY simulations.  They do NOT prove a real WhatsApp
connection -- that requires the full Meta/Twilio setup (see README).
"""

import hashlib
import hmac
import json
import os
import sys

import requests
from twilio.request_validator import RequestValidator

# Import local Flask app for in-memory signature validation tests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import app, _mask_phone

BASE = "http://localhost:8000"

# These must match the values in your .env file
VERIFY_TOKEN = "my_secret_verify_token_123"
APP_SECRET = "test_app_secret_for_local"

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def test_health():
    """GET / should return 200."""
    print("\n-- Test 1: Health endpoint (GET /) --")
    try:
        r = requests.get(f"{BASE}/", timeout=5)
        ok = r.status_code == 200
        print(f"   Status: {r.status_code}  =>  {PASS if ok else FAIL}")
        results.append(ok)
    except requests.ConnectionError:
        print(f"   Could not connect to {BASE} -- is the server running?")
        results.append(False)


def test_webhook_verify_good():
    """GET /webhook with correct token should return the challenge."""
    print("\n-- Test 2: Webhook verification -- correct token --")
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "test_challenge_string_12345",
    }
    r = requests.get(f"{BASE}/webhook", params=params, timeout=5)
    ok = r.status_code == 200 and r.text == "test_challenge_string_12345"
    print(f"   Status: {r.status_code}  Body: {r.text!r}  =>  {PASS if ok else FAIL}")
    results.append(ok)


def test_webhook_verify_bad():
    """GET /webhook with wrong token should return 403."""
    print("\n-- Test 3: Webhook verification -- wrong token --")
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong_token",
        "hub.challenge": "test_challenge",
    }
    r = requests.get(f"{BASE}/webhook", params=params, timeout=5)
    ok = r.status_code == 403
    print(f"   Status: {r.status_code}  =>  {PASS if ok else FAIL}")
    results.append(ok)


def test_webhook_post_signed():
    """POST /webhook with a valid HMAC signature should return 200."""
    print("\n-- Test 4: Signed Meta webhook POST (simulated WhatsApp message) --")

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": "000000000000",
                            },
                            "messages": [
                                {
                                    "from": "919999999999",
                                    "id": "wamid.simulated_local_test_001",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "Hi"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    }

    r = requests.post(
        f"{BASE}/webhook",
        data=body_bytes,
        headers=headers,
        timeout=5,
    )
    ok = r.status_code == 200
    print(f"   Status: {r.status_code}  =>  {PASS if ok else FAIL}")
    if ok:
        print("   (Check server log -- 'WhatsApp message received: Hi')")
    results.append(ok)


def test_webhook_post_bad_signature():
    """POST /webhook with an invalid signature should return 403."""
    print("\n-- Test 5: Meta webhook POST with bad signature --")

    payload = {"object": "whatsapp_business_account", "entry": []}
    body_bytes = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
    }

    r = requests.post(
        f"{BASE}/webhook",
        data=body_bytes,
        headers=headers,
        timeout=5,
    )
    ok = r.status_code == 403
    print(f"   Status: {r.status_code}  =>  {PASS if ok else FAIL}")
    results.append(ok)


def test_twilio_signature_valid():
    """Test valid Twilio signature behind Cloudflare Tunnel (HTTPS header reconstruction)."""
    print("\n-- Test 6: Twilio signature validation -- valid signature (Cloudflare Tunnel) --")
    test_token = "test_auth_token_for_validation_123"
    app.config["TWILIO_AUTH_TOKEN"] = test_token

    # Public Cloudflare HTTPS URL that Twilio actually requests
    public_url = "https://my-tunnel.trycloudflare.com/twilio/webhook"
    form_data = {
        "Body": "Hi",
        "From": "whatsapp:+919999999999",
        "To": "whatsapp:+14155238886",
    }

    # Compute signature with Twilio's official RequestValidator
    validator = RequestValidator(test_token)
    expected_sig = validator.compute_signature(public_url, form_data)

    with app.test_client() as client:
        response = client.post(
            "/twilio/webhook",
            data=form_data,
            headers={
                "X-Twilio-Signature": expected_sig,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "my-tunnel.trycloudflare.com",
            },
        )
        ok = response.status_code == 200 and b"<Response></Response>" in response.data
        print(f"   Status: {response.status_code}  =>  {PASS if ok else FAIL}")
        if ok:
            print("   Reconstructed HTTPS URL signature matched and returned 200!")
        results.append(ok)


def test_twilio_signature_invalid():
    """Test invalid Twilio signature is rejected with 403."""
    print("\n-- Test 7: Twilio signature validation -- bad signature (rejected) --")
    test_token = "test_auth_token_for_validation_123"
    app.config["TWILIO_AUTH_TOKEN"] = test_token

    form_data = {
        "Body": "Hi",
        "From": "whatsapp:+919999999999",
    }

    with app.test_client() as client:
        response = client.post(
            "/twilio/webhook",
            data=form_data,
            headers={
                "X-Twilio-Signature": "invalid_tampered_signature_value",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "my-tunnel.trycloudflare.com",
            },
        )
        ok = response.status_code == 403
        print(f"   Status: {response.status_code}  =>  {PASS if ok else FAIL}")
        results.append(ok)


def test_twilio_signature_missing_fails_closed():
    """Test missing Twilio signature header fails closed with 403."""
    print("\n-- Test 8: Twilio signature validation -- missing header fails closed (403) --")
    test_token = "test_auth_token_for_validation_123"
    app.config["TWILIO_AUTH_TOKEN"] = test_token

    form_data = {
        "Body": "Hi",
        "From": "whatsapp:+919999999999",
    }

    with app.test_client() as client:
        # Request sent without X-Twilio-Signature header
        response = client.post(
            "/twilio/webhook",
            data=form_data,
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "my-tunnel.trycloudflare.com",
            },
        )
        ok = response.status_code == 403
        print(f"   Status: {response.status_code}  =>  {PASS if ok else FAIL}")
        results.append(ok)

    # Clean up test config
    app.config.pop("TWILIO_AUTH_TOKEN", None)


def test_phone_masking():
    """Test personal phone numbers are safely masked for logging."""
    print("\n-- Test 9: Phone number privacy masking --")
    raw_phone = "whatsapp:+919876543210"
    masked = _mask_phone(raw_phone)
    ok = (
        masked.startswith("whatsapp:+91")
        and masked.endswith("3210")
        and "987654" not in masked
        and "****" in masked
    )
    print(f"   Masked '{raw_phone}' => '{masked}'  =>  {PASS if ok else FAIL}")
    results.append(ok)


# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print(" WhatsApp Handshake -- LOCAL Simulation Tests")
    print(" (This does NOT test a real WhatsApp connection)")
    print("=" * 55)

    try:
        test_health()
        test_webhook_verify_good()
        test_webhook_verify_bad()
        test_webhook_post_signed()
        test_webhook_post_bad_signature()
        test_twilio_signature_valid()
        test_twilio_signature_invalid()
        test_twilio_signature_missing_fails_closed()
        test_phone_masking()
    except requests.ConnectionError:
        print(f"\n[ERROR] Cannot reach {BASE}. Start the server first:")
        print("   python app.py")
        sys.exit(1)

    print("\n" + "=" * 55)
    passed = sum(results)
    total = len(results)
    print(f" Results: {passed}/{total} tests passed")
    if passed == total:
        print(" All local tests passed!")
        print(" Next step: Configure Twilio credentials in .env")
        print("            and send a test WhatsApp message.")
    else:
        print(" Some tests failed -- check the output above.")
    print("=" * 55)

    sys.exit(0 if passed == total else 1)
