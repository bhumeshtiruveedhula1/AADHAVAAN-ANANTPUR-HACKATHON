"""Explicit, small live acceptance check. Not imported or run by unit tests."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from time import perf_counter

from app import create_app
from groq_provider import GroqIntentProvider
from service import process_text
from weather_service import WeatherService
from places_service import PlacesService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("weather", "places"), default="weather")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not os.getenv("GROQ_API_KEY", "").strip():
        raise SystemExit("Groq key is not configured; no live call performed.")
    if args.case == "places" and not os.getenv("GOOGLE_PLACES_API_KEY", "").strip():
        raise SystemExit("Google Places key is not configured; no live call performed.")
    timings = {}
    class MeasuredGroq(GroqIntentProvider):
        def extract(self, *values):
            start = perf_counter()
            try:
                return super().extract(*values)
            finally:
                timings["intent_seconds"] = round(perf_counter() - start, 4)
    class MeasuredWeather(WeatherService):
        def forecast(self, *values):
            start = perf_counter()
            try:
                return super().forecast(*values)
            finally:
                timings["weather_seconds"] = round(perf_counter() - start, 4)
    class MeasuredPlaces(PlacesService):
        def search(self, *values):
            start = perf_counter()
            try:
                return super().search(*values)
            finally:
                timings["places_seconds"] = round(perf_counter() - start, 4)
    provider, weather, places = MeasuredGroq(), MeasuredWeather(), MeasuredPlaces()
    client = create_app(processor=lambda request: process_text(request, provider, weather, places)).test_client()
    message = "Will it rain in Bengaluru this evening?" if args.case == "weather" else "Find hotels near Majestic in Bengaluru."
    started = perf_counter()
    response = client.post("/v1/chat", json={"message": message, "city": "Bengaluru"})
    timings["endpoint_seconds"] = round(perf_counter() - started, 4)
    data = response.json.get("data") or {}
    record = {"performed_at": datetime.now(timezone.utc).isoformat(), "case": args.case, "request": {"message": message, "city": "Bengaluru"},
              "weather_interval_semantics": "temperature at interval start; precipitation for preceding hour at interval end",
              "http_status": response.status_code, "timings": timings,
              "google_key_configured": bool(os.getenv("GOOGLE_PLACES_API_KEY", "").strip()),
              "verified": response.status_code == 200 and data.get("status") == "ok",
              "boundary": "Flask test client -> live Groq -> live provider -> composer; no device or Twilio delivery",
              "response": response.json if args.case == "weather" else {"status": data.get("status"), "place_count": len(data.get("places", []))}}
    # Do not persist Google Places content; weather data is attributed in response.
    output = Path(__file__).parent / "evaluation" / f"milestone04_{args.case}_smoke.json"
    attempts = []
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        attempts = previous.get("attempts", [previous])
    attempts.append(record)
    output.write_text(json.dumps({"attempts": attempts}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case": args.case, "http_status": response.status_code, "status": data.get("status"), "verified": record["verified"], "timings": timings,
                      "google_key_configured": record["google_key_configured"], "artifact": str(output)}))
    return 0 if record["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
