"""HTTP adapter for reusable civic operations. No changes to messaging workers."""
import json
import sqlite3

from flask import Blueprint, current_app, jsonify, request, Response
from werkzeug.exceptions import HTTPException

from city_http import CityServiceError
from civic_media import MAX_UPLOAD_BYTES
from civic_service import CivicError, CivicService, identity_hash
from places_service import PlacesService


def register_civic(app, db_path=None, media_root=None, resolver=None):
    # media_root retained only as an integration compatibility argument. Images
    # are transactionally stored in SQLite, not a publicly served file folder.
    service = CivicService(db_path)
    app.extensions['civic_service'] = service
    locations = resolver or PlacesService()
    routes = Blueprint('civic', __name__)

    def answer(data=None, error=None, status=200):
        response = jsonify({'success': error is None, 'data': data, 'error': error})
        response.status_code = status
        response.headers['Cache-Control'] = 'no-store'
        return response

    @routes.before_request
    def upload_limits():
        # Override the existing 8KB limit ONLY for image-bearing civic writes.
        # Limits for /v1/chat and Twilio remain unchanged.
        request.max_content_length = (MAX_UPLOAD_BYTES + 64 * 1024) if request.endpoint in {'civic.create_report', 'civic.create_post'} else 8192
        # Multipart decoding uses 64KB chunks even for streamed file fields.
        # The text payload is separately capped below rather than limiting the
        # decoder buffer so aggressively that ordinary photos are rejected.
        request.max_form_memory_size = 256 * 1024
        request.max_form_parts = 4

    @routes.errorhandler(CivicError)
    def civic_error(error):
        return answer(error={'code': error.code, 'message': error.message}, status=error.status)

    @routes.errorhandler(sqlite3.Error)
    def storage_error(error):
        # Never log SQL, payloads, credentials or local database paths.
        current_app.logger.error('Civic storage operation failed')
        return answer(error={'code': 'storage_unavailable', 'message': 'Your change could not be confirmed. Retry using the same submission ID.'}, status=503)

    @routes.errorhandler(OSError)
    def file_error(error):
        return storage_error(error)

    @routes.errorhandler(HTTPException)
    def http_error(error):
        return answer(error={'code': 'upload_too_large' if error.code == 413 else 'invalid_request',
                             'message': 'Photographs must be no larger than 5 MB.' if error.code == 413 else 'The request could not be read.'}, status=error.code or 400)

    def owner(required=False):
        return identity_hash(request.headers.get('Authorization'), required)

    def body_and_photo():
        photo = None
        if request.mimetype == 'multipart/form-data':
            if (set(request.files) - {'photo'} or set(request.form) - {'payload'} or
                    len(request.files.getlist('photo')) > 1 or len(request.form.getlist('payload')) != 1):
                raise CivicError('invalid_input', 'Send one payload and optional photo.')
            if len(request.form['payload']) > 16 * 1024:
                raise CivicError('invalid_input', 'The submission text is too large.')
            try:
                body = json.loads(request.form.get('payload', ''))
            except (ValueError, TypeError):
                raise CivicError('invalid_input', 'payload must contain a JSON object.') from None
            upload = request.files.get('photo')
            if upload:
                photo = upload.stream.read(MAX_UPLOAD_BYTES + 1)
        elif request.is_json:
            body = request.get_json(silent=True)
        else:
            raise CivicError('invalid_content_type', 'Send JSON or a multipart payload with an optional photo.', 415)
        if not isinstance(body, dict):
            raise CivicError('invalid_input', 'Send a JSON object.')
        return body, photo

    def page_args():
        return {'limit': request.args.get('limit', 20), 'offset': request.args.get('offset', 0), 'area': request.args.get('area')}

    @routes.post('/v1/civic/reports')
    def create_report():
        identity = owner(True)
        body, photo = body_and_photo()
        result = service.create_report(identity, body, photo)
        return answer(result, status=200 if result['replayed'] else 201)

    @routes.get('/v1/civic/reports/<report_id>')
    def get_report(report_id):
        return answer(service.get_report(report_id, owner()))

    @routes.get('/v1/civic/reports')
    def list_reports():
        args = page_args()
        for key in ('latitude', 'longitude', 'radius_km'):
            if key in request.args:
                try:
                    args[key] = float(request.args[key])
                except ValueError:
                    raise CivicError('invalid_location', 'Invalid geographic filter.') from None
        return answer(service.list_reports(owner=owner(), category=request.args.get('category'), **args))

    @routes.get('/v1/civic/my-reports')
    def my_reports():
        return answer(service.list_reports(owner=owner(True), mine=True, **page_args()))

    @routes.post('/v1/civic/reports/<report_id>/support')
    def support(report_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise CivicError('invalid_support', 'Send a supported boolean.')
        return answer(service.set_support(report_id, owner(True), body.get('supported')))

    @routes.post('/v1/community/posts')
    def create_post():
        identity = owner(True)
        body, photo = body_and_photo()
        result = service.create_post(identity, body, photo)
        return answer(result, status=200 if result['replayed'] else 201)

    @routes.get('/v1/community/posts')
    def posts():
        mine = request.args.get('mine') == 'true'
        return answer(service.list_posts(owner=owner(mine), mine=mine, **page_args()))

    @routes.get('/v1/civic/media/<identifier>')
    def media(identifier):
        content, mime = service.get_media(identifier, owner())
        return Response(content, mimetype=mime, headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                                       'Content-Disposition': f'inline; filename="{identifier}.jpg"'})

    @routes.post('/v1/civic/resolve-location')
    def resolve_location():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('text'), str) or not 3 <= len(body['text'].strip()) <= 200:
            raise CivicError('invalid_location', 'Supply a specific location of 3 to 200 characters.')
        try:
            result = locations.resolve(body['text'].strip())
        except CityServiceError as error:
            return answer(error={'code': error.code, 'message': 'Location lookup is unavailable. You may save a text-only report without a map marker.'}, status=503)
        # Resolution is a suggestion. The client must explicitly confirm the
        # exact incident position; a neighbourhood centre is not an exact issue.
        return answer({**result, 'confirmation_required': True,
                       'guidance': 'Confirm that this is the exact issue position. A locality centre alone is not an exact issue location.'})

    app.register_blueprint(routes)
    return service
