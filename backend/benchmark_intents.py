"""One bounded live comparison. No retries/cascades, credentials or headers in output."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from statistics import median
from time import perf_counter, sleep
import unicodedata

from dotenv import load_dotenv

from groq_provider import GroqIntentProvider, MODELS, SYSTEM_PROMPT
from intents import Extraction
from service import TextRequest, process_text as _process_text
from city_http import CityServiceError


class _NoCityLookups:
    """Intent benchmarks must not add city-provider calls after Milestone 04."""
    def forecast(self, *_):
        raise CityServiceError("not_implemented")

    def search(self, *_):
        raise CityServiceError("not_implemented")


def process_text(request, provider=None):
    disabled = _NoCityLookups()
    return _process_text(request, provider, weather=disabled, places=disabled)

DATASET = Path(__file__).parent / "evaluation" / "intent_cases.json"


def normal(value):
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("-", " ").split())
    return value


def field_values(understanding, path):
    values = []
    for alternative in path.split("|"):
        value = understanding
        for part in alternative.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        values.extend(value if isinstance(value, list) else [value])
    return values


def score(case, response):
    expected = case["expected"]
    actual = response.get("understanding", {})
    fields = {name: bool(actual) and any(normal(value) == normal(option) for value in field_values(actual, name) for option in options)
              for name, options in expected["fields"].items()}
    missing_explicit = [name for name, okay in fields.items() if not okay and None not in expected["fields"][name]
                        and all(value is None for value in field_values(actual, name))]
    primary = actual.get("primary_intent") == expected["primary_intent"]
    secondary = "secondary_intents" in actual and set(actual["secondary_intents"]) == set(expected["secondary_intents"])
    clarification = "missing_information" in actual and set(actual["missing_information"]) == set(expected["missing_information"])
    warnings = actual.get("validation_warnings", [])
    flagged = [item for item in warnings if item["code"] in {"ungrounded_evidence", "unsupported_normalization", "unsupported_currency", "unsupported_civic_category", "invalid_budget_amount", "city_context_conflict"}]
    return {"primary_correct": primary, "secondary_correct": secondary, "required_entities": fields,
            "explicit_required_entities": {name: okay for name, okay in fields.items() if None not in expected["fields"][name]},
            "missing_explicit_entities": missing_explicit, "clarification_correct": clarification,
            "unsupported_or_ungrounded_entities_rejected": len(flagged),
            "passed": primary and secondary and clarification and all(fields.values())}


def summarize(records):
    result = {}
    for model in MODELS:
        rows = [row for row in records if row["model"] == model and "response" in row]
        times = sorted(row["seconds"] for row in rows)
        entities = [okay for row in rows for okay in row["score"]["required_entities"].values()]
        explicit_entities = [okay for row in rows for okay in row["score"].get("explicit_required_entities", row["score"]["required_entities"]).values()]
        primary = sum(row["score"]["primary_correct"] for row in rows)
        core = [row for row in rows if row["core"]]
        result[model] = {
            "attempts": sum(row["model"] == model for row in records), "completed": len(rows),
            "primary_correct": primary, "secondary_correct": sum(row["score"]["secondary_correct"] for row in rows),
            "required_entities_correct": sum(entities), "required_entities_total": len(entities),
            "explicit_entities_correct": sum(explicit_entities), "explicit_entities_total": len(explicit_entities),
            "missing_explicit_entities": sum(len(row["score"]["missing_explicit_entities"]) for row in rows),
            "unsupported_or_ungrounded_entities_rejected": sum(row["score"]["unsupported_or_ungrounded_entities_rejected"] for row in rows),
            "schema_validation_failures": sum(row["response"].get("interpretation_status") in {"invalid_schema", "invalid_output"} for row in rows),
            "timeouts": sum(row["response"].get("interpretation_status") == "timeout" for row in rows),
            "rate_limits": sum(row["response"].get("interpretation_status") == "rate_limited" for row in rows),
            "provider_errors": sum(row["response"].get("interpretation_status") == "provider_error" for row in rows),
            "median_seconds": median(times) if times else None,
            "p95_seconds": times[max(0, math.ceil(0.95 * len(times)) - 1)] if times else None,
            "core_passed": [row["case_id"] for row in core if row["score"]["passed"]],
            "failed_cases": [row["case_id"] for row in rows if not row["score"]["passed"]],
            "quality_gate_passed": len(rows) == 25 and primary / 25 >= .9 and bool(explicit_entities) and sum(explicit_entities) / len(explicit_entities) >= .9
                                   and len(core) == 5 and all(row["score"]["passed"] for row in core),
        }
    return result


class RecordingProvider:
    def __init__(self, model):
        self.delegate = GroqIntentProvider(model=model)
        self.raw = None

    def extract(self, *args):
        self.raw = self.delegate.extract(*args)
        return self.raw


def run(output, interval_seconds=8, resume=False):
    if output.exists() and not resume:
        raise SystemExit("Output already exists; refusing to repeat potentially billable benchmark calls.")
    load_dotenv(Path(__file__).parent / ".env")
    key = os.getenv("GROQ_API_KEY", "").strip()
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if len(cases) != 25 or len({case["id"] for case in cases}) != 25:
        raise SystemExit("Benchmark must contain exactly 25 distinct cases.")
    output.parent.mkdir(parents=True, exist_ok=True)
    configuration = json.dumps({"prompt": SYSTEM_PROMPT, "schema": Extraction.model_json_schema(), "dataset": cases}, sort_keys=True, ensure_ascii=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "configuration_sha256": hashlib.sha256(configuration.encode()).hexdigest(),
              "boundary": "process_text wall time including provider, validation and response mapping; not Android network latency",
              "pacing_seconds_excluded_from_latency": interval_seconds,
              "live": bool(key and not key.startswith("YOUR_")), "records": [], "stop_reason": None}
    if resume:
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous["configuration_sha256"] != report["configuration_sha256"]:
            raise SystemExit("Cannot resume with changed dataset, prompt or schema.")
        if previous["stop_reason"] == "rate_limited":
            raise SystemExit("Rate limit reached; benchmark remains stopped as requested.")
        report = previous
        report.setdefault("stops", []).append({"reason": previous["stop_reason"], "reserved_calls": len(previous["records"])})
        report["stop_reason"] = None
    attempted = {(row["case_id"], row["model"]) for row in report["records"]}
    allowed = {(case["id"], model) for case in cases for model in MODELS}
    if len(attempted) != len(report["records"]) or not attempted <= allowed or len(attempted) > 50:
        raise SystemExit("Invalid benchmark call ledger.")

    def save():
        report["summary"] = summarize(report["records"])
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    if not report["live"]:
        report["stop_reason"] = "credentials_unavailable"
        save()
        print("Live comparison unavailable; keep openai/gpt-oss-20b.")
        return
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Interleave models for each case to reduce ordering bias. Exactly one call per row.
    for case in cases:
        for model in MODELS:
            if (case["id"], model) in attempted:
                continue  # Never repeat completed, failed or reserved model calls.
            if report["records"]:
                sleep(interval_seconds)  # Pace fresh calls; never retry a failed call.
            record = {"model": model, "case_id": case["id"], "core": case.get("core", False), "state": "reserved"}
            report["records"].append(record)
            attempted.add((case["id"], model))
            save()  # A crash cannot silently spend the same reserved call again.
            provider = RecordingProvider(model)
            started = perf_counter()
            response = process_text(TextRequest(case["message"], case["city"], "evaluation", context=case.get("context")), provider)
            record.update(state="completed", seconds=perf_counter() - started, response=response,
                          raw_extraction=provider.raw, score=score(case, response))
            save()
            print(f"{model}: case {case['id']} pass={record['score']['passed']} seconds={record['seconds']:.3f}", flush=True)
            if response.get("interpretation_status") in {"rate_limited", "authentication_error", "connection_error", "configuration_error"}:
                report["stop_reason"] = response["interpretation_status"]
                save()
                print("Stopped without retries: " + report["stop_reason"], flush=True)
                return
    report["stop_reason"] = "completed"
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "evaluation" / "live_results.json")
    parser.add_argument("--interval-seconds", type=float, default=8)
    parser.add_argument("--resume", action="store_true", help="Continue only unattempted case/model pairs; never retries failed calls.")
    args = parser.parse_args()
    if not 0 <= args.interval_seconds <= 30:
        parser.error("interval-seconds must be between 0 and 30")
    run(args.output, args.interval_seconds, args.resume)
