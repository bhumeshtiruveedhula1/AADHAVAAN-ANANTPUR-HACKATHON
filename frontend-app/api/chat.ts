import { identityHeaders, type CivicReport, type ReportDraft } from './civic';

export type PlaceRecord = {
  id: string;
  name: string;
  address: string;
  category: string;
  location: { latitude: number; longitude: number } | null;
  source: string;
  retrieved_at: string;
  source_url: string | null;
  directions_url: string | null;
  image: string | null;
  photo?: { url: string; source_url: string | null; authors: { name: string; uri: string | null; avatar_url: string | null }[] } | null;
  attributions: { provider: string; provider_uri: string | null }[];
  marker_type?: 'origin' | 'destination' | 'accommodation' | 'restaurant' | 'service' | 'metro_station' | 'bus_stop' | 'civic';
  place_id?: string;
  // Reserved marker metadata for future real civic records; never populated
  // by this milestone and never inferred from Google listings.
  report_id?: string;
  issue_status?: string;
  support_count?: number;
  itinerary_order?: number;
  visit_minutes?: number;
  planning_reason?: string;
  itinerary_kind?: string;
  civic_report?: CivicReport;
};

export type ChatResult = {
  type: 'stay' | 'weather' | 'civic' | 'guide' | 'unavailable';
  title: string;
  description: string;
  details: string[];
  places: PlaceRecord[];
  status?: string;
  request_id?: string;
  summary?: string;
  sections?: ResultSection[];
  display_requirements?: { google_maps_attribution: boolean; android_display_ready: boolean };
  session_id?: string;
  map_markers?: PlaceRecord[];
  quick_replies?: { id: string; label: string; kind: 'request' | 'input' | 'local' }[];
  reports?: CivicReport[];
  report_draft?: ReportDraft;
};

export type ResultSection = {
  intent: string;
  status: string;
  description: string;
  summary?: string;
  limitations?: string[];
  places: PlaceRecord[];
  directions_url?: string | null;
  sources?: { name: string; url: string; retrieved_at?: string }[];
  demo?: { notice: string; static_label: string; stops: string[]; source: string } | null;
  weather?: { hourly: { time: string; temperature_2m: number; precipitation_probability: number; precipitation_interval_end: string }[] };
  itinerary?: { id: string; constraints: Record<string, unknown>; stops: { place_id: string; order: number; visit_minutes: number; reason: string }[] };
};

export function httpsUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 4096) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password ||
        [...url.searchParams.keys()].some((key) => ['key', 'api_key', 'x-goog-api-key'].includes(key.toLowerCase()))) return null;
    return value;
  } catch { return null; }
}

export function directionsUrl(value: unknown, placeId?: string): string | null {
  const safe = httpsUrl(value);
  if (!safe) return null;
  const url = new URL(safe);
  if (url.hostname !== 'www.google.com' || url.pathname !== '/maps/dir/' ||
      url.searchParams.get('api') !== '1' || !url.searchParams.get('destination')?.trim()) return null;
  if (placeId && url.searchParams.get('destination_place_id') !== placeId) return null;
  const mode = url.searchParams.get('travelmode');
  if (mode && !['transit', 'driving', 'walking', 'bicycling'].includes(mode)) return null;
  return safe;
}

function photoUrl(value: unknown): string | null {
  const safe = httpsUrl(value);
  if (!safe) return null;
  const host = new URL(safe).hostname;
  return ['googleusercontent.com', 'ggpht.com'].some((domain) => host === domain || host.endsWith(`.${domain}`)) ? safe : null;
}

