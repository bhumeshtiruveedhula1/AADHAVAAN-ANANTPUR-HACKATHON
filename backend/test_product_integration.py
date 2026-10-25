"""Chat/workflow integration against temp SQLite and mocked city providers."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import create_app
from conversation import SessionStore
from service import process_text
from test_civic import HEADERS_A, HEADERS_B, report, OWNER_A
from test_intents import extraction
from test_tourism import FakePlaces, FakeWeather


class ProductIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'SMART_NAGRIK_DATA_DIR': self.temp.name})
        env.start(); self.addCleanup(env.stop)
        sessions = patch('service.SESSIONS', SessionStore())
        sessions.start(); self.addCleanup(sessions.stop)
        self.places, self.weather, self.provider = FakePlaces(), FakeWeather(), Mock()
        self.app = create_app(processor=lambda req: process_text(req, self.provider, self.weather, self.places))
        self.client = self.app.test_client()

    def chat(self, message, fields=None, headers=HEADERS_A, **options):
        if fields is not None:
            self.provider.extract.return_value = fields
        response = self.client.post('/v1/chat', json={'message': message, 'city': 'Bengaluru', **options}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['data']

    def test_civic_chat_is_draft_not_submission_and_prefills_existing_form(self):
        data = self.chat("There's a pothole outside my college", extraction('civic_report', civic_category='pothole'))
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['report_draft']['category'], 'pothole')
        self.assertIn('Nothing has been submitted', data['summary'])
        self.assertEqual(data['quick_replies'][0]['id'], 'open_report')
        self.assertFalse((Path(self.temp.name) / 'civic.sqlite3').exists())

    def test_submit_lookup_support_and_feed_share_same_record(self):
        saved = self.client.post('/v1/civic/reports', json=report(longitude=77.6), headers=HEADERS_A).json['data']
        self.client.post(f"/v1/civic/reports/{saved['id']}/support", json={'supported': True}, headers=HEADERS_B)
        data = self.chat('Show potholes near Indiranagar', extraction('civic_lookup', area='Indiranagar', civic_category='pothole'), headers=HEADERS_B)
        self.assertEqual(data['reports'][0]['id'], saved['id'])
        self.assertEqual(data['map_markers'][0]['report_id'], saved['id'])
        self.assertTrue(data['reports'][0]['supported'])
        self.assertEqual(data['map_markers'][0]['support_count'], 1)
        self.assertEqual(data['map_markers'][0]['source'], 'Smart Nagrik community')
        history = self.client.get('/v1/civic/my-reports', headers=HEADERS_A).json['data']['items']
        self.assertEqual(history[0]['support_count'], 1)

    def test_text_only_report_has_no_marker_and_private_report_does_not_leak(self):
        public = self.client.post('/v1/civic/reports', json=report(latitude=None, longitude=None, location_confirmed=False), headers=HEADERS_A).json['data']
        private = self.client.post('/v1/civic/reports', json=report(idempotency_key='private-report-1234', public=False), headers=HEADERS_A).json['data']
        data = self.chat('Status of ' + public['id'], extraction('civic_lookup', report_id=public['id']))
        self.assertEqual(data['reports'][0]['id'], public['id'])
        self.assertEqual(data['map_markers'], [])
        hidden = self.chat('Status of ' + private['id'], extraction('civic_lookup', report_id=private['id']), headers=HEADERS_B)
        self.assertEqual(hidden['status'], 'not_found')
        self.assertEqual(hidden['reports'], [])

    def test_no_reports_is_not_a_safety_claim_and_near_me_asks_area(self):
        data = self.chat('Issues around Jayanagar', extraction('civic_lookup', area='Jayanagar'))
        self.assertIn('No reports found in Smart Nagrik', data['summary'])
        self.assertIn('does not mean', data['summary'])
        data = self.chat('Issues near me', extraction('civic_lookup'))
        self.assertEqual(data['status'], 'clarification_required')
        self.assertEqual(data['map_markers'], [])

    def test_private_session_cannot_be_reused_by_another_installation(self):
        first = self.chat('I need to report a pothole', extraction('civic_report'))
        other = self.chat('Continue', headers=HEADERS_B, follow_up=True, session_id=first['session_id'])
        self.assertEqual(other['status'], 'session_owner_mismatch')
        self.assertNotIn('understanding', other)

    def test_tourism_chat_refinements_and_authorised_plan_reference(self):
        first = self.chat('Plan six hours of heritage and vegetarian food around Indiranagar', extraction('tourism', area='Indiranagar', duration='six hours', preferences=['heritage', 'vegetarian'], secondary_intents=['food_places']))
        section = first['sections'][0]
        self.assertEqual(len(first['sections']), 1)
        self.assertTrue(any(place['itinerary_kind'] == 'food' for place in section['places']))
        identifier = section['itinerary']['id']
        self.assertEqual(self.client.get('/v1/tourism/itineraries/' + identifier, headers=HEADERS_A).status_code, 200)
        self.assertEqual(self.client.get('/v1/tourism/itineraries/' + identifier, headers=HEADERS_B).status_code, 404)
        second = self.chat('Shorter plan', session_id=first['session_id'], follow_up=True, quick_reply='tourism_shorter')
        self.assertEqual(second['sections'][0]['itinerary']['id'], identifier)
        self.assertEqual(second['sections'][0]['itinerary']['constraints']['duration_hours'], 3)
        self.assertEqual(len(second['places']), 2)
        added = self.chat('Add Lalbagh', session_id=first['session_id'], follow_up=True)
        self.assertTrue(any(place['id'] == 'Lalbagh' for place in added['places']))
        self.provider.extract.assert_called_once()

    def test_status_without_reference_requests_authorised_history(self):
        data = self.chat("What's the status of my pothole report?", extraction('civic_lookup'))
        self.assertEqual(data['reports'], [])
        self.assertIn('My reports', data['summary'])


if __name__ == '__main__':
    unittest.main()
