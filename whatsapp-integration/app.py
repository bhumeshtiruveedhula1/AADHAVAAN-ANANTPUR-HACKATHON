"""
WhatsApp Local Handshake Test
─────────────────────────────
A tiny Flask server that proves your laptop can talk to WhatsApp
through the official Meta Cloud API and/or the Twilio WhatsApp Sandbox.

Endpoints
  GET  /              → health check ("server is running")
  GET  /webhook       → Meta webhook verification challenge
  POST /webhook       → receive WhatsApp messages via Meta Cloud API
  POST /twilio/webhook → receive WhatsApp messages via Twilio Sandbox
"""

import hashlib
import hmac
import json
import os
import logging

from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
import requests as http_requests  # renamed to avoid clash with flask.request
import time
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
load_dotenv()  # reads .env file in the same folder

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

# Build the send-message URL once
MESSAGES_URL = (
    f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    f"/{WHATSAPP_PHONE_NUMBER_ID}/messages"
)

# Keep track of processed message IDs to ignore duplicates
processed_message_ids: set[str] = set()

# ──────────────────────────────────────────────
# Logging — show info in the terminal, never print secrets
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whatsapp-handshake")

# ──────────────────────────────────────────────
# Flask app
# ──────────────────────────────────────────────
app = Flask(__name__)


# ───── 1. Health check ────────────────────────
@app.route("/", methods=["GET"])
def health():
    """Simple page to confirm the server is alive."""
    return (
        "<h2>✅ WhatsApp Handshake Server is Running</h2>"
        "<p>Meta webhook: <code>/webhook</code></p>"
        "<p>Twilio webhook: <code>/twilio/webhook</code></p>"
    ), 200


# ───── 2. Webhook verification (GET) ─────────
@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """
    Meta sends a GET request to verify you own this URL.
    It includes three query parameters:
      hub.mode, hub.verify_token, hub.challenge
    We check the token and echo back the challenge.
    """
    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("✅ Webhook verification succeeded!")
        return challenge, 200
    else:
        log.warning("❌ Webhook verification failed — token mismatch")
        return "Forbidden", 403


# ───── 3. Receive messages (POST) ─────────────
@app.route("/webhook", methods=["POST"])
def webhook_receive():
    """
    Meta POSTs incoming WhatsApp events here.
    We validate the signature, extract text messages, and reply.
    """
    # --- Step A: Validate the X-Hub-Signature-256 header ---
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    raw_body = request.get_data()  # bytes

    if META_APP_SECRET:
        if not _verify_signature(raw_body, signature_header):
            log.warning("❌ Invalid signature — request rejected")
            return "Invalid signature", 403
    else:
        log.warning("⚠️  META_APP_SECRET not set — skipping signature check")

    # --- Step B: Parse the JSON body ---
    body = request.get_json(silent=True)
    if not body:
        return "Bad request", 400

    log.info("📩 Webhook event received")

    # --- Step C: Walk through the nested structure ---
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Ignore status updates (sent, delivered, read)
            if "statuses" in value:
                log.info("   ↳ Status update — ignored")
                continue

            messages = value.get("messages", [])
            for msg in messages:
                _handle_message(msg)

    # Always return 200 quickly so Meta doesn't retry
    return "OK", 200


# ───── 4. Twilio WhatsApp Trial / Webhook (POST) ──────
@app.route("/twilio/webhook", methods=["POST"])
def twilio_webhook():
    """
    Twilio POSTs incoming WhatsApp messages here as form data.
    Validates X-Twilio-Signature (fails closed if missing or invalid),
    and sends outbound reply using Twilio Messages API with ContentSid.
    """
    token = app.config.get("TWILIO_AUTH_TOKEN", "") or os.getenv("TWILIO_AUTH_TOKEN", "").strip()

    # Requirement 7: Fail closed if credentials are missing or invalid
    if not token:
        log.error("❌ TWILIO_AUTH_TOKEN is not configured in .env — failing closed (403)")
        return "Forbidden", 403

    if not _verify_twilio_signature(token):
        log.warning("❌ Invalid or missing Twilio signature — request rejected (403)")
        return "Invalid signature", 403

    # Parse incoming form fields
    body_text = request.form.get("Body", "").strip()
    sender = request.form.get("From", "").strip()

    if not body_text:
        # Acknowledge empty / non-text event
        return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="application/xml"), 200

    log.info(f"📱 WhatsApp message received (via Twilio): '{body_text}' from {_mask_phone(sender)}")

    # Choose reply text
    if body_text.lower() == "hi":
        reply = "Hello! Smart Nagrik local backend is connected successfully!"
    else:
        reply = "Smart Nagrik WhatsApp test is working."

    # Send real outbound message via Twilio Messages API using ContentSid template
    _send_twilio_whatsapp_reply(sender, reply)

    # Return empty 200 XML response so Twilio acknowledges receipt without expecting TwiML delivery
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="application/xml"), 200


