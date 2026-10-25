# Milestone 04.1.2 — implementation and verification

Implemented locally on 25 October 2026. No commits, pushes, branch changes,
deployments, account changes, new dependencies or native configuration changes.

## Diagnosis

- No production Majestic fallback was found. Majestic occurred in examples,
  tests and user queries. Weather's existing fixed coordinates are explicitly a
  Bengaluru city-level forecast reference, not a user's origin.
- Transport previously composed an external Maps URL only. It did not resolve
  endpoints, and the map consumed place-search cards only. Consequently a valid
  transport handoff could have no in-app geography.
- Android concatenated the prior submitted question with each follow-up.
  The backend had explicit context support but no bounded conversation store.
- Weather's technical forecast description was displayed as the main answer.
- Live investigation found multiple Google records called Indiranagar and HSR
  Layout. Some were sub-areas inside other neighbourhoods. The new resolver first
  rejected these as ambiguous; it now recognises a unique locality address of
  the form `<requested locality>, Bengaluru, ...`. Other ambiguity stays unresolved.

## Implementation and boundaries

### Locations and sessions

`PlacesService.resolve()` performs one photo-free Places New text search per
endpoint, at most three candidates, within the existing Bengaluru viewport.
It validates actual IDs, names, addresses, coordinates and attribution. Exact
name matching, MG/Mahatma Gandhi Road normalisation and unique locality-address
matching are conservative identity checks, not a fuzzy selection of the first hit.
Returned resolution includes original text, status, confidence, candidate
identities when ambiguous, and the sourced place record when resolved.

Transport makes at most two location searches per request. A successful endpoint
from the same session/workflow is reused when its original name is unchanged;
changing only the destination fetches only that endpoint. Failures can be retried
by a subsequent request. Missing origins and “here” never become Majestic or
central Bengaluru. Directions remain available when coordinate lookup fails.
Ambiguous names prompt for a more specific endpoint. No GPS permission is requested.

The original `/v1/chat` message/city envelope still works. Additive request fields:
`session_id`, `follow_up` (boolean), `quick_reply` (whitelisted identifier).
Responses add `session_id`, `session_expires_in`, `map_markers`, and `quick_replies`.
Existing result types and sections/places remain intact.

Sessions are random server-generated identifiers, channel-scoped, in-memory,
limited to 128 per process and expire after 15 minutes of inactivity. One turn per
session executes at a time. At capacity/busy/expired quick actions the service
returns a truthful retry response. A client-supplied unknown ID is not adopted.
There is no account identity, durable memory, multi-process shared store, database,
or Android/WhatsApp linking. Restarting the backend loses context.

Only the latest validated understanding/result is retained: workflow, city,
origin/destination, mode, time, budget, stated preferences, resolved records and
actionable result. General follow-ups receive two bounded context strings.
Same-workflow missing fields can inherit context; a fresh main-input query resets
it. Explicit mode and destination/area changes use narrow rules. Other natural
language still uses the existing GPT-OSS 120B extractor once. Result composition
does not call another LLM. Old travel fields/preferences are removed on a changed
workflow. The Android adapter sends latest text and a session ID, rather than an
ever-growing concatenated transcript, and resets its session when city changes.

### Existing map, minimal in-place expansion

The existing `InlinePlacesMap` component, Google provider and react-native-maps
version are reused. The compact box remains **180dp**, with the original radius,
position after the existing structured sections/cards, and disabled pan/zoom.
The final UI scope override takes precedence over the earlier map-first placement
request: cards were not rearranged around a new map layout.

Tap the preview/Explore map control to expand the same map instance to 400dp.
Pan/pinch are enabled only while expanded. Collapse via the visible control or
Android hardware Back. Initial region and camera are not reset by selection or
expansion; there is no modal, new screen, navigation route or animation system.
The surrounding results stay mounted. Native scroll/gesture behaviour still needs
physical Expo Go verification.

Markers use actual coordinates and stable IDs; origin/destination keep a separate
provider `place_id`. Supported marker distinctions are origin, destination,
accommodation, restaurant, service/hospital, metro station and bus stop. This
milestone emits resolved endpoints and existing place listings; it does **not**
invent intermediate stop coordinates. Selecting a marker shows its real details,
attribution and Directions, and highlights the matching place card when present.
Optional report ID/status/support-count fields are reserved for future real civic
records; no civic markers/data are populated. Marker count remains bounded at six.

