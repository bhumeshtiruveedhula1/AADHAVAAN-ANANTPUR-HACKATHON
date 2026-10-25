"""Read/draft integration only: a chat message never creates a civic record."""
from city_http import CityServiceError
from civic_service import CivicService, CivicError


def marker(report):
    if not report['public'] or not report['location_confirmed'] or report['latitude'] is None or report['longitude'] is None:
        return None
    return {'id': report['id'], 'report_id': report['id'], 'name': 'Community-reported ' + report['category'].replace('_', ' '),
            'address': report['location_text'], 'category': 'civic_lookup', 'marker_type': 'civic',
            'location': {'latitude': report['latitude'], 'longitude': report['longitude']},
            'source': 'Smart Nagrik community', 'retrieved_at': report['updated_at'], 'source_url': None,
            'directions_url': None, 'image': None, 'photo': None, 'attributions': [],
            'support_count': report['support_count'], 'issue_status': report['status'], 'civic_report': report}


def compose_civic(intent, places, civic=None, owner=None):
    service = civic or CivicService()
    result = {'type': 'civic', 'title': 'Community reports', 'summary': '', 'description': '', 'details': [], 'places': [],
              'sections': [], 'reports': [], 'map_markers': [], 'status': 'ok', 'city': intent.get('city'),
              'understanding': intent, 'intent': intent['primary_intent'], 'actions': [], 'sources': []}
    if intent.get('city') not in {'Bengaluru', 'Bangalore'}:
        result.update(status='clarification_required', summary='Please confirm Bengaluru as your city.')
    elif intent['primary_intent'] == 'civic_report' or intent.get('action') == 'report_issue':
        result.update(title='Review your issue before submitting', status='draft',
                      summary='Open Add Complaint to verify the details and choose whether to share the report. Nothing has been submitted yet.',
                      report_draft={'category': intent.get('civic_category') or 'other', 'description': intent['original_text'],
                                    'location_text': intent.get('exact_location') or intent.get('area') or intent.get('landmark') or ''})
    else:
        try:
            report_id = intent.get('report_id')
            if report_id:
                reports = [service.get_report(report_id, owner)]
                result['summary'] = 'Here is the saved Smart Nagrik report status. This is not an official government status.'
            elif intent.get('action') == 'view_report_status' or 'my report' in intent['original_text'].casefold() or 'status' in intent['original_text'].casefold():
                result.update(status='clarification_required', summary='Open My reports for this installation, or provide the report reference. I will not guess which report you mean.')
                reports = []
            else:
                area = intent.get('area') or intent.get('landmark') or intent.get('exact_location')
                if not area or area.casefold() in {'here', 'near me', 'nearby', 'my location'}:
                    result.update(status='clarification_required', summary='Which Bengaluru locality should I check for community reports? Your current location is unknown.')
                    reports = []
                else:
                    location = places.resolve(area)
                    if location.get('status') != 'resolved':
                        result.update(status='clarification_required', summary='Please give a more specific locality or landmark so I can check the correct area.')
                        reports = []
                    else:
                        point = location['place']['location']
                        page = service.list_reports(owner=owner, latitude=point['latitude'], longitude=point['longitude'], radius_km=3, category=intent.get('civic_category'), limit=6)
                        reports = page['items']
                        result['summary'] = f"{len(reports)} community reports found around {area}." if reports else 'No reports found in Smart Nagrik for this area. This does not mean the area has no issues.'
                        result['details'] = ['Public community reports; not official verification.', 'Search uses a bounded box extending approximately 3 km north/south/east/west of the resolved locality, not your GPS position.']
            result['reports'] = reports
            result['map_markers'] = [value for report in reports if (value := marker(report))][:6]
        except (CivicError, CityServiceError) as error:
            result.update(status=error.code, summary=error.message if isinstance(error, CivicError) else 'The locality lookup is unavailable. Please try again or open My reports.')
    result['description'] = result['summary']
    return result