# ──────────────────────────────────────────────
# Helpers — Meta
# ──────────────────────────────────────────────

def _verify_signature(raw_body: bytes, header: str) -> bool:
    """
    Check that the payload was really signed by Meta using our App Secret.
    Header format: "sha256=<hex digest>"
    """
    if not header.startswith("sha256="):
        return False

    expected = header[7:]  # strip "sha256="
    computed = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected)


def _handle_message(msg: dict) -> None:
    """Process one incoming WhatsApp message."""
    msg_id = msg.get("id", "")
    msg_type = msg.get("type", "")
    sender = msg.get("from", "")

    # Duplicate guard
    if msg_id in processed_message_ids:
        log.info(f"   ↳ Duplicate {msg_id} — skipped")
        return
    processed_message_ids.add(msg_id)

    # We only handle text messages
    if msg_type != "text":
        log.info(f"   ↳ Non-text message ({msg_type}) — ignored")
        return

    text = msg.get("text", {}).get("body", "")
    log.info(f"📱 WhatsApp message received: {text}")

    # Choose reply
    if text.strip().lower() == "hi":
        reply = "Hello! Smart Nagrik local backend is connected successfully ✅"
    else:
        reply = "Smart Nagrik test bot is connected."

    # Send the reply
    _send_text_reply(sender, reply)


