"""Tourism tests use deliberately synthetic Places/weather data only."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from flask import Flask

from city_http import CityServiceError
from maps_service import directions_url
from tourism_routes import ItineraryStore, register_tourism
from tourism_service import constraints_for, plan_itinerary, tourism_quick_replies


SOURCE = {'name': 'Google Maps', 'url': 'https://maps.google.com/', 'retrieved_at': '2026-10-25T00:00:00Z'}


def record(identifier, name=None, lat=12.98, photo=True):
    return {'id': identifier, 'name': name or identifier, 'address': 'Synthetic street, Bengaluru',
            'category': 'tourism', 'location': {'latitude': lat, 'longitude': 77.6}, 'source': 'Google Maps',
            'retrieved_at': SOURCE['retrieved_at'], 'source_url': 'https://maps.google.com/?cid=123',
            'directions_url': directions_url(identifier, place_id=identifier), 'image': None,
            'photo': {'url': 'https://lh3.googleusercontent.com/' + identifier, 'source_url': None,
                      'authors': [{'name': 'Synthetic photographer', 'uri': None, 'avatar_url': None}]} if photo else None,
            'attributions': []}


def parsed(text, **values):
    result = {'primary_intent': 'tourism', 'original_text': text, 'city': 'Bengaluru', 'preferences': [],
              'secondary_intents': [], 'accessibility_constraints': [], 'origin': None, 'area': None, 'landmark': None,
              'duration': None, 'time_constraint': None, 'transport_preference': None, 'budget': None}
    result.update(values)
    return result


class FakePlaces:
    def __init__(self):
        self.calls = []
        self.resolutions = []

    def search(self, intent, category, limit=5):
        self.calls.append((deepcopy(intent), category, limit))
        topic = 'nature' if 'nature' in intent.get('preferences', []) else 'heritage' if 'heritage' in intent.get('preferences', []) else 'general'
        records = [record(f'{topic}-{index}', ('Synthetic Museum' if index == 0 and topic == 'heritage' else f'{topic} synthetic stop {index}'), 12.98 + index / 100) for index in range(5)]
        if category == 'food_places':
            records = [record('food', 'Synthetic restaurant')]
        return {'places': records[:limit], 'source': SOURCE, 'rejected_records': 0}

    def resolve(self, text):
        self.resolutions.append(text)
        return {'status': 'resolved', 'original_text': text, 'confidence': 'name_match', 'place': record(text, photo=False)}


class FakeWeather:
    def __init__(self, chance=70):
        self.calls = []
        self.chance = chance

    def clock(self):
        return datetime(2026, 10, 25, 2, tzinfo=timezone.utc)

    def forecast(self, phrase):
        self.calls.append(phrase)
        return {'source': {'name': 'Open-Meteo', 'url': 'https://open-meteo.com/'}, 'precipitation_probability_max': self.chance}


class TourismTests(unittest.TestCase):
    def setUp(self):
        self.places = FakePlaces()
        self.weather = FakeWeather()

    def test_half_day_and_full_day_have_different_manageable_stop_counts(self):
        half = plan_itinerary(parsed('Plan a half-day visit'), self.places)
        full = plan_itinerary(parsed('Plan a one-day visit'), self.places)
        self.assertEqual(len(half['places']), 3)
        self.assertEqual(len(full['places']), 5)
        self.assertEqual(full['itinerary']['constraints']['duration_hours'], 8)
        self.assertLessEqual(sum(stop['visit_minutes'] for stop in full['itinerary']['stops']), 8 * 60)

    def test_nature_and_heritage_change_provider_queries_and_real_selected_records(self):
        nature = plan_itinerary(parsed('Six hours focused on nature'), self.places)
        heritage = plan_itinerary(parsed('Six hours of heritage and history'), self.places)
        self.assertNotEqual(nature['places'][0]['id'], heritage['places'][0]['id'])
        self.assertIn('nature', self.places.calls[0][0]['preferences'])
        self.assertIn('heritage', self.places.calls[1][0]['preferences'])

    def test_food_preference_is_search_input_not_menu_fact(self):
        result = plan_itinerary(parsed('A day with temples and vegetarian food'), self.places)
        food = next(place for place in result['places'] if place['itinerary_kind'] == 'food')
        self.assertEqual(food['id'], 'food')
        self.assertEqual(food['category'], 'tourism')
        self.assertIn('vegetarian', self.places.calls[1][0]['preferences'])
        self.assertIn('unverified', food['planning_reason'])
        self.assertEqual(len(self.places.calls), 2)

    def test_budget_and_limited_walking_reduce_transfers_without_price_claims(self):
        result = plan_itinerary(parsed('One day under 1500 INR, avoid too much walking', budget={'amount': 1500, 'currency': 'INR', 'raw_text': '1500 INR'}), self.places)
        self.assertLessEqual(len(result['places']), 3)
        self.assertFalse(result['itinerary']['constraints']['budget_verified'])
        self.assertIn('cannot confirm', result['description'])
        self.assertIn('walking distances', result['description'])

    def test_shortening_reuses_places_and_preserves_id_origin_and_preferences(self):
        first = plan_itinerary(parsed('Six hours of nature from Jayanagar', origin='Jayanagar', preferences=['vegetarian']), self.places)
        second = plan_itinerary(parsed('Make it shorter. I only have three hours.'), self.places, previous={'sections': [first]})
        self.assertEqual(second['itinerary']['id'], first['itinerary']['id'])
        self.assertEqual(second['itinerary']['constraints']['origin'], 'Jayanagar')
        self.assertEqual(second['itinerary']['constraints']['duration_hours'], 3)
        self.assertEqual(second['itinerary']['constraints']['preferences'], ['vegetarian'])
        self.assertEqual(len(second['places']), 2)
        self.assertEqual(len(self.places.calls), 1)
        self.assertEqual(self.places.resolutions, ['Jayanagar'])

    def test_added_destination_is_resolved_and_included_without_fake_photo(self):
        first = plan_itinerary(parsed('Six hours sightseeing'), self.places)
        second = plan_itinerary(parsed('Add Lalbagh.'), self.places, previous=first)
        stop = next(place for place in second['places'] if place['id'] == 'Lalbagh')
        self.assertIsNone(stop['photo'])
        self.assertIn('destination_place_id=Lalbagh', stop['directions_url'])
        self.assertEqual(self.places.resolutions, ['Lalbagh'])

    def test_remove_and_replace_retain_other_valid_stops(self):
        first = plan_itinerary(parsed('Six hours of heritage'), self.places)
        removed = plan_itinerary(parsed('Remove the museum.'), self.places, previous=first)
        self.assertFalse(any('museum' in place['name'].lower() for place in removed['places']))
        replaced = plan_itinerary(parsed('Replace the museum with Lalbagh'), self.places, previous=first)
        self.assertIn('Lalbagh', [place['id'] for place in replaced['places']])
        self.assertFalse(any('museum' in place['name'].lower() for place in replaced['places']))

    def test_more_nature_and_rainy_afternoon_retrieve_relevant_candidates(self):
        first = plan_itinerary(parsed('A day of heritage'), self.places)
        second = plan_itinerary(parsed('More nature'), self.places, previous=first)
        self.assertEqual(second['itinerary']['constraints']['interests'], ['nature'])
        third = plan_itinerary(parsed('Make it suitable for a rainy afternoon'), self.places, self.weather, second)
        self.assertTrue(third['itinerary']['constraints']['indoor_preferred'])
        self.assertIn('indoor attractions and museums for a rainy visit', self.places.calls[-1][0]['preferences'])
        self.assertEqual(self.weather.calls, ['today afternoon'])

    def test_future_date_does_not_request_or_invent_weather(self):
        result = plan_itinerary(parsed('A day of nature on 2027-01-01'), self.places, self.weather)
        self.assertEqual(self.weather.calls, [])
        self.assertEqual(result['itinerary']['weather_status'], 'unsupported_range')
        self.assertIsNone(result['itinerary']['weather'])

    def test_weather_failure_and_food_failure_preserve_valid_attractions(self):
        self.weather.forecast = Mock(side_effect=CityServiceError('timeout'))
        search = self.places.search
        self.places.search = lambda intent, category, limit: (_ for _ in ()).throw(CityServiceError('timeout')) if category == 'food_places' else search(intent, category, limit)
        result = plan_itinerary(parsed('Six hours and food'), self.places, self.weather)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('food search was unavailable', result['description'])
        self.assertEqual(result['itinerary']['weather_status'], 'timeout')

    def test_missing_origin_never_invents_current_location_and_no_geometry(self):
        result = plan_itinerary(parsed('I am here and want sightseeing'), self.places)
        self.assertEqual(self.places.resolutions, [])
        self.assertIsNone(result['itinerary']['origin_resolution'])
        self.assertIn('starting area', result['summary'])
        self.assertIsNone(result['itinerary']['route_geometry'])
        self.assertEqual(result['itinerary']['ordering'], 'straight_line_heuristic')

    def test_order_ids_photos_attribution_and_visit_estimates_are_consistent(self):
        result = plan_itinerary(parsed('Six hours from Jayanagar', origin='Jayanagar'), self.places)
        for order, place in enumerate(result['places'], 1):
            self.assertEqual(place['itinerary_order'], order)
            self.assertEqual(result['itinerary']['stops'][order - 1]['place_id'], place['id'])
            self.assertEqual(place['source'], 'Google Maps')
            self.assertEqual(place['photo']['authors'][0]['name'], 'Synthetic photographer')
            self.assertIn('planning estimates', result['description'])
        self.assertNotIn('opening_hours', result['places'][0])
        self.assertNotIn('price', result['places'][0])

    def test_group_count_mode_and_new_starting_area_are_retained(self):
        first = plan_itinerary(parsed('A six hour trip for 3 people by metro from Indiranagar', origin='Indiranagar', transport_preference='metro'), self.places)
        changed = plan_itinerary(parsed('Change area to Jayanagar', origin='Indiranagar', area='Jayanagar'), self.places, previous=first)
        self.assertEqual(changed['itinerary']['constraints']['origin'], 'Jayanagar')
        self.assertEqual(changed['itinerary']['constraints']['group_size'], 3)
        self.assertEqual(changed['itinerary']['constraints']['travel_mode'], 'metro')
        self.assertIn('travelmode=transit', changed['places'][0]['directions_url'])

    def test_no_results_and_ambiguous_added_stop_do_not_fabricate(self):
        self.places.search = lambda *args: {'places': [], 'source': SOURCE}
        self.places.resolve = lambda text: {'status': 'ambiguous', 'place': None}
        result = plan_itinerary(parsed('Add Central Museum'), self.places)
        self.assertEqual(result['status'], 'no_results')
        self.assertEqual(result['places'], [])
        self.assertIn('more precisely', result['description'])

    def test_quick_replies_have_real_operations(self):
        replies = tourism_quick_replies()
        self.assertEqual([tile['id'] for tile in replies], ['tourism_shorter', 'tourism_food', 'tourism_nature', 'change_area'])
        first = plan_itinerary(parsed('A day of sightseeing'), self.places)
        shorter = plan_itinerary(parsed(replies[0]['label']), self.places, previous=first)
        self.assertLess(shorter['itinerary']['constraints']['duration_hours'], first['itinerary']['constraints']['duration_hours'])


class TourismRouteTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0]
        self.store = ItineraryStore(capacity=2, ttl=10, clock=lambda: self.clock[0])
        self.app = Flask(__name__)
        register_tourism(self.app, FakePlaces(), FakeWeather(), self.store)
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + 'a' * 40}

    def test_authorised_create_get_refine_retains_identifier(self):
        response = self.client.post('/v1/tourism/itineraries', headers=self.headers, json={'constraints': {'duration_hours': 6, 'origin': 'Jayanagar', 'interests': ['nature'], 'include_food': True, 'preferences': ['vegetarian'], 'group_size': 2}})
        self.assertEqual(response.status_code, 201)
        data = response.json['data']
        identifier = data['itinerary']['id']
        self.assertFalse(data['persistent'])
        self.assertEqual(self.client.get('/v1/tourism/itineraries/' + identifier, headers=self.headers).status_code, 200)
        refined = self.client.post('/v1/tourism/itineraries/' + identifier + '/refine', headers=self.headers, json={'constraints': {'duration_hours': 3}})
        self.assertEqual(refined.status_code, 200)
        self.assertEqual(refined.json['data']['itinerary']['id'], identifier)
        self.assertEqual(refined.json['data']['itinerary']['constraints']['origin'], 'Jayanagar')
        self.assertEqual(refined.json['data']['itinerary']['constraints']['duration_hours'], 3)

    def test_other_installation_and_missing_token_cannot_read_or_refine(self):
        data = self.client.post('/v1/tourism/itineraries', headers=self.headers, json={'message': 'Six hours sightseeing'}).json['data']
        path = '/v1/tourism/itineraries/' + data['itinerary']['id']
        self.assertEqual(self.client.get(path).status_code, 401)
        other = {'Authorization': 'Bearer ' + 'b' * 40}
        self.assertEqual(self.client.get(path, headers=other).status_code, 404)
        self.assertEqual(self.client.post(path + '/refine', headers=other, json={'message': 'Shorter plan'}).status_code, 404)

    def test_expiry_and_bounded_store(self):
        for _ in range(2):
            self.assertEqual(self.client.post('/v1/tourism/itineraries', headers=self.headers, json={'message': 'Six hours sightseeing'}).status_code, 201)
        self.assertEqual(self.client.post('/v1/tourism/itineraries', headers=self.headers, json={'message': 'Six hours sightseeing'}).status_code, 503)
        identifier = next(iter(self.store.entries))
        self.clock[0] = 11
        self.assertEqual(self.client.get('/v1/tourism/itineraries/' + identifier, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post('/v1/tourism/itineraries', headers=self.headers, json={'message': 'Six hours sightseeing'}).status_code, 201)

    def test_invalid_constraints_are_rejected(self):
        for body in ({'constraints': {'duration_hours': True}}, {'constraints': {'duration_hours': 24}}, {'constraints': {'origin': ''}}, {'constraints': {'budget': {'amount': -1, 'currency': 'INR'}}}, {'constraints': {'group_size': 0}}, {'constraints': {'execute': 'arbitrary'}}, {'message': ['bad']}):
            self.assertEqual(self.client.post('/v1/tourism/itineraries', headers=self.headers, json=body).status_code, 422)


if __name__ == '__main__':
    unittest.main()
