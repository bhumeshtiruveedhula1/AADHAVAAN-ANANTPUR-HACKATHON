"""Three explicit bounded live acceptance cases; retain metrics, never Places content."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
from time import perf_counter

import httpx

from app import create_app
from groq_provider import GroqIntentProvider
from places_service import PlacesService, safe_photo_uri
from service import process_text

CASES = [
    ("hotels", "Show hotels near Majestic.", ["accommodation"]),
    ("food", "Find restaurants near Indiranagar.", ["food_places"]),
    ("mixed", "I'm at Majestic at 4 AM. I need a hotel under ₹1,500 and something to eat.", ["accommodation", "food_places"]),
]


def main():
    for logger in ("httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.WARNING)
    if not os.getenv("GROQ_API_KEY") or not os.getenv("GOOGLE_PLACES_API_KEY"):
        raise SystemExit("Supply backend Groq and Google Places keys before the explicit live check.")
    output = Path(__file__).parent / "evaluation" / "milestone041_live.json"
    if output.exists():
        raise SystemExit("Live evidence already exists. No automatic repeat or overwrite performed.")
    records = []
    counts = {"groq": 0, "places": 0, "photo_media": 0, "image_fetch": 0}
    class CountingClient(httpx.Client):
        def request(self, method, url, **kwargs):
            if url.endswith(":searchText"):
                counts["places"] += 1
            elif "/media" in url:
                counts["photo_media"] += 1
            return super().request(method, url, **kwargs)
    class CountingGroq(GroqIntentProvider):
        def extract(self, *args):
            counts["groq"] += 1
            return super().extract(*args)
    with CountingClient(timeout=httpx.Timeout(4, connect=2, pool=0.5), follow_redirects=False) as network:
        places = PlacesService(network)
        client = create_app(processor=lambda req: process_text(req, CountingGroq(), places=places)).test_client()
        for name, message, expected in CASES:
            start = perf_counter()
            response = client.post("/v1/chat", json={"message": message, "city": "Bengaluru"})
            elapsed = round(perf_counter() - start, 4)
            data = response.json.get("data") or {}
            sections = data.get("sections", [])
            records_out = data.get("places", [])
            photos = [place["photo"] for place in records_out if place.get("photo")]
            image_verified = False
            image_bytes = None
            # One real image download per case, in memory only, no disk/cache.
            if photos and safe_photo_uri(photos[0]["url"]):
                counts["image_fetch"] += 1
                try:
                    with network.stream("GET", photos[0]["url"], timeout=4) as image:
                        size, prefix = 0, b""
                        for chunk in image.iter_bytes():
                            size += len(chunk)
                            prefix = (prefix + chunk)[:16]
                            if size > 2_000_000:
                                break
                        signature = prefix.startswith(b"\xff\xd8\xff") or prefix.startswith(b"\x89PNG") or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
                        image_verified = image.status_code == 200 and image.headers.get("content-type", "").startswith("image/") and signature and size <= 2_000_000
                        image_bytes = size
                except httpx.RequestError:
                    pass
            # Feed the actual envelope to the real TS adapter without storing it.
            check = subprocess.run(["node", "../frontend-app/tests/accept-response.cjs"], input=json.dumps(response.json), capture_output=True, text=True, timeout=15)
            record = {"case": name, "http_status": response.status_code, "status": data.get("status"), "endpoint_seconds": elapsed,
                      "intents": [section["intent"] for section in sections], "expected_intents": expected,
                      "place_count": len(records_out), "photo_count": len(photos),
                      "photo_statuses": [place.get("photo_status") for place in records_out],
                      "photo_authors_present": sum(bool(photo["authors"]) for photo in photos),
                      "individual_photo_source_links": sum(bool(photo["source_url"]) for photo in photos),
                      "image_http_and_signature_verified": image_verified, "image_bytes_in_memory": image_bytes,
                      "actual_frontend_adapter_passed": check.returncode == 0,
                      "verified": response.status_code == 200 and data.get("status") == "ok" and [section["intent"] for section in sections] == expected and bool(records_out) and check.returncode == 0}
            records.append(record)
            output.write_text(json.dumps({"performed_at": datetime.now(timezone.utc).isoformat(), "records": records, "requests": counts,
                "boundary": "Flask endpoint, live Groq/Places/Photos, one in-memory image fetch per case and actual TypeScript adapter; no Android device or native map verification"}, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(record))
            if not record["verified"]:
                break  # No blind retries or unbounded troubleshooting.
    return 0 if len(records) == 3 and all(record["verified"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