def _send_text_reply(to: str, text: str) -> None:
    """Send a text message through the WhatsApp Cloud API."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        resp = http_requests.post(
            MESSAGES_URL,
            headers=headers,
            json=payload,
            timeout=10,  # seconds
        )
        if resp.status_code == 200:
            log.info(f"✅ Reply sent to {to}")
        else:
            log.error(
                f"❌ WhatsApp API error {resp.status_code}: {resp.text}"
            )
    except http_requests.exceptions.RequestException as exc:
        log.error(f"❌ Network error sending reply: {exc}")


# ──────────────────────────────────────────────
# Helpers — Twilio & Privacy
# ──────────────────────────────────────────────

def _mask_phone(phone: str) -> str:
    """Mask phone number for safe logging (e.g. whatsapp:+919876543210 -> whatsapp:+91****3210)."""
    if not phone:
        return "unknown"
    prefix = ""
    num = phone
    if phone.startswith("whatsapp:"):
        prefix = "whatsapp:"
        num = phone[len("whatsapp:"):]
    if len(num) <= 4:
        return f"{prefix}****"
    return f"{prefix}{num[:3]}****{num[-4:]}"


def _verify_twilio_signature(auth_token: str | None = None, public_url: str | None = None) -> bool:
    """
    Validate the X-Twilio-Signature header.

    Behind Cloudflare Tunnel the local Flask app sees http://localhost:8000
    but Twilio signed against the public https://...trycloudflare.com URL.
    We reconstruct the real URL from the X-Forwarded-* headers that
    Cloudflare injects.
    """
    token = auth_token or app.config.get("TWILIO_AUTH_TOKEN", "") or os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not token:
        log.error("❌ TWILIO_AUTH_TOKEN is missing — cannot verify signature")
        return False

    signature = request.headers.get("X-Twilio-Signature", "").strip()
    if not signature:
        log.warning("❌ Missing X-Twilio-Signature header")
        return False

    validator = RequestValidator(token)

    # The central backend passes the exact registered HTTPS URL. The original
    # standalone proof still supports forwarded headers when no URL is passed.
    if public_url:
        return validator.validate(public_url, request.form.to_dict(), signature)

    # Reconstruct the public URL Twilio actually called
    raw_proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    proto = raw_proto.split(",")[0].strip() if raw_proto else "https"

    raw_host = request.headers.get("X-Forwarded-Host", request.host)
    host = raw_host.split(",")[0].strip() if raw_host else request.host

    query = request.query_string.decode("utf-8") if request.query_string else ""
    url = f"{proto}://{host}{request.path}"
    if query:
        url = f"{url}?{query}"

    form_params = request.form.to_dict()
    return validator.validate(url, form_params, signature)


def _send_twilio_whatsapp_reply(to_number: str, message_text: str) -> dict | None:
    """
    Send an outbound WhatsApp reply using Twilio's official Python SDK (Messages API).
    In the new Twilio WhatsApp trial environment, outbound messages require
    a pre-approved template using TWILIO_CONTENT_SID (starts with HX...).
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip() or "whatsapp:+14155238886"
    content_sid = os.getenv("TWILIO_CONTENT_SID", "").strip()
    content_vars_raw = os.getenv("TWILIO_CONTENT_VARIABLES", "").strip()

    masked_to = _mask_phone(to_number)

    if not account_sid:
        log.error("❌ Outbound reply failed: TWILIO_ACCOUNT_SID is missing in .env")
        return None

    if not auth_token:
        log.error("❌ Outbound reply failed: TWILIO_AUTH_TOKEN is missing in .env")
        return None

    client = Client(account_sid, auth_token)

    # Build content variables if template contains placeholders
    content_vars = None
    if content_vars_raw:
        try:
            parsed = json.loads(content_vars_raw) if isinstance(content_vars_raw, str) else content_vars_raw
            content_vars = json.dumps(parsed)
        except Exception:
            content_vars = json.dumps({"1": message_text})
    elif content_sid:
        content_vars = json.dumps({"1": message_text})

    try:
        if content_sid:
            log.info(f"📤 Dispatching approved template (ContentSid={content_sid[:6]}...) to {masked_to}")
            create_kwargs = {
                "from_": from_number,
                "to": to_number,
                "content_sid": content_sid,
            }
            if content_vars:
                create_kwargs["content_variables"] = content_vars
            msg = client.messages.create(**create_kwargs)
        else:
            log.warning("⚠️  TWILIO_CONTENT_SID is not set in .env! Freeform text might be rejected in Twilio trial...")
            msg = client.messages.create(
                from_=from_number,
                to=to_number,
                body=message_text,
            )

        log.info(f"📨 Twilio message dispatched: SID={msg.sid}, Initial Status={msg.status}, To={masked_to}")

        # Check outbound message delivery status
        time.sleep(1.2)  # brief pause to allow carrier dispatch update
        try:
            updated = client.messages(msg.sid).fetch()
            log.info(f"📊 Delivery status check: SID={updated.sid}, Status={updated.status}")
            if updated.error_code:
                log.error(f"❌ Twilio delivery error {updated.error_code}: {updated.error_message}")
            return {"sid": updated.sid, "status": updated.status, "error_code": updated.error_code}
        except Exception as fetch_err:
            log.warning(f"⚠️ Could not fetch updated delivery status: {fetch_err}")
            return {"sid": msg.sid, "status": msg.status, "error_code": None}

    except TwilioRestException as exc:
        log.error(f"❌ Twilio API error {exc.code} (HTTP {exc.status}): {exc.msg}")
        return None
    except Exception as exc:
        log.error(f"❌ Unexpected error sending Twilio WhatsApp message: {exc}")
        return None


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    log.info("🚀 Starting WhatsApp Handshake Server on http://localhost:8000")

    if not WHATSAPP_ACCESS_TOKEN:
        log.warning("⚠️  WHATSAPP_ACCESS_TOKEN is empty — Meta replies won't work")
    if not WEBHOOK_VERIFY_TOKEN:
        log.warning("⚠️  WEBHOOK_VERIFY_TOKEN is empty — Meta verification will fail")
    if not os.getenv("TWILIO_AUTH_TOKEN"):
        log.warning("⚠️  TWILIO_AUTH_TOKEN is empty — Twilio webhooks will fail closed (403)")
    if not os.getenv("TWILIO_ACCOUNT_SID"):
        log.warning("⚠️  TWILIO_ACCOUNT_SID is empty — Twilio outbound replies disabled")
    if not os.getenv("TWILIO_CONTENT_SID"):
        log.warning("⚠️  TWILIO_CONTENT_SID is empty — Trial template replies require an approved ContentSid (HX...)")

    app.run(host="0.0.0.0", port=8000, debug=False)