**No verified route geometry provider or local geometry dataset is configured.**
The map shows endpoints/places, not a calculated walking/driving/transit path.
No line, ETA, route distance or fare is fabricated. Full road-route drawing would
need a separately approved backend routing provider or verified geometry dataset.
No paid Routes API was enabled or requested.

### Transport source and simulation boundaries

Small static examples, not a Bengaluru transit-network implementation:

| Example | Static coverage | Source |
| --- | --- | --- |
| Purple central | Majestic through Indiranagar, eight named stations | [BMRCL schematic map](https://www.bmrc.co.in/schematic-route-map/) |
| Green south | Lalbagh, South End Circle, Jayanagar | Same BMRCL map |
| Historical ITPB bus segment | Hebbal Bus Stop, Kempapura Bus Stop, Nagavara Kere Arch, Nagavara Cross | [BMTC February 2023 schedule hosted by ITPB/CapitaLand](https://www.capitaland.com/content/dam/capitaland-media-library/businesspark-industrial-logistics/india/bangalore/international-tech-park-bangalore/BMTC%20ITPB%20BUS%20ROUTE%20TIME%20SCHEDULE-FEB%202023_2.pdf), PDF page 4 |

Sources were inspected this turn. BMRCL's linked schematic image was visually
checked. The historical bus sequence is explicitly dated; its present service is
**not verified**. Static stop order does not verify access from a neighbourhood,
current operation or a direct service for arbitrary endpoints.

Only matching configured stop pairs show the fixed **Demo / Simulated** snapshot:
five-minute example frequency and an example next departure in three minutes.
The visible notice says this is not live BMRCL/BMTC information, and Google Maps
may choose different services. No route number, live vehicle position, fare or
actual timetable was invented. Other pairs get resolved endpoints and genuine
Google Maps transit handoff. Walking uses `travelmode=walking`; metro and bus
use the supported `travelmode=transit`. A Maps URL cannot force a particular
metro/bus service, and we do not claim it does.

### Concise summaries and functional replies

- Weather: probability >=60% gives “rain looks likely”; <=20% and zero forecast
  precipitation gives “mostly dry”; intermediate/conflicting signals express a
  chance of rain. The requested time is included. Forecast uncertainty remains
  visible, with hourly values/technical details behind Hourly forecast.
- Hotels/food: retain primary-first sections, real names, photos, credits,
  addresses, independent Directions and explicit price/check-in/menu limitations.
  Summaries refer to the actual locality, mixed need and requested budget.
- Transport: a short origin/destination summary, endpoint map, external Directions
  and configured static/demo information. Technical description stays in Details.

| Context | Tiles and actual behaviour |
| --- | --- |
| Weather | Hourly forecast toggles already-returned hourly data; Tomorrow performs a contextual forecast request |
| Transport | Walk / Metro options / Bus options change retained mode; Change destination opens a small inline input |
| Accommodation | Change area and Change budget collect a refinement; Food nearby searches the retained area |
| Food | Vegetarian options refine search; Change area collects input; Search paneer options appears only for an explicitly stated paneer preference |

Map expand/collapse and Directions are real local/link actions. Request tiles send
structured IDs, checked against the last offered actions. Repeated taps are
guarded while pending. No civic/dead action tiles are exposed. Preferences affect
search terms, never claims that a real restaurant serves a menu item. No optional
demo preference profile was added: user-stated preferences already support the
requested flow without pretending to know personal history.

## Exact frontend scope and files changed this turn

`App.tsx`: seven existing lines changed: ChatOptions import; submission function
signature and options forwarding; ResultView callback; the existing follow-up
composer's two handlers; ResultView signature; and result rendering that moves
technical detail pills behind the existing detailed content when a concise summary
exists. No original styles, layout shell, hero, typography, parallax, animations,
navigation, city picker, Profile, More, News, complaint/post or With You demo changed.
The preservation test retains the original pre-04.1 hashes and permits only the
explicit request-wiring substitutions before hashing the untouched shell.

- Backend: `app.py`, `service.py`, `city_services.py`, `maps_service.py`,
  `places_service.py`; new `conversation.py`, `transport_demo.py`.
- Frontend: `App.tsx`, `api/chat.ts`, `components/CityResults.tsx`,
  `components/InlinePlacesMap.native.tsx`, `components/InlinePlacesMap.tsx`;
  `.gitignore` now also ignores the team's local `.env`.
- Verification: new `test_conversation.py`, `smoke_conversation.py`,
  `evaluation/milestone0412_live.json`; updated `test_city_services.py`,
  `test_frontend_contract.cjs`, `frontend-app/tests/city-results.test.cjs`.
- Documentation: this report and a README link.

The working tree already contained earlier milestone changes. They were not
committed, reset or replaced. Intent model/schema, Twilio adapter/template/signature
logic, native configuration and dependency versions are unchanged this turn.

## Verification

- **116 backend tests pass**: all 96 existing tests plus 20 new tests. Provider
  unit/integration traffic is mocked. Covers all six requested origin/destination
  pairs separately from route coverage; ambiguity; “here”/missing origin; missing
  keys/timeouts; context/mode/destination changes; fresh workflows; session
  separation, locks, capacity and expiry; weather summaries; preferences; demo
  labels and contextual actions. Existing Twilio signature/dedup/worker tests pass.
- **26 frontend tests pass**: original 17 plus nine new tests. Execute actual React
  components with mocked RN/Expo surfaces. Include map instance/camera retention,
  expansion/collapse, hardware Back handler, marker/card connection, Directions,
  hourly display, quick-reply deduplication/input, session request bodies, demo
  labels, existing photos/credits/failures, and original UI preservation.
- TypeScript `npx tsc --noEmit`: pass.
- ESLint `npm run lint`: pass. Expo's wrapper stalled inside the Windows sandbox;
  direct ESLint passed, and the unchanged Expo lint command passed outside it.
- Android JavaScript export: pass, 691 modules. Offline bundle export only,
  **not** an APK/native map/device verification. Generated export was removed.
- `git diff --check`: pass. Known CRLF conversion notices are not whitespace errors.
- Actual configured secret values were scanned against source/artifacts and the
  generated bundle without printing values: no matches. Both local `.env` files
  are Git-ignored. No keys/config values were changed.

### Live verification, separate from mocks

The successful bounded run used four Groq extractions, seven photo-free Places
resolutions and one Open-Meteo fetch through Flask's actual endpoint/test client.
All seven scenarios passed: Indiranagar–Lalbagh, metro follow-up, walking follow-up,
destination change to Cubbon Park, HSR Layout–Whitefield, Majestic–MG Road, weather.
Fresh endpoints took **1.9444–2.9314 seconds** in this run. Destination-only change
took 0.2986 seconds. Cached mode-only turns made zero provider calls; their tiny
local timings are **not end-to-end Android latency claims**.

`evaluation/milestone0412_live.json` retains the initial sandbox timeouts and the
first live ambiguous-resolution failures as previous attempts, as well as the
successful rerun. It records statuses/counts/timing, not Google listing/photo
content or credentials. Live photograph fetching was not repeated this milestone;
existing photo behaviour was regression-tested with mocked provider responses.

**No physical Android/Expo Go interaction or WhatsApp delivery was tested by this
agent.** The team's earlier device observations belong to 04.1. Native map gestures,
scroll retention, external-app opening and hardware Back need device confirmation.

## Team checks and remaining scope

Restart Flask and reload Expo Go. Existing server Groq/Places credentials worked;
no new credential, signing detail, asset or permission is required for this Expo Go
change. Keep backend credentials server-side. The existing custom-binary map setup
is unchanged and was not exercised. Places resolution requests use the existing
billable Text Search API; no photos are requested for transport. Existing place
search photo bounds remain two per section/four for mixed results. No extra LLM is
used for summaries or map interactions.

On the phone: try Indiranagar → Lalbagh, “By metro”, “Actually I'll walk”, then
Change destination; expand/pan/pinch/select/collapse the existing map, test Back,
verify original scrolling and Directions; retest hotels/food/photos and hourly
weather. Public rollout still needs the previously documented Google attribution,
terms/privacy requirements. No provider terms or account settings were changed.

Remaining: verified route geometry and broader/current transit data, physical
device acceptance, future advanced tourism, durable civic records/markers/support
counts, and later WhatsApp identity/context integration. No civic persistence,
ASR, booking, Business API, simulated menu claims or secure handoff was implemented.
