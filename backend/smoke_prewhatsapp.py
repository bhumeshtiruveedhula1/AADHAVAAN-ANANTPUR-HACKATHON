"""Explicit bounded live tourism acceptance; no civic writes or server restart.

Two Groq calls, at most four Places searches/six photo requests/four resolutions,
and one cached Open-Meteo forecast. Only counts/timings are persisted.
"""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from time import perf_counter
import subprocess

from app import create_app
from service import process_text
from groq_provider import GroqIntentProvider
from places_service import PlacesService
from weather_service import WeatherService


def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    if not all(os.getenv(name, '').strip() for name in ('GROQ_API_KEY', 'GOOGLE_PLACES_API_KEY')):
        raise SystemExit('Existing provider configuration missing; no calls performed.')
    counts = {'groq': 0, 'places_searches': 0, 'resolutions': 0}
    class Groq(GroqIntentProvider):
        def extract(self, *args):
            counts['groq'] += 1
            return super().extract(*args)
    class Places(PlacesService):
        def search(self, *args):
            counts['places_searches'] += 1
            return super().search(*args)
        def resolve(self, *args):
            counts['resolutions'] += 1
            return super().resolve(*args)
    groq, places, weather = Groq(), Places(), WeatherService()
    client = create_app(processor=lambda req: process_text(req, groq, weather, places)).test_client()
    cases = [('nature_half_day', 'Plan six hours of nature sightseeing starting in Indiranagar with vegetarian food.'),
             ('shorter', 'Shorter plan'),
             ('heritage_full_day', 'Plan a one-day heritage sightseeing trip starting in Jayanagar.')]
    session = None
    records = []
    previous_id = None
    for name, message in cases:
        payload = {'message': message, 'city': 'Bengaluru'}
        if name == 'shorter':
            payload.update(session_id=session, follow_up=True, quick_reply='tourism_shorter')
        before = dict(counts)
        started = perf_counter()
        response = client.post('/v1/chat', json=payload)
        data = response.json.get('data') or {}
        session = data.get('session_id')
        section = next((s for s in data.get('sections', []) if s.get('itinerary')), {})
        itinerary = section.get('itinerary', {})
        accepted = False
        if data.get('places'):
            script = Path(__file__).parent.parent / 'frontend-app/tests/accept-response.cjs'
            check = subprocess.run(['node', str(script)], input=json.dumps(response.json), capture_output=True, text=True, timeout=15)
            accepted = check.returncode == 0
        verified = response.status_code == 200 and bool(itinerary) and bool(data.get('places')) and accepted
        if name == 'shorter':
            verified = verified and itinerary.get('id') == previous_id and itinerary.get('constraints', {}).get('duration_hours') == 3
        previous_id = itinerary.get('id')
        record = {'case': name, 'http_status': response.status_code, 'status': data.get('status'), 'verified': verified,
                  'endpoint_and_adapter_seconds': round(perf_counter() - started, 4), 'adapter_accepted': accepted,
                  'stops': len(data.get('places', [])), 'photos_with_valid_uris': sum(bool(p.get('photo')) for p in data.get('places', [])),
                  'food_stops': sum(p.get('itinerary_kind') == 'food' for p in data.get('places', [])),
                  'weather_status': itinerary.get('weather_status'), 'duration_hours': itinerary.get('constraints', {}).get('duration_hours'),
                  'new_calls': {key: counts[key] - before[key] for key in counts}}
        records.append(record)
        print(json.dumps(record), flush=True)
        if not verified:
            break
    output = {'performed_at': datetime.now(timezone.utc).isoformat(), 'boundary': 'Flask test client -> live Groq/Places/Open-Meteo -> actual frontend adapter. No device test or civic writes. Photo URIs obtained, images not displayed on device.', 'counts': counts, 'cases': records}
    path = Path(__file__).parent / 'evaluation/prewhatsapp_live.json'
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))
        output['previous_attempts'] = [*old.get('previous_attempts', []), {key: value for key, value in old.items() if key != 'previous_attempts'}]
    path.write_text(json.dumps(output, indent=2), encoding='utf-8')
    return 0 if len(records) == 3 and all(item['verified'] for item in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
