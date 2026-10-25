"""Optional authorised, ephemeral itinerary referencing; no permanent Google cache."""
from copy import deepcopy
from hashlib import sha256
import re
from threading import RLock
from time import monotonic

from flask import Blueprint, request

from city_http import CityServiceError
from places_service import PlacesService
from tourism_service import plan_itinerary
from weather_service import WeatherService


class TourismInputError(ValueError):
    pass


class ItineraryStore:
    """Runtime-only plans, same 15-minute bound as chat context; no disk history."""
    def __init__(self, capacity=128, ttl=900, clock=monotonic):
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.entries = {}
        self.lock = RLock()

    def _expire(self):
        now = self.clock()
        for identifier in list(self.entries):
            if now - self.entries[identifier]['used'] >= self.ttl:
                del self.entries[identifier]

    def get(self, identifier, owner):
        with self.lock:
            self._expire()
            value = self.entries.get(identifier)
            if not value or value['owner'] != owner:
                return None
            value['used'] = self.clock()
            return deepcopy(value)

    def save(self, owner, intent, section):
        identifier = section['itinerary']['id']
        with self.lock:
            self._expire()
            existing = self.entries.get(identifier)
            if existing and existing['owner'] != owner:
                raise TourismInputError('Itinerary is unavailable.')
            if not existing and len(self.entries) >= self.capacity:
                raise CityServiceError('busy')
            self.entries[identifier] = {'owner': owner, 'used': self.clock(), 'intent': deepcopy(intent), 'section': deepcopy(section)}


def _owner():
    value = request.headers.get('Authorization', '')
    match = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{32,128})', value)
    return sha256(match[1].encode()).hexdigest() if match else None


def _intent(body, old=None):
    if not isinstance(body, dict) or set(body) - {'message', 'constraints'}:
        raise TourismInputError('Send message and/or supported constraints.')
    message = body.get('message', '')
    if not isinstance(message, str) or len(message) > 2000:
        raise TourismInputError('message must be at most 2000 characters.')
    constraints = body.get('constraints', {})
    if not isinstance(constraints, dict) or set(constraints) - {'duration_hours', 'origin', 'date_time', 'interests', 'preferences', 'include_food', 'budget', 'travel_mode', 'group_size', 'limited_walking'}:
        raise TourismInputError('Unsupported itinerary constraints.')
    if not message.strip() and not constraints:
        raise TourismInputError('Supply a sightseeing request or constraint.')
    intent = deepcopy(old) if old else {'primary_intent': 'tourism', 'secondary_intents': [], 'city': 'Bengaluru', 'origin': None, 'area': None,
                                      'landmark': None, 'preferences': [], 'accessibility_constraints': [], 'budget': None, 'duration': None,
                                      'transport_preference': None, 'original_text': '', 'time_constraint': None}
    pieces = [message.strip()]
    for name in ('origin', 'date_time', 'travel_mode'):
        if name in constraints:
            value = constraints[name]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 150:
                raise TourismInputError(name + ' must be a short nonempty string.')
            target = {'origin': 'origin', 'date_time': 'time_constraint', 'travel_mode': 'transport_preference'}[name]
            intent[target] = value.strip()
            pieces.append(value.strip())
    for name in ('interests', 'preferences'):
        if name in constraints:
            values = constraints[name]
            if not isinstance(values, list) or len(values) > 8 or any(not isinstance(value, str) or not 1 <= len(value) <= 80 for value in values):
                raise TourismInputError(name + ' must contain at most eight short strings.')
            intent['preferences'] = list(dict.fromkeys([*intent.get('preferences', []), *values]))[:8]
            pieces.extend(values)
    if 'duration_hours' in constraints:
        value = constraints['duration_hours']
        if type(value) not in (int, float) or not 1 <= value <= 12:
            raise TourismInputError('duration_hours must be 1 to 12.')
        intent['duration'] = f'{value:g} hours'
        pieces.insert(0, intent['duration'])
    if 'group_size' in constraints:
        value = constraints['group_size']
        if type(value) is not int or not 1 <= value <= 50:
            raise TourismInputError('group_size must be 1 to 50.')
        pieces.append(f'{value} people')
    for name in ('include_food', 'limited_walking'):
        if name in constraints:
            if type(constraints[name]) is not bool:
                raise TourismInputError(name + ' must be a boolean.')
            if name == 'include_food':
                pieces.append('include food' if constraints[name] else 'no food')
            elif constraints[name]:
                pieces.append('avoid too much walking')
    if 'budget' in constraints:
        value = constraints['budget']
        if not isinstance(value, dict) or set(value) - {'amount', 'currency'} or type(value.get('amount')) not in (int, float) or not 0 < value['amount'] <= 1_000_000 or value.get('currency') != 'INR':
            raise TourismInputError('budget requires a positive amount and INR currency.')
        intent['budget'] = {**value, 'raw_text': f"{value['amount']:g} INR", 'constraint': 'maximum'}
    intent['original_text'] = '. '.join(pieces)
    return intent


def register_tourism(app, places=None, weather=None, store=None):
    places = places or PlacesService()
    weather = weather or WeatherService()
    store = store or ItineraryStore()
    app.extensions['tourism_store'] = store
    bp = Blueprint('tourism', __name__)

    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    def response(data=None, code=None, message=None, status=200):
        return {'success': code is None, 'data': data, 'error': {'code': code, 'message': message} if code else None}, status

    def build(owner, previous=None, identifier=None):
        if not request.is_json:
            return response(code='invalid_content_type', message='Send JSON.', status=415)
        try:
            intent = _intent(request.get_json(silent=True), previous['intent'] if previous else None)
            section = plan_itinerary(intent, places, weather, previous['section'] if previous else None, identifier)
            store.save(owner, intent, section)
            return response({**section, 'expires_in': store.ttl, 'persistent': False}, status=200 if previous else 201)
        except TourismInputError as error:
            return response(code='invalid_itinerary', message=str(error), status=422)
        except CityServiceError as error:
            return response(code=error.code, message='The sightseeing provider could not complete this request.', status=503)

    @bp.post('/v1/tourism/itineraries')
    def create():
        owner = _owner()
        if not owner:
            return response(code='installation_required', message='An installation token is required.', status=401)
        return build(owner)

    @bp.get('/v1/tourism/itineraries/<identifier>')
    def get(identifier):
        owner = _owner()
        if not owner:
            return response(code='installation_required', message='An installation token is required.', status=401)
        value = store.get(identifier, owner)
        if not value:
            return response(code='itinerary_not_found', message='The plan is unavailable or its session expired.', status=404)
        return response({**value['section'], 'expires_in': store.ttl, 'persistent': False})

    @bp.post('/v1/tourism/itineraries/<identifier>/refine')
    def refine(identifier):
        owner = _owner()
        if not owner:
            return response(code='installation_required', message='An installation token is required.', status=401)
        value = store.get(identifier, owner)
        if not value:
            return response(code='itinerary_not_found', message='The plan is unavailable or its session expired.', status=404)
        return build(owner, value, identifier)

    app.register_blueprint(bp)
    return store