function cleanPlaces(values: unknown): PlaceRecord[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  return values.slice(0, 6).flatMap((place: PlaceRecord) => {
    if (!place || !['id', 'name', 'address', 'category'].every((key) => typeof place[key as keyof PlaceRecord] === 'string' && String(place[key as keyof PlaceRecord]).trim()) || seen.has(`${place.category}:${place.id}`) || place.source !== 'Google Maps' ||
        !Array.isArray(place.attributions) || !place.attributions.every((credit) => credit && typeof credit.provider === 'string' && (!credit.provider_uri || httpsUrl(credit.provider_uri)))) return [];
    seen.add(`${place.category}:${place.id}`);
    const point = place.location;
    const location = point && Number.isFinite(point.latitude) && Number.isFinite(point.longitude) &&
      point.latitude >= 12.8 && point.latitude <= 13.2 && point.longitude >= 77.4 && point.longitude <= 77.8 ? point : null;
    const photo = place.photo;
    const validPhoto = photo && photoUrl(photo.url) && (!photo.source_url || httpsUrl(photo.source_url)) &&
      Array.isArray(photo.authors) && photo.authors.every((author) => author && typeof author.name === 'string' && author.name.trim() && (!author.uri || httpsUrl(author.uri)));
    return [{ ...place, location, directions_url: directionsUrl(place.directions_url, place.place_id || place.id), source_url: httpsUrl(place.source_url),
      image: validPhoto ? photo!.url : null,
      photo: validPhoto ? { ...photo!, authors: photo!.authors.map((author) => ({ ...author, avatar_url: photoUrl(author.avatar_url) })) } : null }];
  });
}

type Envelope = {
  success: boolean;
  data: ChatResult | null;
  error: { code: string; message: string } | null;
};

const issue = (title: string, description: string, status: string): ChatResult => ({
  type: 'unavailable', title, description, details: [], places: [], status,
});

export function adaptResult(value: unknown): ChatResult | null {
  if (!value || typeof value !== 'object') return null;
  const result = value as ChatResult;
  if (!['stay', 'weather', 'civic', 'guide', 'unavailable'].includes(result.type) ||
      typeof result.title !== 'string' || typeof result.description !== 'string' ||
      !Array.isArray(result.details) || !result.details.every((item) => typeof item === 'string') ||
      !Array.isArray(result.places)) return null;
  const places = cleanPlaces(result.places);
  const sections = Array.isArray(result.sections) ? result.sections.slice(0, 2).filter((section) => section && typeof section.intent === 'string' && typeof section.description === 'string').map((section) => ({
    ...section, summary: typeof section.summary === 'string' ? section.summary : undefined,
    limitations: Array.isArray(section.limitations) ? section.limitations.filter((item) => typeof item === 'string') : [],
    places: cleanPlaces(section.places).flatMap((place) => {
      const canonical = places.find((candidate) => candidate.id === place.id && candidate.category === section.intent);
      return canonical ? [canonical] : [];
    }),
    directions_url: directionsUrl(section.directions_url),
    sources: Array.isArray(section.sources) ? section.sources.filter((source) => source && typeof source.name === 'string' && httpsUrl(source.url)) : [],
    demo: section.demo && typeof section.demo.notice === 'string' && section.demo.notice.startsWith('Demo / Simulated:') && typeof section.demo.static_label === 'string' && httpsUrl(section.demo.source) && Array.isArray(section.demo.stops) && section.demo.stops.every((stop) => typeof stop === 'string') ? { ...section.demo, stops: section.demo.stops.slice(0, 12) } : null,
    weather: section.intent === 'weather_conditions' && Array.isArray(section.weather?.hourly) ? { hourly: section.weather!.hourly.slice(0, 24).filter((row) => row && typeof row.time === 'string' && typeof row.precipitation_interval_end === 'string' && Number.isFinite(row.temperature_2m) && Number.isFinite(row.precipitation_probability) && row.precipitation_probability >= 0 && row.precipitation_probability <= 100) } : undefined,
  })) : undefined;
  const quickKinds: Record<string, string> = { mode_walk: 'request', mode_metro: 'request', mode_bus: 'request', weather_tomorrow: 'request', food_nearby: 'request', vegetarian: 'request', paneer: 'request', show_details: 'local', change_area: 'input', change_budget: 'input', change_destination: 'input', tourism_shorter: 'request', tourism_food: 'request', tourism_nature: 'request', open_report: 'local', my_reports: 'local' };
  const quick_replies = Array.isArray(result.quick_replies) ? result.quick_replies.filter((tile) => tile && quickKinds[tile.id] === tile.kind && typeof tile.label === 'string').slice(0, 4) : [];
  const reports = Array.isArray(result.reports) ? result.reports.filter(validReport).slice(0, 6) : [];
  const civicMarkers: PlaceRecord[] = reports.filter((report) => report.public && report.location_confirmed && typeof report.latitude === 'number' && typeof report.longitude === 'number' && report.latitude >= 12.8 && report.latitude <= 13.2 && report.longitude >= 77.4 && report.longitude <= 77.8).map((report) => ({ id: report.id, report_id: report.id, name: `Community-reported ${report.category.replace(/_/g, ' ')}`, address: report.location_text, category: 'civic_lookup', marker_type: 'civic', source: 'Smart Nagrik community', retrieved_at: report.updated_at, source_url: null, directions_url: null, image: null, attributions: [], location: { latitude: report.latitude!, longitude: report.longitude! }, civic_report: report, support_count: report.support_count, issue_status: report.status }));
  return { ...result, summary: typeof result.summary === 'string' ? result.summary : undefined, places, sections,
    reports, report_draft: result.report_draft && typeof result.report_draft === 'object' ? { category: typeof result.report_draft.category === 'string' ? result.report_draft.category : undefined, description: typeof result.report_draft.description === 'string' ? result.report_draft.description : undefined, location_text: typeof result.report_draft.location_text === 'string' ? result.report_draft.location_text : undefined } : undefined,
    map_markers: [...cleanPlaces(result.map_markers), ...civicMarkers].slice(0, 6), quick_replies };
}

