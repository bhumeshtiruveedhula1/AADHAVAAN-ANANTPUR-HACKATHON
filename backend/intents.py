"""Compact semantic extraction, evidence validation and Python-owned routing."""

from enum import Enum
import json
import math
import re
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class IntentCategory(str, Enum):
    ACCOMMODATION = "accommodation"
    FOOD = "food_places"
    PLACES = "places_services"
    TRANSPORT = "transport"
    WEATHER = "weather_conditions"
    REPORT = "civic_report"
    LOOKUP = "civic_lookup"
    TOURISM = "tourism"
    EMERGENCY = "emergency"
    GUIDANCE = "city_guidance"
    UNSUPPORTED = "unsupported"


Action = Literal["report_issue", "with_you", "view_report_status", "open_directions", "select_city"]
ACTIONS = {
    "report_issue": IntentCategory.REPORT, "with_you": IntentCategory.LOOKUP,
    "view_report_status": IntentCategory.LOOKUP, "open_directions": IntentCategory.TRANSPORT,
    "select_city": IntentCategory.GUIDANCE,
}
EXACT_ACTIONS = {
    "report issue": "report_issue", "with you": "with_you", "view report status": "view_report_status",
    "open directions": "open_directions", "select city": "select_city",
}
HANDLERS = {
    IntentCategory.ACCOMMODATION: "accommodation_service", IntentCategory.FOOD: "places_service",
    IntentCategory.PLACES: "places_service", IntentCategory.TRANSPORT: "transport_service",
    IntentCategory.WEATHER: "weather_service", IntentCategory.REPORT: "report_service",
    IntentCategory.LOOKUP: "civic_records_service", IntentCategory.TOURISM: "itinerary_service",
    IntentCategory.EMERGENCY: "emergency_information_service",
    IntentCategory.GUIDANCE: "city_guidance_service", IntentCategory.UNSUPPORTED: None,
}


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(ClosedModel):
    type: Literal["city", "area", "landmark", "origin", "destination", "budget", "currency",
                  "time", "duration", "transport_preference", "civic_category", "exact_location",
                  "preference", "accessibility", "report_id"]
    value: str
    evidence: str


class Extraction(ClosedModel):
    primary_intent: IntentCategory
    secondary_intents: list[IntentCategory]
    entities: list[Entity]


class Budget(ClosedModel):
    amount: float | None
    currency: str | None
    raw_text: str
    constraint: str | None = None


class IntentFields(ClosedModel):
    """Existing internal fields remain available to the response layer."""
    primary_intent: IntentCategory
    secondary_intents: list[IntentCategory]
    language: Literal["en", "hi", "kn"] | None = None
    city: str | None = None
    area: str | None = None
    landmark: str | None = None
    origin: str | None = None
    destination: str | None = None
    exact_location: str | None = None
    report_id: str | None = None
    budget: Budget | None = None
    time_constraint: str | None = None
    duration: str | None = None
    transport_preference: str | None = None
    preferences: list[str] = Field(default_factory=list)
    accessibility_constraints: list[str] = Field(default_factory=list)
    civic_category: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    entities: list[dict] = Field(default_factory=list)
    validation_warnings: list[dict] = Field(default_factory=list)


class TextContext(ClosedModel):
    recent_messages: list[str] = Field(default_factory=list, max_length=3)
    area: str | None = Field(default=None, max_length=300)
    landmark: str | None = Field(default=None, max_length=300)
    origin: str | None = Field(default=None, max_length=300)
    destination: str | None = Field(default=None, max_length=300)
    exact_location: str | None = Field(default=None, max_length=300)
    report_id: str | None = Field(default=None, max_length=100)


class ExtractionError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("Invalid extraction schema")


def validate_context(value):
    context = TextContext.model_validate({} if value is None else value, strict=True)
    if any(not item.strip() or len(item) > 1000 for item in context.recent_messages):
        raise ValueError("Invalid recent context")
    return context.model_dump()


