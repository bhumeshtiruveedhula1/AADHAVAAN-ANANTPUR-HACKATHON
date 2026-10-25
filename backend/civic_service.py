"""Reusable civic operations. Anonymous installation credentials are not accounts."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4

from civic_media import MediaError, sanitize_image
from civic_store import CivicStore

CATEGORIES = {'pothole', 'streetlight', 'garbage', 'road_damage', 'waterlogging', 'other'}


class CivicError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def identity_hash(authorization, required=False):
    if not authorization:
        if required:
            raise CivicError('identity_required', 'This operation needs your installation credential.', 401)
        return None
    if not isinstance(authorization, str) or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{32,128}', authorization):
        raise CivicError('invalid_identity', 'Invalid installation credential.', 401)
    return hashlib.sha256(authorization[7:].encode('ascii')).hexdigest()


def text(value, label, minimum=1, maximum=2000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise CivicError('invalid_input', f'{label} must contain {minimum} to {maximum} characters.')
    if any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise CivicError('invalid_input', f'{label} contains unsupported control characters.')
    return value.strip()


def flag(body, name, default=False):
    value = body.get(name, default)
    if type(value) is not bool:
        raise CivicError('invalid_input', f'{name} must be a boolean.')
    return value


def coordinates(latitude, longitude):
    if latitude is None and longitude is None:
        return None, None
    if any(type(value) not in (float, int) or not math.isfinite(value) for value in (latitude, longitude)):
        raise CivicError('invalid_location', 'Supply both latitude and longitude as finite numbers.')
    if not (12.8 <= latitude <= 13.2 and 77.4 <= longitude <= 77.8):
        raise CivicError('unsupported_location', 'The selected position is outside the supported Bengaluru area.')
    return float(latitude), float(longitude)


def required_owner(owner):
    if not isinstance(owner, str) or not re.fullmatch(r'[a-f0-9]{64}', owner):
        raise CivicError('identity_required', 'This operation needs your installation credential.', 401)


def pagination(limit, offset):
    try:
        if isinstance(limit, bool) or isinstance(offset, bool):
            raise ValueError()
        limit, offset = int(limit), int(offset)
    except (TypeError, ValueError):
        raise CivicError('invalid_page', 'Invalid feed page.') from None
    if not 1 <= limit <= 25 or not 0 <= offset <= 10000:
        raise CivicError('invalid_page', 'Use a limit of 1 to 25 and offset of 0 to 10000.')
    return limit, offset


class CivicService:
    def __init__(self, db_path=None, clock=None):
        self.store = CivicStore(db_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _photo(photo):
        if photo is None:
            return None
        try:
            return sanitize_image(photo)
        except MediaError as error:
            raise CivicError('invalid_photo', str(error)) from None

    @staticmethod
    def _submission(body, owner):
        required_owner(owner)
        if not isinstance(body, dict):
            raise CivicError('invalid_input', 'Send a JSON object.')
        key = body.get('idempotency_key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
            raise CivicError('invalid_idempotency_key', 'Supply a stable submission ID of 16 to 128 letters, digits, underscores or hyphens.')
        return key

    @staticmethod
    def _fingerprint(values, photo):
        return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode('utf-8') + (photo or b'')).hexdigest()

    @staticmethod
    def _existing(db, table, owner, key, fingerprint):
        row = db.execute(f'SELECT * FROM {table} WHERE owner=? AND idempotency_key=?', (owner, key)).fetchone()
        if row and row['fingerprint'] != fingerprint:
            raise CivicError('idempotency_conflict', 'This submission ID already saved different content. Start a new submission.', 409)
        return row

    @staticmethod
    def _media(db, owner, record_type, record_id, photo):
        if photo is None:
            return None
        identifier = uuid4().hex
        db.execute('INSERT INTO media VALUES (?,?,?,?,?,?)', (identifier, owner, record_type, record_id, photo, 'image/jpeg'))
        return identifier

    def create_report(self, owner, body, photo=None):
        key = self._submission(body, owner)
        category = body.get('category')
        if not isinstance(category, str) or category not in CATEGORIES:
            raise CivicError('invalid_category', 'Choose pothole, streetlight, garbage, road_damage, waterlogging or other.')
        description = text(body.get('description'), 'Description', 5, 2000)
        location = text(body.get('location_text'), 'Location', 3, 300)
        latitude, longitude = coordinates(body.get('latitude'), body.get('longitude'))
        confirmed = flag(body, 'location_confirmed')
        if latitude is not None and not confirmed:
            raise CivicError('location_confirmation_required', 'Confirm this exact position before saving a map marker.')
        if confirmed and latitude is None:
            raise CivicError('invalid_location', 'A confirmed map position requires coordinates.')
        public, photo_public = flag(body, 'public'), flag(body, 'photo_public')
        if photo_public and not public:
            raise CivicError('invalid_consent', 'A private report cannot publish its photograph.')
        photo = self._photo(photo)
        values = [category, description, location, latitude, longitude, confirmed, public, photo_public]
        fingerprint = self._fingerprint(values, photo)
        with self.store.connection(write=True) as db:
            old = self._existing(db, 'reports', owner, key, fingerprint)
            if old:
                return {**self._report(db, old, owner), 'replayed': True}
            identifier, now = 'SN-' + uuid4().hex, self.clock()
            media_id = self._media(db, owner, 'report', identifier, photo)
            db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, owner, key, fingerprint, category, description, location, latitude, longitude,
                        int(confirmed), int(public), int(photo_public), media_id, 'reported', now, now))
            db.execute('INSERT INTO status_history(report_id,status,created_at,source) VALUES (?,?,?,?)',
                       (identifier, 'reported', now, 'Smart Nagrik submission'))
            row = db.execute('SELECT * FROM reports WHERE id=?', (identifier,)).fetchone()
            result = {**self._report(db, row, owner), 'replayed': False}
        return result

    def _report(self, db, row, owner=None):
        owns = bool(owner and row['owner'] == owner)
        count = db.execute('SELECT count(*) FROM support WHERE report_id=?', (row['id'],)).fetchone()[0]
        supported = bool(owner and db.execute('SELECT 1 FROM support WHERE report_id=? AND supporter=?', (row['id'], owner)).fetchone())
        photo = row['media_id'] if owns or row['public'] and row['photo_public'] else None
        history = [dict(item) for item in db.execute('SELECT status,created_at,source FROM status_history WHERE report_id=? ORDER BY id', (row['id'],))]
        return {'id': row['id'], 'category': row['category'], 'description': row['description'],
                'location_text': row['location_text'], 'latitude': row['latitude'], 'longitude': row['longitude'],
                'location_confirmed': bool(row['location_confirmed']), 'public': bool(row['public']),
                'status': row['status'], 'status_label': 'Reported to Smart Nagrik',
                'created_at': row['created_at'], 'updated_at': row['updated_at'],
                'support_count': count, 'supported': supported, 'is_owner': owns,
                'photo_url': '/v1/civic/media/' + photo if photo else None, 'history': history}

    @staticmethod
    def _permitted(row, owner):
        if row is None or not (row['public'] or owner and row['owner'] == owner):
            raise CivicError('not_found', 'That record is unavailable.', 404)

    def get_report(self, report_id, owner=None):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
            self._permitted(row, owner)
            return self._report(db, row, owner)

    def list_reports(self, owner=None, area=None, latitude=None, longitude=None, radius_km=3, category=None, limit=20, offset=0, mine=False):
        limit, offset = pagination(limit, offset)
        clauses, params = ['public=1'], []
        if mine:
            required_owner(owner)
            clauses, params = ['owner=?'], [owner]
        if area:
            area = text(area, 'Area', 1, 200)
            clauses.append("location_text LIKE ? ESCAPE '\\'")
            params.append('%' + area.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if category is not None:
            if category not in CATEGORIES:
                raise CivicError('invalid_category', 'Unknown issue category.')
            clauses.append('category=?')
            params.append(category)
        latitude, longitude = coordinates(latitude, longitude)
        if latitude is not None:
            if type(radius_km) not in (int, float) or not math.isfinite(radius_km) or not 0.1 <= radius_km <= 10:
                raise CivicError('invalid_radius', 'Use a search radius from 0.1 to 10 kilometres.')
            lat_delta = radius_km / 111
            lon_delta = radius_km / (111 * math.cos(math.radians(latitude)))
            clauses += ['location_confirmed=1', 'latitude BETWEEN ? AND ?', 'longitude BETWEEN ? AND ?']
            params += [latitude - lat_delta, latitude + lat_delta, longitude - lon_delta, longitude + lon_delta]
        with self.store.connection() as db:
            rows = db.execute('SELECT * FROM reports WHERE ' + ' AND '.join(clauses) + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*params, limit + 1, offset)).fetchall()
            return {'items': [self._report(db, row, owner) for row in rows[:limit]],
                    'next_offset': offset + limit if len(rows) > limit else None,
                    'geographic_filter': 'bounding_box' if latitude is not None else 'reported_location_text' if area else None}

    def set_support(self, report_id, owner, supported):
        required_owner(owner)
        if type(supported) is not bool:
            raise CivicError('invalid_support', 'supported must be true or false.')
        with self.store.connection(write=True) as db:
            row = db.execute('SELECT * FROM reports WHERE id=? AND public=1', (report_id,)).fetchone()
            if row is None:
                raise CivicError('not_found', 'That public report is unavailable.', 404)
            if supported:
                db.execute('INSERT OR IGNORE INTO support VALUES (?,?,?)', (report_id, owner, self.clock()))
            else:
                db.execute('DELETE FROM support WHERE report_id=? AND supporter=?', (report_id, owner))
            result = self._report(db, row, owner)
        return result

    def create_post(self, owner, body, photo=None):
        key = self._submission(body, owner)
        content = text(body.get('text'), 'Post text', 3, 2000)
        location = text(body.get('location_text', ''), 'Location', 0, 300)
        public, photo_public = flag(body, 'public'), flag(body, 'photo_public')
        if photo_public and not public:
            raise CivicError('invalid_consent', 'A private post cannot publish its photograph.')
        report_id = body.get('report_id')
        if report_id is not None and (not isinstance(report_id, str) or not re.fullmatch(r'SN-[a-f0-9]{32}', report_id)):
            raise CivicError('invalid_report', 'Supply a valid report reference.')
        photo = self._photo(photo)
        fingerprint = self._fingerprint([content, location, report_id, public, photo_public], photo)
        with self.store.connection(write=True) as db:
            old = self._existing(db, 'posts', owner, key, fingerprint)
            if old:
                return {**self._post(db, old, owner), 'replayed': True}
            if report_id:
                linked = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
                self._permitted(linked, owner)
                if public and not linked['public']:
                    raise CivicError('private_report', 'A public post cannot expose a private report.', 403)
            identifier, now = 'SP-' + uuid4().hex, self.clock()
            media_id = self._media(db, owner, 'post', identifier, photo)
            db.execute('INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, owner, key, fingerprint, content, location, report_id, int(public), int(photo_public), media_id, now))
            row = db.execute('SELECT * FROM posts WHERE id=?', (identifier,)).fetchone()
            result = {**self._post(db, row, owner), 'replayed': False}
        return result

    def _post(self, db, row, owner=None):
        owns = bool(owner and row['owner'] == owner)
        photo = row['media_id'] if owns or row['public'] and row['photo_public'] else None
        linked = db.execute('SELECT * FROM reports WHERE id=?', (row['report_id'],)).fetchone() if row['report_id'] else None
        report = self._report(db, linked, owner) if linked and (linked['public'] or owns and linked['owner'] == owner) else None
        return {'id': row['id'], 'text': row['text'], 'location_text': row['location_text'],
                'created_at': row['created_at'], 'public': bool(row['public']), 'is_owner': owns,
                'photo_url': '/v1/civic/media/' + photo if photo else None, 'report_id': report['id'] if report else None, 'report': report}

    def list_posts(self, owner=None, area=None, limit=20, offset=0, mine=False):
        limit, offset = pagination(limit, offset)
        clauses, params = ['p.public=1'], []
        if mine:
            required_owner(owner)
            clauses, params = ['p.owner=?'], [owner]
        if area:
            area = text(area, 'Area', 1, 200)
            clauses.append("(p.location_text LIKE ? ESCAPE '\\' OR r.location_text LIKE ? ESCAPE '\\')")
            term = '%' + area.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            params += [term, term]
        with self.store.connection() as db:
            rows = db.execute('SELECT p.* FROM posts p LEFT JOIN reports r ON p.report_id=r.id WHERE ' + ' AND '.join(clauses) + ' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?', (*params, limit + 1, offset)).fetchall()
            return {'items': [self._post(db, row, owner) for row in rows[:limit]], 'next_offset': offset + limit if len(rows) > limit else None}

    def get_media(self, identifier, owner=None):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise CivicError('not_found', 'That photograph is unavailable.', 404)
        with self.store.connection() as db:
            media = db.execute('SELECT * FROM media WHERE id=?', (identifier,)).fetchone()
            if media is None:
                raise CivicError('not_found', 'That photograph is unavailable.', 404)
            table = 'reports' if media['record_type'] == 'report' else 'posts'
            record = db.execute(f'SELECT * FROM {table} WHERE id=?', (media['record_id'],)).fetchone()
            if not record or not (owner and record['owner'] == owner or record['public'] and record['photo_public']):
                raise CivicError('not_found', 'That photograph is unavailable.', 404)
            return bytes(media['content']), media['content_type']
