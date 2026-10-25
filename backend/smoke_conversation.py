"""Opt-in bounded live 04.1.2 acceptance; never part of unit discovery.

At most four Groq calls, twelve photo-free Places resolutions, one weather fetch.
Successful context reuse normally reduces Places requests to seven.
Artifacts contain counts/status/timing only, not Google listing content or keys.
"""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from time import perf_counter

from app import create_app
from groq_provider import GroqIntentProvider
from places_service import PlacesService
from service import process_text
from weather_service import WeatherService


def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    if not all(os.getenv(key, '').strip() for key in ('GROQ_API_KEY', 'GOOGLE_PLACES_API_KEY')):
        raise SystemExit('Missing server configuration; no live requests performed.')
    counts = {'groq': 0, 'resolutions': 0}
    class Provider(GroqIntentProvider):
        def extract(self, *args):
            counts['groq'] += 1
            return super().extract(*args)
    class Places(PlacesService):
        def resolve(self, text):
            counts['resolutions'] += 1
            return super().resolve(text)
    provider, places, weather = Provider(), Places(), WeatherService()
    client = create_app(processor=lambda req: process_text(req, provider, weather, places)).test_client()
    session = None
    records = []
    cases = [
        ('route', 'I need to go from Indiranagar to Lalbagh.', False),
        ('mode_metro', 'By metro', True),
        ('mode_walk', "Actually I'll walk", True),
        ('destination', 'Change destination to Cubbon Park', True),
        ('other_route', 'How do I get from HSR Layout to Whitefield?', False),
        ('demo_route', 'How do I get from Majestic to MG Road by metro?', False),
        ('weather', 'Will it rain in Bengaluru this evening?', False),
    ]
    for label, message, follow in cases:
        before = dict(counts)
        started = perf_counter()
        response = client.post('/v1/chat', json={'message': message, 'city': 'Bengaluru', 'follow_up': follow, **({'session_id': session} if session else {})})
        data = response.json.get('data') or {}
        session = data.get('session_id', session)
        section = next(iter(data.get('sections', [])), {})
        understanding = data.get('understanding', {})
        locations = section.get('resolved_locations', {})
        verified = response.status_code == 200 and data.get('status') == 'ok'
        if label != 'weather':
            verified = verified and len(data.get('map_markers', [])) == 2 and all(value['status'] == 'resolved' for value in locations.values())
        if label == 'mode_walk':
            verified = verified and understanding.get('origin') == 'Indiranagar' and understanding.get('destination') == 'Lalbagh' and section.get('travel_mode') == 'walking'
        if label == 'destination':
            verified = verified and understanding.get('origin') == 'Indiranagar' and understanding.get('destination') == 'Cubbon Park'
        record = {'case': label, 'http_status': response.status_code, 'status': data.get('status'), 'verified': verified,
                  'endpoint_seconds': round(perf_counter() - started, 4), 'new_calls': {key: counts[key] - before[key] for key in counts},
                  'resolution_statuses': {key: value['status'] for key, value in locations.items()},
                  'marker_count': len(data.get('map_markers', [])), 'mode': section.get('travel_mode'),
                  'demo_labelled': bool(section.get('demo', {}).get('simulated')) if section.get('demo') else False,
                  'geometry_present': section.get('route_geometry') is not None,
                  'summary_characters': len(data.get('summary', ''))}
        records.append(record)
        print(json.dumps(record), flush=True)
        if data.get('status', '').startswith('intent_') or not understanding:
            break  # No dependent requests after a failed extraction.
    report = {'performed_at': datetime.now(timezone.utc).isoformat(), 'boundary': 'Flask test client -> real Groq/Places/Open-Meteo; no device or WhatsApp test', 'counts': counts, 'cases': records}
    path = Path(__file__).parent / 'evaluation' / 'milestone0412_live.json'
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))
        report['previous_attempts'] = [*old.get('previous_attempts', []), {key: value for key, value in old.items() if key != 'previous_attempts'}]
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if all(record['verified'] for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