def _normal(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _phrase(text):
    text = text.strip()
    pairs = {'"': '"', "'": "'", '“': '”', '‘': '’'}
    while len(text) >= 2 and pairs.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text


def _diagnostic(path, code):
    return {"path": path, "code": code}


def _currency(text):
    patterns = {"INR": r"₹|\b(?:inr|rupees?|rs)\b", "USD": r"\$|\b(?:usd|dollars?)\b",
                "EUR": r"€|\b(?:eur|euros?)\b"}
    matches = [code for code, pattern in patterns.items() if re.search(pattern, text, re.I)]
    return matches[0] if len(matches) == 1 else None


def _vague_location(text):
    return bool(re.search(r"\b(my college|my house|near me|here|over there)\b", text, re.I))


_DURATION = re.compile(r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|half(?:\s+an?)?|an?)[ -]+(?:day|hour|minute|week)s?\b", re.I)
_TIME = re.compile(r"\b(?:(?:this|next|tomorrow|yesterday)\s+(?:morning|afternoon|evening|night)|right now|tonight|today|tomorrow|(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?))(?!\w)", re.I)


def validate_extraction(payload, message, city, context):
    """Reject unusable schemas; recover independent optional entities with diagnostics."""
    warnings = []
    try:
        parsed = Extraction.model_validate_json(json.dumps(payload, allow_nan=False), strict=True)
    except (ValidationError, ValueError, TypeError) as error:
        errors = error.errors(include_input=False, include_url=False) if isinstance(error, ValidationError) else []
        # Recover only invalid individual optional entity records, never routing fields.
        bad_indices = {item["loc"][1] for item in errors
                       if len(item["loc"]) >= 2 and item["loc"][0] == "entities" and isinstance(item["loc"][1], int)}
        if not errors or not bad_indices or any(item["loc"][0] != "entities" or len(item["loc"]) < 2 for item in errors):
            diagnostics = [_diagnostic(".".join(map(str, item["loc"])), item["type"]) for item in errors]
            raise ExtractionError(diagnostics or [_diagnostic("$", "invalid_schema")]) from None
        warnings.extend(_diagnostic(".".join(map(str, item["loc"])), item["type"]) for item in errors)
        cleaned = {**payload, "entities": [entity for index, entity in enumerate(payload["entities"]) if index not in bad_indices]}
        try:
            parsed = Extraction.model_validate_json(json.dumps(cleaned, allow_nan=False), strict=True)
        except (ValidationError, ValueError, TypeError):
            raise ExtractionError([_diagnostic("$", "invalid_schema")]) from None

    result = IntentFields(primary_intent=parsed.primary_intent, secondary_intents=[])
    for secondary in parsed.secondary_intents:
        if secondary not in {parsed.primary_intent, IntentCategory.UNSUPPORTED} and secondary not in result.secondary_intents and len(result.secondary_intents) < 3:
            result.secondary_intents.append(secondary)
        else:
            warnings.append(_diagnostic("secondary_intents", "redundant_intent"))
    if parsed.primary_intent == IntentCategory.UNSUPPORTED:
        result.secondary_intents = []
    sources = [message, *context["recent_messages"],
               *(value for key, value in context.items() if key != "recent_messages" and value), city or ""]
    sources = [_normal(source) for source in sources]
    budget_currency = None
    for index, entity in enumerate(parsed.entities[:24]):
        path = f"entities.{index}"
        evidence, value = _phrase(entity.evidence), _phrase(entity.value)
        # Context is trusted only as explicitly supplied data; JSON labels are
        # not user phrases. Reconcile a matching city against that known value.
        if entity.type == "city" and city and _normal(value) == _normal(city):
            evidence = city
        if not evidence or len(evidence) > 1000 or not any(_normal(evidence) in source for source in sources):
            warnings.append(_diagnostic(path + ".evidence", "ungrounded_evidence"))
            continue
        if not value or len(value) > 300:
            warnings.append(_diagnostic(path + ".value", "invalid_value"))
            continue
        kind = entity.type
        if kind == "budget":
            numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", value)
            stated = [float(number.replace(",", "")) for number in re.findall(r"\d[\d,]*(?:\.\d+)?", evidence)]
            amount = float(numbers[0].replace(",", "")) if len(numbers) == 1 else None
            if amount is None or not math.isfinite(amount) or amount < 0 or amount not in stated:
                warnings.append(_diagnostic(path + ".value", "invalid_budget_amount"))
                continue
            result.budget = Budget(amount=amount, currency=_currency(evidence), raw_text=evidence,
                                   constraint="maximum" if re.search(r"\b(under|below|up to|at most|max(?:imum)?)\b", evidence, re.I) else None)
            value = str(amount)
        elif kind == "currency":
            currency = _currency(evidence)
            if not currency or value.upper() != currency:
                warnings.append(_diagnostic(path + ".value", "unsupported_currency"))
                continue
            budget_currency = currency
            value = currency
        elif kind == "civic_category":
            civic_terms = {"pothole": r"\bpotholes?\b", "garbage": r"\b(garbage|rubbish|trash|waste)\b",
                           "streetlight": r"\bstreet\s*lights?\b", "waterlogging": r"\b(waterlog\w*|flood\w*)\b",
                           "other": r"."}
            if value not in civic_terms or not re.search(civic_terms[value], evidence, re.I):
                warnings.append(_diagnostic(path + ".value", "unsupported_civic_category"))
                continue
            result.civic_category = value
        else:
            if _normal(value) not in _normal(evidence):
                if kind in {"time", "duration"}:
                    value = evidence  # Keep the stated phrase rather than invent a clock/date.
                    warnings.append(_diagnostic(path + ".value", "used_original_time_phrase"))
                else:
                    warnings.append(_diagnostic(path + ".value", "unsupported_normalization"))
                    continue
            if kind == "exact_location" and _vague_location(value):
                warnings.append(_diagnostic(path + ".value", "imprecise_incident_location"))
                continue
            if kind == "preference":
                result.preferences.append(value)
            elif kind == "accessibility":
                result.accessibility_constraints.append(value)
            elif kind == "time":
                result.time_constraint = value
            elif kind == "duration":
                result.duration = value
            elif kind == "transport_preference":
                result.transport_preference = value
                result.preferences.append(value)
            elif kind == "city" and city:
                if _normal(value) != _normal(city):
                    warnings.append(_diagnostic(path + ".value", "city_context_conflict"))
                    continue
            else:
                setattr(result, kind, value)
        result.entities.append({"type": kind, "value": value, "evidence": evidence,
                                "source": "request_context" if kind == "city" and city else "groq"})

    if len(parsed.entities) > 24:
        warnings.append(_diagnostic("entities", "entity_limit"))
    if result.budget and budget_currency:
        if result.budget.currency and result.budget.currency != budget_currency:
            warnings.append(_diagnostic("budget.currency", "conflicting_currency"))
        else:
            result.budget.currency = budget_currency
    # Preserve a literal unit-bearing constraint even when the model omits it.
    # This grammar never chooses an intent or invents a numeric/calendar value.
    if not result.duration:
        duration = _DURATION.search(message)
        if duration:
            result.duration = duration.group()
            result.entities.append({"type": "duration", "value": result.duration, "evidence": result.duration, "source": "literal_normalizer"})
    if not result.time_constraint:
        time = _TIME.search(message)
        if time:
            result.time_constraint = time.group()
            result.entities.append({"type": "time", "value": result.time_constraint, "evidence": result.time_constraint, "source": "literal_normalizer"})
    result.time_constraint = result.time_constraint or result.duration
    # Bengaluru literally named in the request is supplied city information,
    # unlike a landmark or telephone number. No GPS/current location is inferred.
    if not city and not result.city and re.search(r"\bBengaluru\b", message, re.I):
        result.city = "Bengaluru"
        result.entities.append({"type": "city", "value": "Bengaluru", "evidence": "Bengaluru", "source": "literal_normalizer"})
    result.validation_warnings = warnings
    return _apply_context(result, city, context)


def _apply_context(result, city, context):
    result.city = city or result.city
    for field in ("area", "landmark", "origin", "destination", "exact_location", "report_id"):
        if not getattr(result, field) and context[field]:
            setattr(result, field, context[field])
    if result.exact_location and _vague_location(result.exact_location):
        result.exact_location = None
    return result


def understand(message, city=None, context=None, action=None, provider=None):
    context = validate_context(context)
    action = action or EXACT_ACTIONS.get(_normal(message))
    if action:
        if action not in ACTIONS:
            raise ValueError("Unsupported action")
        parsed = _apply_context(IntentFields(primary_intent=ACTIONS[action], secondary_intents=[]), city, context)
        method = "rules"
    else:
        if provider is None:
            from groq_provider import GroqIntentProvider
            provider = GroqIntentProvider()
        parsed = validate_extraction(provider.extract(message, city, context), message, city, context)
        method = "groq"

    missing = []
    categories = {parsed.primary_intent, *parsed.secondary_intents}
    if parsed.primary_intent == IntentCategory.UNSUPPORTED:
        missing = ["request_goal"]
    else:
        if not parsed.city:
            missing.append("city")
        if IntentCategory.TRANSPORT in categories:
            missing.extend(name for name in ("origin", "destination") if not getattr(parsed, name))
        if IntentCategory.REPORT in categories and not parsed.exact_location:
            missing.append("exact_location")
        if action in {"with_you", "view_report_status"} and not parsed.report_id:
            missing.append("report_id")
    result = parsed.model_dump(mode="json")
    result.update(original_text=message, action=action, method=method,
                  missing_information=missing, clarification_needed=bool(missing),
                  suggested_service_handler=HANDLERS[parsed.primary_intent],
                  processing_status="unsupported" if parsed.primary_intent == IntentCategory.UNSUPPORTED else
                  ("clarification_required" if missing else "understood"))
    return result
