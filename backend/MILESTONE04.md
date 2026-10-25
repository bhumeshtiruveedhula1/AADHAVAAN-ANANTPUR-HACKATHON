# Milestone 04 — city-service implementation and verification

Historical report: [Milestone 04.1](MILESTONE041.md) supersedes the Android
attribution/directions/photo blockers below and records live Places verification.

Implemented on 25 October 2026. The read-only backend and weather acceptance path
are verified. Google Places is implemented with mocked tests, but no Google key
is configured. Android device interaction and WhatsApp delivery were not tested.
The frozen Android presentation cannot yet display Google listing attribution
compliantly or execute response-driven directions links. These are explicit
frontend integration gaps, not claimed working buttons.

## Changes in this milestone

New files:

- `city_http.py`: pooled HTTPS client, four in-flight provider slots, bounded
  connect/pool/read/write timeouts, no retries or redirects, safe error codes.
- `weather_service.py`: Open-Meteo validation, Bengaluru local intervals, one-entry
  five-minute cache and original retrieval timestamps.
- `places_service.py`: Google Places (New) searches and validated source records.
- `maps_service.py`: encoded Google Maps directions URLs; no routing engine.
- `city_services.py`: deterministic routing, primary/secondary result composition.
- `test_city_services.py`, `test_frontend_contract.cjs`: mocked provider, routing,
  and actual TypeScript API-adapter compatibility checks.
- `smoke_city_services.py`: explicit live smoke command; never invoked by tests.
- `evaluation/milestone04_weather_smoke.json`: all three smoke attempts, including
  the failed first attempt and final corrected weather result.
- `MILESTONE04.md`: this report.

Modified files:

- `service.py`: joins validated intent to read-only services; preserves failures.
- `.env.example`: adds placeholder `GOOGLE_PLACES_API_KEY` only.
- `README.md`: current setup and implementation status.
- `test_intents.py`, `test_evaluation.py`: isolate new city network boundaries and
  replace obsolete “weather is unimplemented” expectation with a mocked timeout.
- `benchmark_intents.py`, `revalidate_benchmark.py`: explicitly disable city calls
  in the old intent-only benchmark/replay. No benchmark was rerun or overwritten.
- `../frontend-app/api/chat.ts`: concrete additive place types, runtime validation,
  30-second client timeout for AI plus bounded provider fan-out, and a Google
  attribution display guard. No Expo/React Native API or component was modified.

`App.tsx`, `intents.py`, `groq_provider.py`, `twilio_adapter.py` are byte-for-byte
unchanged from the beginning of this milestone (SHA256 checked). Existing Git
changes from prior milestones remain. No commits, pushes or branch operations.
No dependency additions, account changes, billing changes or `.env` edits.

## Execution and endpoints

`POST /v1/chat` accepts the existing `{message, city}` request. WhatsApp's queued
worker calls the same `process_text()`. One unchanged GPT-OSS 120B extraction
produces the existing validated intent. Python dispatches at most the primary
plus one supported secondary need, sequentially, preserving primary-first order.
Other needs remain in `understanding` and `deferred_intents`. Each provider failure
is isolated to its section; a successful other section yields `partial`.

| Intent | Service |
| --- | --- |
| `weather_conditions` | Open-Meteo |
| `accommodation`, `food_places`, `places_services`, `tourism` | One shared Google Places adapter |
| `transport` | Google Maps handoff, optional official BMRCL reference |
| Civic, emergency, city guidance | No new factual service or action execution |
| Known explicit actions | Existing rule classification, no LLM or action execution |

Provider endpoints:

- `GET https://api.open-meteo.com/v1/forecast`
- `POST https://places.googleapis.com/v1/places:searchText`
- `https://www.google.com/maps/dir/?api=1&destination=...` — generated handoff URL,
  not a provider lookup or computed route. Optional `origin`, `travelmode`, and
  `destination_place_id` are encoded with `urlencode`.
- `https://english.bmrc.co.in/` — official metro reference, not scraped for fares,
  schedules or live train information.

## Weather semantics

