"""Durable civic acceptance tests. Every test uses an isolated temporary DB."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from PIL import Image, PngImagePlugin

from civic_media import MAX_UPLOAD_BYTES, sanitize_image, MediaError
from civic_routes import register_civic
from civic_service import CivicService, CivicError, identity_hash

TOKEN_A = 'a' * 48
TOKEN_B = 'b' * 48
OWNER_A = identity_hash('Bearer ' + TOKEN_A)
OWNER_B = identity_hash('Bearer ' + TOKEN_B)
HEADERS_A = {'Authorization': 'Bearer ' + TOKEN_A}
HEADERS_B = {'Authorization': 'Bearer ' + TOKEN_B}


def report(**changes):
    return {'idempotency_key': 'test-submission-0001', 'category': 'pothole',
            'description': 'A large pothole beside the entrance.', 'location_text': '12th Main, Indiranagar, Bengaluru',
            'latitude': 12.98, 'longitude': 77.64, 'location_confirmed': True,
            'public': True, 'photo_public': False, **changes}


def photo(format='PNG'):
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('GPS', 'private coordinates')
    image = Image.new('RGB', (60, 40), 'red')
    image.save(output, format=format, **({'pnginfo': metadata} if format == 'PNG' else {}))
    return output.getvalue()


class CivicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'civic.sqlite3'
        self.service = CivicService(self.path)

    def test_lazy_schema_and_restart_durability(self):
        self.assertFalse(self.path.exists())
        saved = self.service.create_report(OWNER_A, report())
        fresh = CivicService(self.path).get_report(saved['id'], OWNER_A)
        self.assertEqual(fresh['id'], saved['id'])
        self.assertEqual(fresh['status_label'], 'Reported to Smart Nagrik')
        self.assertEqual(fresh['history'][0]['source'], 'Smart Nagrik submission')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_record_survives_separate_process_open(self):
        saved = self.service.create_report(OWNER_A, report())
        script = "from civic_service import CivicService; import sys; r=CivicService(sys.argv[1]).get_report(sys.argv[2]); print(r['id'])"
        result = subprocess.run([sys.executable, '-c', script, str(self.path), saved['id']], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), saved['id'])

    def test_report_retry_returns_same_record_and_conflict_rejected(self):
        first = self.service.create_report(OWNER_A, report())
        again = self.service.create_report(OWNER_A, report())
        self.assertFalse(first['replayed'])
        self.assertTrue(again['replayed'])
        self.assertEqual(first['id'], again['id'])
        with self.assertRaisesRegex(CivicError, 'different content'):
            self.service.create_report(OWNER_A, report(description='Changed description'))
        self.assertEqual(len(self.service.list_reports()['items']), 1)

    def test_same_idempotency_key_is_scoped_to_owner(self):
        a = self.service.create_report(OWNER_A, report())
        b = self.service.create_report(OWNER_B, report())
        self.assertNotEqual(a['id'], b['id'])

    def test_concurrent_duplicate_submissions_commit_one_report(self):
        # Initialise schema before contention, not database content.
        self.service.list_reports()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.create_report(OWNER_A, report()), range(4)))
        self.assertEqual(len({result['id'] for result in results}), 1)
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM status_history').fetchone()[0], 1)

    def test_private_report_requires_owner_not_public_id(self):
        saved = self.service.create_report(OWNER_A, report(public=False))
        for identity in (None, OWNER_B):
            with self.assertRaises(CivicError) as caught:
                self.service.get_report(saved['id'], identity)
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.service.list_reports()['items'], [])
        self.assertEqual(self.service.list_reports(OWNER_B, mine=True)['items'], [])
        self.assertEqual(self.service.list_reports(OWNER_A, mine=True)['items'][0]['id'], saved['id'])
        public = json.dumps(self.service.get_report(saved['id'], OWNER_A))
        self.assertNotIn(OWNER_A, public)
        self.assertNotIn(TOKEN_A, public)
        self.assertNotIn('fingerprint', public)

    def test_coords_require_confirmation_and_do_not_invent_a_point(self):
        with self.assertRaises(CivicError) as caught:
            self.service.create_report(OWNER_A, report(location_confirmed=False))
        self.assertEqual(caught.exception.code, 'location_confirmation_required')
        saved = self.service.create_report(OWNER_A, report(latitude=None, longitude=None, location_confirmed=False))
        self.assertIsNone(saved['latitude'])
        self.assertIsNone(saved['longitude'])
        self.assertFalse(saved['location_confirmed'])
        self.assertEqual(self.service.list_reports(latitude=12.98, longitude=77.64)['items'], [])

    def test_input_and_bengaluru_bounds_are_validated(self):
        invalid = [{'description': ''}, {'category': 'official_alert'}, {'latitude': float('nan')},
                   {'latitude': True}, {'longitude': None}, {'latitude': 28.6}, {'public': 'true'},
                   {'location_text': ''}, {'idempotency_key': '../../bad'}, {'description': 'a' * 2001},
                   {'photo_public': True, 'public': False}]
        for fields in invalid:
            with self.subTest(fields=fields):
                with self.assertRaises(CivicError):
                    self.service.create_report(OWNER_A, report(**fields))
        self.assertFalse(self.path.exists())

    def test_geographic_category_and_area_filters_are_bounded(self):
        near = self.service.create_report(OWNER_A, report())
        self.service.create_report(OWNER_A, report(idempotency_key='test-submission-0002', category='garbage', latitude=13.1, longitude=77.7, location_text='Yelahanka'))
        results = self.service.list_reports(latitude=12.98, longitude=77.64, radius_km=2)
        self.assertEqual([r['id'] for r in results['items']], [near['id']])
        self.assertEqual(len(self.service.list_reports(area='Indiranagar', category='pothole')['items']), 1)
        self.assertEqual(self.service.list_reports(area='%')['items'], [])
        self.assertEqual(self.service.list_reports(area='Unknown place')['items'], [])
        with self.assertRaises(CivicError):
            self.service.list_reports(limit=1000)
        with self.assertRaises(CivicError):
            self.service.list_reports(latitude=12.98, longitude=77.64, radius_km=100)

    def test_support_is_persistent_idempotent_and_reversible(self):
        saved = self.service.create_report(OWNER_A, report())
        for _ in range(3):
            data = self.service.set_support(saved['id'], OWNER_B, True)
            self.assertEqual(data['support_count'], 1)
            self.assertTrue(data['supported'])
        fresh = CivicService(self.path).get_report(saved['id'], OWNER_B)
        self.assertTrue(fresh['supported'])
        self.assertFalse(self.service.get_report(saved['id'], OWNER_A)['supported'])
        self.service.set_support(saved['id'], OWNER_A, True)
        self.assertEqual(self.service.get_report(saved['id'])['support_count'], 2)
        self.service.set_support(saved['id'], OWNER_B, False)
        data = self.service.set_support(saved['id'], OWNER_B, False)
        self.assertEqual(data['support_count'], 1)
        self.assertFalse(data['supported'])

    def test_support_requires_credential_boolean_and_public_record(self):
        saved = self.service.create_report(OWNER_A, report(public=False))
        for identity, state in ((None, True), (OWNER_B, 'true'), (OWNER_B, True)):
            with self.assertRaises(CivicError):
                self.service.set_support(saved['id'], identity, state)

    def test_post_persists_and_references_same_report_support(self):
        saved = self.service.create_report(OWNER_A, report())
        payload = {'text': 'Please take care around this issue.', 'report_id': saved['id'], 'public': True, 'idempotency_key': 'post-submission-0001'}
        first = self.service.create_post(OWNER_A, payload)
        again = self.service.create_post(OWNER_A, payload)
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['replayed'])
        self.service.set_support(saved['id'], OWNER_B, True)
        feed = CivicService(self.path).list_posts(OWNER_B)
        self.assertEqual(feed['items'][0]['report']['id'], saved['id'])
        self.assertEqual(feed['items'][0]['report']['support_count'], 1)
        self.assertTrue(feed['items'][0]['report']['supported'])
        self.assertEqual(len(self.service.list_posts(area='Indiranagar')['items']), 1)
        self.assertEqual(self.service.list_posts(area='Unknown')['items'], [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)

    def test_public_post_cannot_publish_a_private_report(self):
        saved = self.service.create_report(OWNER_A, report(public=False))
        with self.assertRaises(CivicError) as caught:
            self.service.create_post(OWNER_A, {'text': 'Publish this', 'public': True, 'report_id': saved['id'], 'idempotency_key': 'post-submission-0001'})
        self.assertEqual(caught.exception.code, 'private_report')
        self.assertEqual(self.service.list_posts()['items'], [])

    def test_page_cursor_and_private_posts(self):
        for index in range(3):
            self.service.create_post(OWNER_A, {'text': f'Observation number {index}', 'idempotency_key': f'post-submission-{index:04}', 'public': index != 2})
        first = self.service.list_posts(limit=1)
        self.assertEqual(first['next_offset'], 1)
        second = self.service.list_posts(limit=1, offset=1)
        self.assertIsNone(second['next_offset'])
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertEqual(len(self.service.list_posts(OWNER_A, mine=True)['items']), 3)
        self.assertEqual(self.service.list_posts(OWNER_B, mine=True)['items'], [])

    def test_photo_publication_requires_separate_consent(self):
        saved = self.service.create_report(OWNER_A, report(), photo())
        media_id = saved['photo_url'].rsplit('/', 1)[-1]
        self.assertIsNone(self.service.get_report(saved['id'])['photo_url'])
        with self.assertRaises(CivicError):
            self.service.get_media(media_id, OWNER_B)
        content, mime = self.service.get_media(media_id, OWNER_A)
        self.assertEqual(mime, 'image/jpeg')
        image = Image.open(BytesIO(content))
        self.assertEqual(image.size, (60, 40))
        self.assertEqual(dict(image.getexif()), {})
        self.assertNotIn('GPS', image.info)
        published = self.service.create_report(OWNER_A, report(idempotency_key='test-submission-0002', photo_public=True), photo())
        self.assertTrue(self.service.get_media(published['photo_url'].rsplit('/', 1)[-1])[0])

    def test_invalid_photo_leaves_no_report_or_media(self):
        with self.assertRaises(CivicError):
            self.service.create_report(OWNER_A, report(), b'not-an-image')
        self.assertFalse(self.path.exists())

    def test_atomic_rollback_includes_media(self):
        with patch.object(self.service, '_report', side_effect=sqlite3.OperationalError('fixture failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.create_report(OWNER_A, report(), photo())
        with closing(sqlite3.connect(self.path)) as db:
            for table in ('reports', 'media', 'status_history'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)


class CivicHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config['MAX_CONTENT_LENGTH'] = 8192
        self.resolver = Mock()
        self.service = register_civic(self.app, Path(self.temp.name) / 'test.sqlite3', resolver=self.resolver)
        self.client = self.app.test_client()

    def test_real_http_write_and_history_envelopes(self):
        response = self.client.post('/v1/civic/reports', json=report(), headers=HEADERS_A)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json['success'])
        reference = response.json['data']['id']
        again = self.client.post('/v1/civic/reports', json=report(), headers=HEADERS_A)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json['data']['id'], reference)
        history = self.client.get('/v1/civic/my-reports', headers=HEADERS_A)
        self.assertEqual(history.json['data']['items'][0]['id'], reference)
        support = self.client.post(f'/v1/civic/reports/{reference}/support', json={'supported': True}, headers=HEADERS_B)
        self.assertEqual(support.json['data']['support_count'], 1)
        self.assertEqual(support.headers['Cache-Control'], 'no-store')

    def test_http_image_upload_and_access(self):
        response = self.client.post('/v1/civic/reports', headers=HEADERS_A, data={'payload': json.dumps(report()), 'photo': (BytesIO(photo()), '../../wrong.exe')})
        self.assertEqual(response.status_code, 201)
        url = response.json['data']['photo_url']
        self.assertEqual(self.client.get(url).status_code, 404)
        permitted = self.client.get(url, headers=HEADERS_A)
        self.assertEqual(permitted.status_code, 200)
        self.assertEqual(permitted.content_type, 'image/jpeg')
        self.assertEqual(permitted.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('wrong', permitted.headers['Content-Disposition'])

    def test_oversized_upload_and_invalid_credentials_are_rejected(self):
        response = self.client.post('/v1/civic/reports', headers=HEADERS_A, content_type='application/json', data=b'x' * (MAX_UPLOAD_BYTES + 100000))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post('/v1/civic/reports', json=report()).status_code, 401)
        self.assertEqual(self.client.get('/v1/civic/my-reports', headers={'Authorization': 'Bearer short'}).status_code, 401)

    def test_photo_larger_than_original_chat_limit_is_accepted(self):
        image = Image.effect_noise((500, 500), 80).convert('RGB')
        output = BytesIO()
        image.save(output, format='PNG')
        content = output.getvalue()
        self.assertGreater(len(content), 65536)
        response = self.client.post('/v1/civic/reports', headers=HEADERS_A, data={'payload': json.dumps(report()), 'photo': (BytesIO(content), 'photo.png')})
        self.assertEqual(response.status_code, 201, response.json)

    def test_json_shape_filter_and_database_error_handling(self):
        self.assertEqual(self.client.post('/v1/civic/reports', json=[], headers=HEADERS_A).status_code, 422)
        self.assertEqual(self.client.get('/v1/civic/reports?latitude=bad&longitude=77.6').status_code, 422)
        self.assertEqual(self.client.get('/v1/civic/reports?limit=1000').status_code, 422)
        with patch.object(self.service, 'create_report', side_effect=sqlite3.OperationalError('secret path')):
            response = self.client.post('/v1/civic/reports', json=report(), headers=HEADERS_A)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret path', response.get_data(as_text=True))

    def test_location_resolution_requires_user_confirmation_and_does_not_save(self):
        self.resolver.resolve.return_value = {'status': 'ambiguous', 'original_text': 'Example', 'place': None, 'candidates': []}
        response = self.client.post('/v1/civic/resolve-location', json={'text': 'Example'})
        self.assertTrue(response.json['data']['confirmation_required'])
        self.assertEqual(response.json['data']['status'], 'ambiguous')
        self.assertFalse(self.service.store.path.exists())

    def test_post_http_public_feed_and_my_posts(self):
        response = self.client.post('/v1/community/posts', headers=HEADERS_A, json={'text': 'A local observation', 'public': True, 'idempotency_key': 'post-submission-0001'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get('/v1/community/posts').json['data']['items'][0]['id'], response.json['data']['id'])
        self.assertEqual(self.client.get('/v1/community/posts?mine=true', headers=HEADERS_B).json['data']['items'], [])


class ImageTests(unittest.TestCase):
    def test_supported_formats_are_decoded_and_transcoded(self):
        for format in ('JPEG', 'PNG', 'WEBP'):
            content = sanitize_image(photo(format))
            self.assertEqual(Image.open(BytesIO(content)).format, 'JPEG')

    def test_truncated_svg_gif_oversized_are_rejected(self):
        for content in (photo()[:25], b'<svg onload="bad"/>', photo('GIF'), b'x' * (MAX_UPLOAD_BYTES + 1)):
            with self.assertRaises(MediaError):
                sanitize_image(content)

    def test_exif_gps_metadata_not_retained(self):
        image = Image.new('RGB', (10, 10), 'blue')
        metadata = Image.Exif()
        metadata[270] = 'private identifying details'
        metadata[274] = 6
        stream = BytesIO()
        image.save(stream, format='JPEG', exif=metadata)
        clean = Image.open(BytesIO(sanitize_image(stream.getvalue())))
        self.assertEqual(dict(clean.getexif()), {})
        self.assertNotIn('exif', clean.info)


if __name__ == '__main__':
    unittest.main()