export function validReport(value: unknown): value is CivicReport {
  if (!value || typeof value !== 'object') return false;
  const report = value as CivicReport;
  return /^SN-[a-f0-9]{32}$/.test(report.id) && ['category', 'description', 'location_text', 'status', 'status_label', 'created_at', 'updated_at'].every((key) => typeof report[key as keyof CivicReport] === 'string') && typeof report.public === 'boolean' && typeof report.supported === 'boolean' && Number.isSafeInteger(report.support_count) && report.support_count >= 0;
}

export type ChatOptions = { follow_up?: boolean; quick_reply?: string };
let conversation: { city: string; id: string } | null = null;
let pending: Promise<ChatResult> | null = null;

export async function sendChat(message: string, city: string, options?: ChatOptions): Promise<ChatResult> {
  if (pending) return issue('Request in progress', 'Please wait for the current reply.', 'busy');
  pending = requestChat(message, city, options);
  try { return await pending; } finally { pending = null; }
}

async function requestChat(message: string, city: string, options?: ChatOptions): Promise<ChatResult> {
  if (conversation?.city !== city) conversation = null;
  const baseUrl = process.env.EXPO_PUBLIC_API_URL?.trim();
  if (!baseUrl) return issue('Backend address needed', 'Set EXPO_PUBLIC_API_URL to your local Smart Nagrik backend.', 'api_url_missing');

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`${baseUrl.replace(/\/$/, '')}/v1/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...await identityHeaders() },
      body: JSON.stringify({ message, city, ...(conversation ? { session_id: conversation.id } : {}), ...options }),
      signal: controller.signal,
    });
    const body: Envelope = await response.json();
    const result = adaptResult(body.data);
    if (result) {
      if (typeof result.session_id === 'string' && /^[A-Za-z0-9_-]{24,64}$/.test(result.session_id)) conversation = { city, id: result.session_id };
      return result;
    }
    if (!response.ok || !body.success) {
      return issue('Request unavailable', body.error?.message || 'The backend could not answer this request.', body.error?.code || 'api_error');
    }
    return issue('Unexpected response', 'The backend returned a result the app cannot display.', 'invalid_response');
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return issue('Request timed out', 'The backend did not respond within 30 seconds. Please try again.', 'timeout');
    }
    return issue('Cannot reach the backend', 'Check that the backend is running and this device can reach its address.', 'network_error');
  } finally {
    clearTimeout(timeout);
  }
}