The forecast reference is **12.97 N, 77.58 E**, documented by
[IMD's Bangalore reference](https://packolkata.imd.gov.in/download/smrs/Bangalore.pdf).
It is a city-level forecast reference, never the user's GPS. Requests specify
`Asia/Kolkata`. Python uses India's fixed UTC+05:30 offset, including on Windows.

Supported time phrases: today, tomorrow, this morning/afternoon/evening/night,
tomorrow plus those parts, tonight, now/right now/current/currently, or ISO date.
Morning is 06–12, afternoon 12–18, evening 18–22, night 21–24. Unknown phrases ask
for a supported time; dates outside today through the next six days are rejected.
No silent UTC-day substitution. Full-day results include the full local day;
they are forecast values, not observed conditions. Past same-day hours are not
silently relabelled as future conditions.

Only temperature, precipitation probability and precipitation amount are queried.
Temperature is instantaneous. Open-Meteo's precipitation/probability describe the
**preceding hour**: an 18–22 request uses rain rows timestamped 19,20,21,22, alongside
temperature rows 18,19,20,21. Eight days are fetched to include the midnight ending
the last supported day. Returned aligned hourly rows retain both `time` and
`precipitation_interval_end`. Missing values are not converted to zero; invalid
units, timestamps, ranges, duplicate/missing hours and malformed data are rejected.
See the [official field definitions](https://open-meteo.com/en/docs).

The maximum hourly probability is labelled precisely; it is not the calculated
probability for the entire window. Amount is summed over the requested hours.
Temperature and precipitation models can disagree; Python does not invent a
single certainty or reconcile them into a fabricated warning.

One in-memory cache entry is keyed by local date, reference coordinates and actual
forecast parameters; TTL is 300 seconds. Cached results retain original retrieval
time and expose `cache_hit`. Failed refreshes return a limitation, not stale-as-new
data. Concurrent refreshes fail promptly as busy. Cache is not durable.

Attribution appears in the visible description and structured source metadata.
This uses Open-Meteo's public noncommercial API. A commercial rollout must obtain
the appropriate provider terms/plan; no commercial account was created.

## Places boundaries and configuration

Add the real key manually to ignored `backend/.env`:

```ini
GOOGLE_PLACES_API_KEY=YOUR_GOOGLE_PLACES_API_KEY
```

Retain existing Groq/Twilio variables and `GROQ_MODEL=openai/gpt-oss-120b`.
The existing Flask startup already loads this file. Restart the backend afterward.
The team must enable Places API (New) on its billing-enabled project and restrict
the backend credential appropriately. No Cloud project, quota or billing was
created/changed. Existing secrets were not printed or returned.

The precise field mask is:

```text
places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.attributions
```

Single-query limit is five; mixed requests use three per section, at most six
records and two calls. No pagination/retries, wildcard mask, photos, ratings,
prices, opening-hours or availability calls. Requests use a bounded Bengaluru
viewport (12.8–13.2 N, 77.4–77.8 E), explicitly a search area rather than a municipal
boundary. Returned addresses must name Bengaluru/Bangalore; supplied coordinates
must fall within that viewport. This conservative scope may exclude legitimate
outlying listings or records without English city addresses. No inferred GPS.

Query subject comes from the validated service category, area/landmark and explicit
preferences; generic facilities use the user's own request text. Hotel budget and
time remain explicit constraints, never a verified price/check-in filter. “Near me”
requires a useful supplied area. Accessibility, room availability, price, check-in,
operating status and emergency capacity remain unknown. City switching is rejected
by the endpoint; explicitly extracted conflicting cities are rejected by the router.

Place IDs, names, addresses, coordinates when present, safe source links and
third-party attributions survive mapping. Malformed records are rejected; an
entirely invalid nonempty response is an error, not “no results”. Empty successful
responses get `no_results`. Google data is neither cached nor persisted; the live
Places smoke command would store only result count/status, not listing content.

Absent Google key leaves weather and transport operational. A Places-only request
returns the established HTTP 503 error envelope with truthful `stay`/`guide` data
and no places. Other lookup failures use distinct section/result status codes:
`timeout`, `rate_limited`, `authentication_error`, `connection_error`, `busy`,
`invalid_response`, `missing_data`, `unsupported_time`, `unsupported_range`,
`missing_location`, `unsupported_city`, `no_results`, `partial`.
Existing Groq HTTP error semantics remain unchanged.

## Exact result shape and frozen renderer

The envelope remains `{success: boolean, data: object|null, error: {code,message}|null}`.
Required result fields remain `type`, `title`, `description`, `details: string[]`,
`places: object[]`. City results use `stay`, `weather`, `civic`, or `guide`.
The existing `unavailable` value is retained only for the existing intent/error UI.

Additive city-result fields:

```text
status, city, intent, understanding, interpretation_status,
source, sources[], actions: [], sections[], deferred_intents[], channel_text
request_id                    # added by /v1/chat
display_requirements?         # when Google listing content is present
```

Sections contain `intent`, `status`, `description`, `places`, `sources`, with
optional `details`, `weather`, `directions_url`, origin/destination/travel_mode,
or rejected-record count. `weather` contains interval, timezone, reference_location,
units, temperature_min/max, precipitation_probability_max, precipitation_total,
hourly, source, cache_hit and cache_ttl_seconds. Source records distinguish actual
retrievals from static official references/handoffs; static links have no invented
retrieval timestamp.

Place record fields:

```text
id, name, address, location: {latitude,longitude}|null, category,
source, retrieved_at, source_url|null, directions_url|null,
attributions: [{provider,provider_uri|null}],
image:null, price:null, availability:null, immediate_check_in:null,
open_now:null, accessibility:null
```

The actual `App.tsx` ResultView renders only title, description and detail pills;
it does not render `places`, photos or response actions. Existing unused styles
are not evidence of working handlers. Weather appears in the normal result card.
Transport displays truthful guidance and a nonclickable Maps URL. No fake buttons
are emitted (`actions: []`). Explicit `open_directions` action execution remains
unbuilt; ordinary transport requests produce handoff information only.

Google requires identifiable, styled attribution and third-party credit display;
see [the current Places policy](https://developers.google.com/maps/documentation/places/web-service/policies).
The frozen renderer has no logo slot; body text uses `#73736F`, and detail pills are
10sp, neither the documented text colour/size combination for compliant Google
attribution. Photo author links also lack handlers. Therefore the API adapter hides
listing content and reports `display_integration_required` until a separate,
authorised frontend attribution/link integration. Non-Google weather/transport
sections remain visible for mixed responses. Backend structured records remain
available for future compliant consumers. Photos always remain null.
Public Terms of Use and Privacy Policy incorporating required provider terms also
remain a team rollout requirement. No UI, typography, navigation or asset changed.

## Tests and live evidence

Mocked tests exercise the complete route with one intent call, actual SDK-compatible
extraction and HTTP fixtures, both provider failures, relative dates, attribution,
malformed/empty results, no invented metadata, URL encoding, partial results,
concurrency, cache expiry, deterministic actions, old intent regressions and Twilio
signature/queue/deduplication tests. The contract test transpiles and executes the
actual `frontend-app/api/chat.ts`, including `sendChat`, against backend responses.
These tests use synthetic listings and require no live provider credentials.

Executed checks:

- `backend/.venv/Scripts/python -m unittest discover -q`: **87 tests passed**.
- `node node_modules/typescript/bin/tsc --noEmit`: passed.
- `node node_modules/eslint/bin/eslint.js App.tsx api/chat.ts`: passed.
- `git diff --check`: passed; `.env` confirmed ignored.

Live acceptance request: `{"message":"Will it rain in Bengaluru this evening?","city":"Bengaluru"}`.
The Flask test client exercised the real `/v1/chat` envelope, actual Groq 120B,
actual Open-Meteo and Python composition. No network/browser proxy or fabricated
forecast fixture was substituted for the live calls.

The first sandboxed request timed out during Groq (HTTP 504, 2.6269 s); weather was
not called. A network-enabled request passed (1.6534 s), followed by a final repeat
after correcting preceding-hour precipitation selection. Final result: **HTTP 200,
status ok, 1.8021 s total**: intent 1.1092 s, weather 0.6918 s. It selected
25 October 2026 18:00–22:00 Asia/Kolkata. All attempts are retained in
[`evaluation/milestone04_weather_smoke.json`](evaluation/milestone04_weather_smoke.json).
The earlier successful record predates the precipitation alignment correction;
use the final attempt for the accepted forecast. These are individual backend
measurements, not latency percentiles or device end-to-end measurements.

Verification distinction:

- **Backend:** implemented and mocked tests passed.
- **Live providers:** Groq plus Open-Meteo verified. Google key absent; no Google
  call performed. Missing-key path verified separately with mocks.
- **Android:** actual adapter/TypeScript/lint verified locally; no emulator/device
  interaction or rendered attribution verification. No clickable directions yet.
- **WhatsApp:** shared processing and signature/queue/template tests passed with
  mocks; no live Twilio message or delivery verification in this milestone.

Manual checks from `backend/`:

```powershell
.\.venv\Scripts\python app.py
# Explicit optional live weather smoke (one Groq + one weather request):
.\.venv\Scripts\python smoke_city_services.py
# Only after supplying an enabled/restricted Google key; may incur usage charges:
.\.venv\Scripts\python smoke_city_services.py --case places
```

Do not run the live commands as part of the unit suite. Intent benchmark and
saved-output replay explicitly disable city providers to preserve their scope.

## Remaining scope

Civic persistence/map flags/With You operations, durable WhatsApp jobs, hotel
Business API with price/check-in/availability, booking, live transit feeds,
WhatsApp voice/ASR, deep-link handoff, advanced itineraries and multi-city rollout
remain unimplemented. Existing approved-template delivery is unchanged and does
not dynamically send the new `channel_text`. In-memory jobs/deduplication remain
non-durable across restarts. Provider timeouts are network phase timeouts, not a
hard wall-clock SLA; frontend timeout is 30 seconds. No additional infrastructure.
