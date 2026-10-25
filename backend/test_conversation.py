"""04.1.2 regressions: synthetic coordinates, mocked providers, no live traffic."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from app import create_app
from city_http import CityServiceError
from city_services import compose, weather_section
from conversation import SessionStore, SessionError, merge_followup, quick_replies
from places_service import PlacesService
from service import TextRequest, process_text
from test_city_services import intent, place_payload, forecast_payload, NOW
from test_intents import extraction
from transport_demo import demo_connection
from weather_service import WeatherService

PAIRS = [('Majestic', 'MG Road'), ('HSR Layout', 'Whitefield'), ('Indiranagar', 'Lalbagh'),
         ('Jayanagar', 'Cubbon Park'), ('Yelahanka', 'Hebbal'), ('Koramangala', 'Electronic City')]


def resolver():
    calls = []
    def respond(request):
        query = json.loads(request.content)['textQuery'].removesuffix(', Bengaluru')
        calls.append(query)
        payload = place_payload(query.replace(' ', '-'), query)
        # Deliberately synthetic, distinguishable fixture coordinates.
        payload['places'][0]['location'] = {'latitude': 12.91 + len(calls) / 1000, 'longitude': 77.61}
        return httpx.Response(200, json=payload)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    return PlacesService(client, api_key='synthetic-key', clock=lambda: NOW), calls, client


class LocationTests(unittest.TestCase):
    def test_six_arbitrary_pairs_resolve_independently_of_static_route_coverage(self):
        service, calls, client = resolver()
        self.addCleanup(client.close)
        for origin, destination in PAIRS:
            with self.subTest(origin=origin):
                parsed = intent('transport', f'From {origin} to {destination}', origin=origin, destination=destination)
                result = compose(parsed, places=service)
                section = result['sections'][0]
                self.assertEqual([m['original_text'] for m in result['map_markers']], [origin, destination])
                self.assertEqual([m['marker_type'] for m in result['map_markers']], ['origin', 'destination'])
                self.assertTrue(all(r['status'] == 'resolved' for r in section['resolved_locations'].values()))
                self.assertIsNone(section['route_geometry'])
                params = parse_qs(urlsplit(section['directions_url']).query)
                self.assertEqual(params['destination_place_id'], [destination.replace(' ', '-')])
                self.assertEqual(params['origin_place_id'], [origin.replace(' ', '-')])
                self.assertEqual(params['travelmode'], ['transit'])
                if origin != 'Majestic':
                    self.assertIsNone(section['demo'])
        self.assertEqual(calls, [name for pair in PAIRS for name in pair])

    def test_ambiguous_matches_never_choose_first_and_preserve_candidates(self):
        payload = place_payload('a', 'Central Hospital')
        payload['places'] += place_payload('b', 'Central Hospital')['places']
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            result = PlacesService(client, api_key='test').resolve('Central Hospital')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['place'])
        self.assertEqual(len(result['candidates']), 2)

    def test_vague_location_never_calls_provider_or_creates_coordinates(self):
        client = Mock()
        service = PlacesService(client, api_key='test')
        for text in (None, 'here', 'near me', 'my location'):
            self.assertEqual(service.resolve(text)['status'], 'missing_location')
        client.request.assert_not_called()

    def test_unique_locality_address_disambiguates_same_named_subareas(self):
        payload = place_payload('locality', 'HSR Layout')
        payload['places'][0]['formattedAddress'] = 'HSR Layout, Bengaluru, Karnataka'
        payload['places'] += place_payload('subarea', 'HSR Layout')['places']
        payload['places'][1]['formattedAddress'] = 'HSR Layout, New Thippasandra, Bengaluru, Karnataka'
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            data = PlacesService(client, api_key='test').resolve('HSR Layout')
        self.assertEqual(data['status'], 'resolved')
        self.assertEqual(data['place']['id'], 'locality')
        self.assertEqual(data['confidence'], 'locality_address_match')

    def test_missing_key_and_timeout_keep_real_external_handoff_without_markers(self):
        parsed = intent('transport', 'Walking from HSR Layout to Whitefield', origin='HSR Layout', destination='Whitefield', transport_preference='Walking')
        for service in (PlacesService(api_key=''), Mock(resolve=Mock(side_effect=CityServiceError('timeout')))):
            result = compose(parsed, places=service)
            self.assertEqual(result['map_markers'], [])
            self.assertIn('travelmode=walking', result['sections'][0]['directions_url'])

    def test_no_origin_is_not_replaced_by_city_centre(self):
        service, calls, client = resolver()
        self.addCleanup(client.close)
        result = compose(intent('transport', 'To Lalbagh', destination='Lalbagh'), places=service)
        self.assertEqual(calls, ['Lalbagh'])
        self.assertEqual(len(result['map_markers']), 1)
        self.assertIn('starting point', result['summary'])
        self.assertNotIn('origin=', result['sections'][0]['directions_url'])

    def test_here_is_not_sent_as_a_fake_known_origin(self):
        service, calls, client = resolver()
        self.addCleanup(client.close)
        result = compose(intent('transport', 'From here to Lalbagh', origin='here', destination='Lalbagh'), places=service)
        self.assertEqual(calls, ['Lalbagh'])
        self.assertNotIn('origin=', result['sections'][0]['directions_url'])
        self.assertIn('starting point', result['summary'])


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.sessions = patch('service.SESSIONS', self.store)
        self.sessions.start()
        self.addCleanup(self.sessions.stop)
        self.places, self.calls, client = resolver()
        self.addCleanup(client.close)
        self.provider = Mock()

    def send(self, message, session=None, follow=False, quick=None, payload=None):
        if payload is not None:
            self.provider.extract.return_value = payload
        return process_text(TextRequest(message, 'Bengaluru', 'android', session_ref=session, use_session=True, follow_up=follow, quick_reply=quick), self.provider, places=self.places)

    def start(self):
        return self.send('From Indiranagar to Lalbagh', payload=extraction('transport', origin='Indiranagar', destination='Lalbagh'))

    def test_mode_switches_preserve_endpoints_reuse_coordinates_and_skip_llm(self):
        first = self.start()
        second = self.send('By metro', first['session_id'], True)
        third = self.send("Actually I'll walk", first['session_id'], True)
        self.assertEqual(third['understanding']['origin'], 'Indiranagar')
        self.assertEqual(third['understanding']['destination'], 'Lalbagh')
        self.assertEqual(third['sections'][0]['travel_mode'], 'walking')
        self.assertEqual(self.calls, ['Indiranagar', 'Lalbagh'])
        self.provider.extract.assert_called_once()
        self.assertEqual(second['session_id'], third['session_id'])

    def test_destination_edit_changes_only_destination_and_resolves_only_new_place(self):
        first = self.start()
        changed = self.send('Change destination to Cubbon Park', first['session_id'], True)
        self.assertEqual(changed['understanding']['origin'], 'Indiranagar')
        self.assertEqual(changed['understanding']['destination'], 'Cubbon Park')
        destinations = [entity['value'] for entity in changed['understanding']['entities'] if entity['type'] == 'destination']
        self.assertEqual(destinations, ['Cubbon Park'])
        self.assertEqual(self.calls, ['Indiranagar', 'Lalbagh', 'Cubbon Park'])
        self.provider.extract.assert_called_once()

    def test_alternative_followup_gets_controlled_history_and_retains_route(self):
        first = self.start()
        result = self.send('Another option?', first['session_id'], True, payload=extraction('transport'))
        context = self.provider.extract.call_args.args[2]
        self.assertLessEqual(len(context['recent_messages']), 3)
        self.assertIn('Indiranagar', str(context))
        self.assertEqual(result['understanding']['destination'], 'Lalbagh')
        self.assertFalse(result['understanding']['clarification_needed'])

    def test_fresh_request_has_no_old_route_or_preferences(self):
        first = self.start()
        result = self.send('Find food in Koramangala', first['session_id'], payload=extraction('food_places', area='Koramangala'))
        self.assertIsNone(result['understanding']['origin'])
        self.assertIsNone(result['understanding']['destination'])
        self.assertEqual(self.provider.extract.call_args.args[2]['recent_messages'], [])

    def test_new_category_in_followup_does_not_inherit_travel_entities(self):
        old = intent('transport', 'From Indiranagar to Lalbagh by metro', origin='Indiranagar', destination='Lalbagh', transport_preference='metro')
        new = deepcopy(old)
        new.update(primary_intent='weather_conditions', original_text='Will it rain?', method='groq')
        result = merge_followup(new, old)
        self.assertIsNone(result['origin'])
        self.assertIsNone(result['destination'])
        self.assertIsNone(result['transport_preference'])
        self.assertEqual(result['preferences'], [])

    def test_sessions_are_independent_and_unknown_id_does_not_fixate_session(self):
        first = self.start()
        second = self.send('From Yelahanka to Hebbal', payload=extraction('transport', origin='Yelahanka', destination='Hebbal'))
        changed = self.send('Walk', second['session_id'], True)
        self.assertNotEqual(first['session_id'], second['session_id'])
        self.assertEqual(changed['understanding']['origin'], 'Yelahanka')
        other = self.send('By metro', 'x' * 32, True, payload=extraction('transport', transport_preference='metro'))
        self.assertNotEqual(other['session_id'], 'x' * 32)
        self.assertIsNone(other['understanding']['origin'])
        self.assertEqual(other['status'], 'clarification_required')

    def test_expiry_capacity_channel_isolation_and_concurrent_turn_rejection(self):
        clock = [0]
        store = SessionStore(capacity=1, ttl=10, clock=lambda: clock[0])
        with store.turn('android') as (identifier, entry):
            entry['result'] = {'test': True}
            with self.assertRaisesRegex(SessionError, 'session_busy'):
                with store.turn('android', identifier):
                    pass
            with self.assertRaisesRegex(SessionError, 'session_capacity'):
                with store.turn('whatsapp', identifier):
                    pass
        clock[0] = 11
        with store.turn('android', identifier) as (fresh, entry):
            self.assertNotEqual(fresh, identifier)
            self.assertIsNone(entry['result'])

    def test_unoffered_quick_reply_is_rejected_without_provider_calls(self):
        first = self.start()
        result = self.send('Tomorrow', first['session_id'], True, 'weather_tomorrow')
        self.assertEqual(result['status'], 'invalid_quick_reply')
        self.provider.extract.assert_called_once()

    def test_quick_mode_action_and_api_session_validation(self):
        first = self.start()
        result = self.send('Walk', first['session_id'], True, 'mode_walk')
        self.assertEqual(result['sections'][0]['travel_mode'], 'walking')
        app = create_app(processor=Mock()).test_client()
        for extra in ({'session_id': 'bad'}, {'follow_up': 'yes'}, {'quick_reply': 'execute_code'}):
            self.assertEqual(app.post('/v1/chat', json={'message': 'Hi', 'city': 'Bengaluru', **extra}).status_code, 422)

    def test_user_paneer_preference_drives_search_without_menu_claim(self):
        first = self.send('I love paneer. Find food near Indiranagar', payload=extraction('food_places', area='Indiranagar', preferences=['paneer']))
        self.assertIn('paneer', [tile['id'] for tile in first['quick_replies']])
        next_result = self.send('Search paneer options', first['session_id'], True, 'paneer')
        self.assertIn('paneer', self.calls[-1])
        self.assertNotIn('served here', next_result['description'])
        self.assertIn('not verified', str(next_result['details']))


class SituationTests(unittest.TestCase):
    def test_rain_dry_uncertain_summaries_use_actual_forecast_values(self):
        payload = forecast_payload()
        for chance, amount, expected in ((70, .2, 'likely'), (10, 0, 'mostly dry'), (40, .1, 'chance')):
            payload['hourly']['precipitation_probability'] = [chance] * 168
            payload['hourly']['precipitation'] = [amount] * 168
            with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
                section = weather_section(WeatherService(client, clock=lambda: NOW).forecast('this evening'))
            self.assertIn(expected, section['summary'])
            self.assertLess(len(section['summary']), 140)
            self.assertEqual(len(section['weather']['hourly']), 4)
            self.assertEqual(section['places'], [])

    def test_static_demo_is_bounded_sourced_and_explicitly_not_live(self):
        for origin, destination, mode in [('Majestic', 'MG Road', 'metro'), ('Jayanagar', 'Lalbagh', 'metro'), ('Hebbal Bus Stop', 'Nagavara Cross', 'bus')]:
            first = demo_connection(origin, destination, mode)
            self.assertEqual(first, demo_connection(origin, destination, mode))
            self.assertTrue(first['simulated'])
            self.assertTrue(first['source'].startswith('https://'))
            self.assertIn('Demo / Simulated', first['notice'])
            self.assertIn('not live', first['notice'])
            self.assertEqual(first['frequency_minutes'], 5)
        self.assertIsNone(demo_connection('HSR Layout', 'Whitefield', 'metro'))
        self.assertIsNone(demo_connection('Majestic', 'MG Road', 'walking'))

    def test_tiles_are_contextual_not_civic_dead_buttons(self):
        for category, expected in [('accommodation', 'food_nearby'), ('weather_conditions', 'weather_tomorrow'), ('transport', 'mode_walk')]:
            result = {'status': 'ok', 'understanding': {'primary_intent': category}}
            self.assertIn(expected, [tile['id'] for tile in quick_replies(result)])
        self.assertEqual([tile['id'] for tile in quick_replies({'status': 'ok', 'understanding': {'primary_intent': 'civic_report'}})], ['open_report', 'my_reports'])


if __name__ == '__main__':
    unittest.main()
