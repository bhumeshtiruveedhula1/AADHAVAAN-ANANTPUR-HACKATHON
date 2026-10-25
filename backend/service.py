"""Channel-neutral text processing boundary for Android and WhatsApp."""

from dataclasses import dataclass

from groq_provider import ProviderError
from intents import ExtractionError, understand
from city_services import compose
from conversation import SESSIONS, SessionError, prepare, refined_intent, merge_followup, quick_replies


@dataclass(frozen=True)
class TextRequest:
    message: str
    city: str | None
    channel: str
    provider_message_id: str | None = None
    sender_ref: str | None = None
    session_ref: str | None = None
    action: str | None = None
    context: dict | None = None
    follow_up: bool = False
    quick_reply: str | None = None
    use_session: bool = False
    owner_ref: str | None = None


def process_text(request: TextRequest, provider=None, weather=None, places=None) -> dict:
    if not request.use_session:
        return _process_text(request, provider, weather, places)
    try:
        with SESSIONS.turn(request.channel, request.session_ref) as (identifier, entry):
            if entry.get('result') and entry.get('owner') != request.owner_ref:
                raise SessionError('session_owner_mismatch')
            entry['owner'] = request.owner_ref
            previous = entry["result"]
            prepared, old = prepare(request, previous)
            if request.quick_reply and not old:
                raise SessionError("session_expired")
            result = _process_text(prepared, provider, weather, places, old, previous)
            result.update(session_id=identifier, session_expires_in=SESSIONS.ttl)
            if result.get("understanding") and not result.get("status", "").startswith("intent_"):
                result["quick_replies"] = quick_replies(result)
                entry["result"] = result
            return result
    except SessionError as error:
        return {"type": "guide", "title": "Conversation unavailable", "description": "Please send your request again. The conversation expired, is busy, or the requested reply is no longer available.",
                "details": [], "places": [], "status": str(error)}


def _process_text(request, provider=None, weather=None, places=None, old=None, previous=None):
    """One intent inference, bounded read-only lookups, deterministic composition."""
    result = {
        "type": "unavailable",
        "title": "City answers are not connected yet",
        "description": (
            "Smart Nagrik received your request, but live city services "
            "are not configured. Please try again after the service is connected."
        ),
        "details": ["Bengaluru only" if request.city else "City context not established", "Live results unavailable"],
        "places": [],
        "status": "service_not_configured",
        "source": None,
    }
    try:
        intent = refined_intent(request, old, previous) or understand(request.message, request.city, request.context, request.action, provider)
        intent = merge_followup(intent, old)
    except ProviderError as error:
        messages = {
            "not_configured": "Intent understanding is not configured yet. The team must supply the server's Groq API key.",
            "configuration_error": "Intent understanding has a server configuration error.",
            "authentication_error": "The intent provider could not authenticate the server.",
            "timeout": "Intent understanding timed out. Please try again.",
            "rate_limited": "The intent provider is temporarily rate limited. Please try again later.",
            "connection_error": "The server could not reach the intent provider. Please try again.",
            "busy": "Intent understanding is busy. Please try again shortly.",
            "refused": "The intent provider could not interpret this request. Please rephrase it.",
        }
        result.update(title="Intent understanding unavailable", description=messages.get(error.code, "The intent provider could not return a valid interpretation. Please try again."),
                      interpretation_status=error.code,
                      status="service_not_configured" if error.code == "not_configured" else "intent_" + error.code)
        if error.http_status is not None:
            result["provider_diagnostics"] = {"http_status": error.http_status, "code": error.code}
        return result
    except ExtractionError as error:
        result.update(title="Could not validate the interpretation", description="Please rephrase your request. No city service or action has been run.",
                      interpretation_status="invalid_schema", status="intent_invalid_output", validation_diagnostics=error.diagnostics)
        return result
    except (ValueError, TypeError):
        result.update(title="Could not validate the interpretation", description="Please rephrase your request. No city service or action has been run.",
                      interpretation_status="invalid_output", status="intent_invalid_output")
        return result

    result = compose(intent, weather=weather, places=places, previous=previous if old else None, owner=request.owner_ref)
    if result["status"] == "not_configured":
        result["status"] = "service_not_configured"
    return result
