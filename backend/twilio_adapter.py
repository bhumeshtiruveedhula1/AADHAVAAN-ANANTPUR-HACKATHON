"""Twilio transport adapter for the single local Smart Nagrik Flask app.

The original connector remains the source of truth for signature verification
and trial-compatible outbound Messages API calls. Only this central app is run.
"""

import importlib.util
import json
import logging
import os
from pathlib import Path
from queue import Full, Queue
import re
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit

from flask import Response, request

from service import TextRequest


_source = Path(__file__).resolve().parent.parent / "whatsapp-integration" / "app.py"
_spec = importlib.util.spec_from_file_location("smart_nagrik_existing_whatsapp", _source)
_connector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_connector)

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_SID = re.compile(r"^SM[0-9a-fA-F]{32}$")
_SENDER = re.compile(r"^whatsapp:\+[0-9]{7,15}$")
_log = logging.getLogger("smart_nagrik.twilio")


def register_twilio_webhook(app, processor, sender=None):
    """Register one signed webhook; process and send outside its request thread."""
    outbound = sender or _connector._send_twilio_whatsapp_reply
    pending = Queue(maxsize=64)
    seen = {}
    guard = Lock()
    worker_started = False

    def work():
        while True:
            incoming = pending.get()
            try:
                result = processor(incoming)
                _log.info("Processed WhatsApp text; sid=%s status=%s", incoming.provider_message_id, result.get("status", "ok"))
                # The current trial sends only its configured approved template.
                # Never feed arbitrary city answers into an unverified template.
                if _outbound_ready():
                    sent = outbound(incoming.sender_ref, "Smart Nagrik received your message.")
                    if sent is None:
                        _log.warning("Twilio outbound request failed; sid=%s", incoming.provider_message_id)
                else:
                    _log.warning("Twilio outbound template/credentials are not configured; sid=%s", incoming.provider_message_id)
            except Exception:
                _log.error("WhatsApp processing failed; sid=%s", incoming.provider_message_id)
            finally:
                pending.task_done()

    @app.post("/twilio/webhook")
    def twilio_webhook():
        nonlocal worker_started
        token = app.config.get("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        public_url = app.config.get("TWILIO_PUBLIC_WEBHOOK_URL") or os.getenv("TWILIO_PUBLIC_WEBHOOK_URL", "").strip()
        if not token or not public_url:
            _log.error("Twilio auth token or public webhook URL is not configured")
            return "Webhook not configured", 503
        parsed = urlsplit(public_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path != "/twilio/webhook" or parsed.fragment:
            _log.error("Twilio public webhook URL must be the exact HTTPS /twilio/webhook address")
            return "Webhook configuration invalid", 503
        if parsed.query != request.query_string.decode("utf-8"):
            return "Webhook URL mismatch", 403
        if not _connector._verify_twilio_signature(token, public_url):
            _log.warning("Rejected invalid or missing Twilio signature")
            return "Invalid signature", 403

        message = request.form.get("Body", "").strip()
        if not message:
            return Response(_EMPTY_TWIML, content_type="application/xml")
        sid = request.form.get("MessageSid", "").strip()
        sender_ref = request.form.get("From", "").strip()
        if not _SID.fullmatch(sid) or not _SENDER.fullmatch(sender_ref) or len(message) > 2000:
            return "Invalid message", 400

        incoming = TextRequest(
            message=message,
            city=None,  # Twilio does not provide a verified city or GPS location.
            channel="whatsapp",
            provider_message_id=sid,
            sender_ref=sender_ref,
            session_ref=sender_ref,
        )
        with guard:
            now = monotonic()
            while seen:
                old_sid = next(iter(seen))
                if now - seen[old_sid] <= 86400:
                    break
                del seen[old_sid]
            if sid in seen:
                return Response(_EMPTY_TWIML, content_type="application/xml")
            if len(seen) >= 10000:
                _log.error("Twilio deduplication capacity reached")
                return "Temporarily busy", 503
            try:
                pending.put_nowait(incoming)
            except Full:
                _log.error("Twilio work queue is full")
                return "Temporarily busy", 503
            seen[sid] = now
            if not worker_started:
                Thread(target=work, daemon=True, name="smart-nagrik-twilio").start()
                worker_started = True
        return Response(_EMPTY_TWIML, content_type="application/xml")


def _outbound_ready():
    required = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER", "TWILIO_CONTENT_SID")
    if not all(os.getenv(name, "").strip() for name in required):
        return False
    # Require explicit, static approved-template substitutions. This prevents
    # the old connector's freeform or dynamic-variable fallback in trial mode.
    raw = os.getenv("TWILIO_CONTENT_VARIABLES", "").strip()
    try:
        return isinstance(json.loads(raw), dict)
    except (ValueError, TypeError):
        return False
