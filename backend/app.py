"""Local Smart Nagrik API."""

import logging
import os
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

load_dotenv(Path(__file__).resolve().parent / ".env")

from service import TextRequest, process_text
from intents import ACTIONS, validate_context
from twilio_adapter import register_twilio_webhook
from civic_routes import register_civic
from civic_service import CivicError, identity_hash
from tourism_routes import register_tourism
from city_http import CityServiceError


def envelope(data=None, error=None, status=200):
    return jsonify({"success": error is None, "data": data, "error": error}), status


def api_error(code, message, status, data=None):
    return envelope(data, {"code": code, "message": message}, status)


def create_app(processor=None, twilio_sender=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.config["API_HOST"] = os.getenv("SMART_NAGRIK_HOST", "127.0.0.1")
    app.config["API_PORT"] = int(os.getenv("SMART_NAGRIK_PORT", "5000"))
    allowed_origins = {part.strip() for part in os.getenv("SMART_NAGRIK_CORS_ORIGINS", "").split(",") if part.strip()}
    process = processor or process_text
    register_twilio_webhook(app, process, twilio_sender)
    register_civic(app)
    register_tourism(app)

    @app.after_request
    def add_cors(response):
        if request.path == '/v1/chat':
            response.headers['Cache-Control'] = 'private, no-store'
        # Native Android requests do not use browser CORS. Opt in only for a
        # known web development origin; never reflect arbitrary origins.
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.get("/health")
    def health():
        return envelope({"status": "ok"})

    @app.get("/v1/cities")
    def cities():
        return envelope({"cities": [{"name": "Bengaluru", "supported": True}]})

    @app.route("/v1/chat", methods=["POST", "OPTIONS"])
    def chat():
        if request.method == "OPTIONS":
            return "", 204
        if not request.is_json:
            return api_error("invalid_content_type", "Send a JSON request body.", 415)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return api_error("invalid_input", "Request body must be a JSON object.", 400)
        message, city = body.get("message"), body.get("city")
        if not isinstance(message, str) or not message.strip() or len(message.strip()) > 2000:
            return api_error("invalid_message", "message must contain 1 to 2000 characters.", 422)
        if not isinstance(city, str) or city.strip().casefold() != "bengaluru":
            return api_error("unsupported_city", "Only Bengaluru is supported for real-data requests.", 422)
        action = body.get("action")
        session_id = body.get("session_id")
        if session_id is not None and (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", session_id)):
            return api_error("invalid_session", "Invalid session identifier.", 422)
        if type(body.get("follow_up", False)) is not bool:
            return api_error("invalid_context", "follow_up must be a boolean.", 422)
        quick = body.get("quick_reply")
        if quick is not None and (not isinstance(quick, str) or quick not in {"mode_walk", "mode_metro", "mode_bus", "weather_tomorrow", "food_nearby", "vegetarian", "paneer", "tourism_shorter", "tourism_food", "tourism_nature"}):
            return api_error("invalid_action", "Unknown quick reply.", 422)
        if action is not None and (not isinstance(action, str) or action not in ACTIONS):
            return api_error("invalid_action", "Unknown application action.", 422)
        try:
            context = validate_context(body.get("context"))
        except (ValueError, TypeError):
            return api_error("invalid_context", "Supply at most three recent messages and supported location fields.", 422)

        request_id = uuid.uuid4().hex
        try:
            owner = identity_hash(request.headers.get('Authorization'))
        except CivicError as error:
            return api_error(error.code, error.message, error.status)
        try:
            result = process(TextRequest(message=message.strip(), city="Bengaluru", channel="android", action=action, context=context,
                                         session_ref=session_id, use_session=True, follow_up=body.get("follow_up", False), quick_reply=quick, owner_ref=owner))
        except Exception:
            app.logger.error("Chat processing failed; request_id=%s", request_id)
            return api_error("service_unavailable", "City answers are temporarily unavailable.", 503)
        result = {**result, "request_id": request_id}
        if owner:
            for section in result.get('sections', []):
                if section.get('itinerary'):
                    try:
                        app.extensions['tourism_store'].save(owner, result['understanding'], section)
                    except (ValueError, RuntimeError, CityServiceError):
                        pass  # Result remains usable if optional referencing fails.
        if result.get("status") == "service_not_configured":
            return api_error("service_not_configured", "Live city services are not configured.", 503, result)
        if result.get("status", "").startswith("intent_"):
            status = result["status"]
            http_status = 504 if status == "intent_timeout" else 503
            return api_error(status, result["description"], http_status, result)
        return envelope(result)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return api_error("http_error", exc.description, exc.code or 500)

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception("Unhandled API error")
        return api_error("internal_error", "An unexpected server error occurred.", 500)

    logging.basicConfig(level=os.getenv("SMART_NAGRIK_LOG_LEVEL", "INFO"))
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=app.config["API_HOST"], port=app.config["API_PORT"], debug=False)
