# Final pre-WhatsApp implementation — 25 October 2026

Tourism, civic reporting, civic discovery, community posts and With You are
implemented in the existing Flask/Expo application. Automated and live-provider
verification are described below. Physical Android acceptance remains pending.
Existing running servers were not stopped or restarted. No Git history operations,
native configuration changes, credential replacements or Twilio changes were made.

## What works

### Tourism

Chat routes tourism to `plan_itinerary()` after the existing GPT-OSS 120B extraction.
The deterministic planner uses actual Google Places results and photos, and the
existing Open-Meteo forecast within its supported date horizon. It considers
duration, starting area, interests, explicitly stated food preferences, requested
budget, mode, group size and limited walking. A missing origin prompts for a
starting area; the planner never substitutes GPS or a central Bengaluru origin.

One to five stops are selected, with a separate food stop when requested and when
the duration permits. Short plans have fewer stops. Geographic ordering is a
nearest-neighbour straight-line heuristic, not a road-route or travel-time engine.
Visit minutes and journey/break buffer are labelled planning estimates. Costs,
opening hours, menu availability and accessibility are unverified. A lower-budget
or limited-walking request reduces stop count/transfers; it does not prove that
the budget or walking-distance requirement is met.

Rain probability can influence the search towards indoor alternatives. No forecast
is requested for dates beyond the existing horizon. Provider failure is disclosed;
available attraction results remain usable when optional food/weather fails.

The existing result cards show stop number, real name/address/photo/credits,
planning reason, estimated visit duration and actual destination Directions. The
same 180dp/400dp inline map shows numbered real stop markers. No route lines are
invented. Food stops retain a food label while using the existing tourism section.

Refinements include shorter plans, adding a named stop such as Lalbagh, removing
or replacing a named stop, nature/heritage changes, a rainy afternoon, budget,
walking preference, duration and starting area. Explicit refinements use bounded
rules within an active tourism workflow; other language uses the existing extractor.
Unchanged stops can be reused within the 15-minute session. Shortening was verified
live with zero additional Groq/Places calls. A stable `IT-...` identifier survives
refinement. Plans are **ephemeral**, owner-scoped, bounded to 128 and expire after
15 minutes. Restarting the backend loses plans; no Google content/photo cache is
persisted to SQLite.

### Civic reports and authorised media

The existing Add Complaint/Report Issue form now collects real values and calls
the backend. Chat descriptions only produce a draft and Open Report Issue action;
they never submit automatically. The user reviews the issue, location and public
visibility before pressing Submit report. Success appears only after a confirmed
server save, with the actual `SN-...` reference.

Location lookup uses the existing Places resolver. A returned locality centre is
explicitly not assumed to be an exact issue position. Users review/adjust optional
latitude and longitude and confirm their details. Without coordinates, the report
is saved with no map marker. The server requires confirmation for coordinates and
enforces the existing Bengaluru viewport. No automatic GPS request is introduced.

Reports default to private. Public description/location and public photo consent
are separate choices. A private report/photo requires the owning installation
credential; knowing its reference or media ID is insufficient. Public reports may
be discovered by area/category or bounded geographic box. Media is decoded,
validated and converted to a new metadata-free JPEG before saving: JPEG/PNG/WebP,
single frame, <=5 MB input, <=20 million pixels, resized to <=2000px per dimension.
EXIF/GPS metadata is removed. Invalid media rolls back the entire save. No public
upload directory or original user filename is exposed.

`Profile → My reports` reads actual installation-authorised history. Status is
**Reported to Smart Nagrik**, with a real stored timestamp/history entry. There is
no government submission, official verification or invented resolution status.

### Durable SQLite data

Default location: `backend/data/civic.sqlite3`, created lazily at the first civic
operation. `SMART_NAGRIK_DATA_DIR` can override its directory. `backend/data/` is
Git-ignored. No default database was created by this verification: tests used
temporary directories exclusively, and live tests made no civic writes.

| Table | Stored purpose / constraint |
| --- | --- |
| `reports` | Stable ID, hashed owner, user details, optional confirmed coordinates, visibility, media reference, actual status/timestamps; unique `(owner,idempotency_key)` |
| `status_history` | Actual Smart Nagrik status event, source and timestamp |
| `posts` | User text/location, optional existing report reference, publication choices, media reference, timestamp; unique `(owner,idempotency_key)` |
| `support` | Unique `(report_id,supporter)`; one installation can support once |
| `media` | Sanitised JPEG BLOB, owning installation hash and associated record |

Schema version 1, foreign keys, WAL mode, bounded busy timeout and transactions are
used. Photo and report/post commit atomically. Duplicate request retries return the
same saved record; reuse of a key for different content returns 409. Reopening the
database in a separate Python process was tested. Back up this directory using a
SQLite-consistent backup procedure; it contains private local user data.

### Community, markers and With You

The existing Post form saves actual text, optional location/photo and optional
existing report ID. It links to the original issue rather than cloning it. Public
posts cannot expose a private report. My posts and community lists are paginated.
The existing News panel now explicitly distinguishes community information from
official news; the old fabricated news/report cards and counters are not presented
as real records. No news provider or push notification service was added.

Only real **public, confirmed, geolocated** reports become civic markers. Civic
chat resolves the requested locality and queries a box extending approximately
3 km north/south/east/west; this is not a precise radial or GPS distance claim.
No matches means “No reports found in Smart Nagrik for this area,” not “safe.”
Weather/hotel results do not automatically receive unrelated civic pins. Relevant
alerts show reports already loaded in the current civic result.

Selecting a civic marker shows the same report card, actual category/status/count,
View report, With You and public Share. Map camera and parent results remain
mounted. Both map detail and list card listen for the same updated report record.
With You uses an idempotent explicit set/unset operation; retries cannot inflate
the count. The supported state and count persist in SQLite and reload correctly.
Sharing uses native concise public text/reference, not a fabricated deep link or
private photo/token. Support means “this concern matters to me,” not verification.

Installation identity is two cryptographic UUIDs stored in Expo SecureStore;
only a SHA-256 hash is stored in SQLite. It is **not verified one-person-one-vote**,
an account, or a phone binding. Clearing the installation/secure storage may lose
access to its private history. There is no recovery/account system in this scope.
Chat sessions are bound to their installation hash when supplied, preventing a
different installation from resuming a private contextual result.

## Reusable interfaces for the WhatsApp engineer

Twilio adapters, sender/templates, signature validation, voice and phone identity
were left untouched. The reusable operations do not depend on React Native:

- `tourism_service.plan_itinerary(intent, places, weather=None, previous=None)`
- `CivicService.create_report(owner_hash, body, photo_bytes=None)`
- `CivicService.get_report(id, owner_hash=None)`
- `CivicService.list_reports(...)`, including `mine=True` for authorised history
- `CivicService.create_post(owner_hash, body, photo_bytes=None)` / `list_posts(...)`
- `CivicService.set_support(report_id, owner_hash, supported_bool)`
- `CivicService.get_media(id, owner_hash=None)`
- Shared `process_text(TextRequest)` with additive optional `owner_ref`.

Future adapters must establish authorised identity and explicit submission consent.
Do not map an unverified WhatsApp phone number to an Android installation or create
a second civic store. WhatsApp consequential writes still require durable inbound
job/deduplication integration; this milestone's submission keys do not make the
existing WhatsApp in-memory queue durable.

All HTTP endpoints retain `{success,data,error}`. Civic writes and private history
use `Authorization: Bearer <32–128 character installation token>`. Tokens are never
in query strings, report JSON, share text or logs. Public reads can omit identity;
supplying it returns that installation's support state. Native frontend requests
use the existing API base URL. Production use requires HTTPS and an appropriate
abuse/moderation policy; no administrative or official-status workflow was added.

| Endpoint | Contract |
| --- | --- |
| `POST /v1/tourism/itineraries` | `{message?,constraints?}`; constraints include duration_hours 1–12, origin, date_time, interests, preferences, include_food, budget `{amount,currency:'INR'}`, mode, group size and limited walking (wire keys: `travel_mode`, `group_size`, `limited_walking`) |
| `GET /v1/tourism/itineraries/{id}` | Authorised ephemeral plan, `persistent:false`, `expires_in` |
| `POST /v1/tourism/itineraries/{id}/refine` | Same constraint/message shape, same plan ID |
| `POST /v1/civic/reports` | JSON, or multipart `payload` JSON plus optional `photo`; fields below |
| `GET /v1/civic/reports/{id}` | Public or owner-authorised actual record/history |
| `GET /v1/civic/reports` | `area`, `category`, or `latitude`,`longitude`,`radius_km`; `limit` 1–25, `offset` <=10000 |
| `GET /v1/civic/my-reports` | Same pagination, authenticated owning installation |
| `POST /v1/civic/reports/{id}/support` | `{supported:true|false}`; idempotent, reversible |
| `POST /v1/civic/resolve-location` | `{text}`; provider suggestion, still requires user confirmation |
| `GET /v1/civic/media/{id}` | JPEG, permission checked, private/no-store |
| `GET /v1/community/posts` | `area`, `limit`, `offset`, optional authenticated `mine=true` |
| `POST /v1/community/posts` | JSON or multipart; text/location, optional report ID, consent and retry key |

Report fields: `idempotency_key`, `category` (pothole/streetlight/garbage/road_damage/
waterlogging/other), `description`, `location_text`, optional numeric `latitude` and
`longitude`, `location_confirmed`, `public`, `photo_public`. Posts use `text`, optional
`location_text`/`report_id`, `public`, `photo_public`, `idempotency_key`. Owner hashes
are server-derived, never client-supplied data fields. Coordinates may be absent;
then `location_confirmed` is false and no marker is emitted. JSON invalid fields
return meaningful errors; oversized uploads are bounded independently of the
existing 8KB chat/webhook limit.

Frontend response additions: `reports[]`, `report_draft`, civic marker metadata and
`sections[].itinerary`. Place records may include `itinerary_order`, `visit_minutes`,
`planning_reason`, `itinerary_kind`. Existing result types/envelope/places remain
compatible. Civic markers are validated from report records separately from Google
content, with source `Smart Nagrik community` and no misleading Google attribution.

Tourism quick actions: `tourism_shorter`, `tourism_food`, `tourism_nature` and existing
`change_area`. Civic local actions: `open_report`, `my_reports`. Each is wired to a
real operation or existing sheet. No automatic complaint submission, dead Replace
Stop tile, or WhatsApp-specific handoff is generated.

## Exact file changes in this milestone

| Files | Purpose |
| --- | --- |
| `backend/tourism_service.py`, `tourism_routes.py` | Planner, refinement, bounded authorised plan references |
| `backend/civic_store.py`, `civic_service.py`, `civic_media.py`, `civic_routes.py` | Durable records, permissions, sanitised media, support and HTTP adapters |
| `backend/civic_chat.py` | Draft-only reporting and sourced database lookup/markers |
| `backend/app.py`, `service.py`, `city_services.py`, `conversation.py` | Route registration, owner/session isolation, shared intent routing and quick actions |
| `backend/requirements.txt` | Pillow for actual image decoding/transcoding/metadata removal |
| `backend/.env.example`, `.gitignore` | Optional data-directory documentation and ignored durable data |
| `frontend-app/api/civic.ts` | Protected installation identity, authenticated API/media, retry keys, shared support updates |
| `frontend-app/api/chat.ts` | Identity headers and validated civic/itinerary response extensions |
| `frontend-app/components/CivicForms.tsx` | Existing form content uses passed original App style tokens; real save/confirmation/error paths |
| `frontend-app/components/CivicContent.tsx` | Real report/post/history content and support/share/media |
| `frontend-app/components/CityResults.tsx`, `InlinePlacesMap.native.tsx` | Itinerary numbers, civic cards/markers, contextual actions; same map sizes/position |
| `frontend-app/App.tsx` | Existing form/profile/news/alerts/result handlers connected to real operations; fake local-success/counter handlers removed |
| `frontend-app/package.json`, `package-lock.json` | Expo-compatible SecureStore and Crypto dependencies only |
| `backend/test_civic.py`, `test_tourism.py`, `test_product_integration.py` | New temp-DB/provider-mocked module and combined tests |
| `backend/test_city_services.py`, `test_conversation.py`, `test_frontend_contract.cjs` | Existing expectations updated for now-implemented tourism/civic actions; identity module mock |
| `frontend-app/tests/city-results.test.cjs`, `tests/accept-response.cjs` | Actual component/handler tests, native mocks, real-response adapter checks |
| `backend/smoke_prewhatsapp.py`, `evaluation/prewhatsapp_live.json` | Bounded opt-in live acceptance and honest timing/failure evidence |
| `backend/evaluation/prewhatsapp_preservation.json` | Pre-edit hashes for unchanged visual/configuration boundaries |
| This report and `backend/README.md` | Team acceptance and API handoff |

Previous milestone changes already existed in the uncommitted working tree. This
report describes the additions for this milestone, not the entire Git diff.

## UI and dependency preservation

App's entire existing style object, hero/home markup, city picker, emergency
controls, `app.json`, `app.config.js`, Groq provider and Twilio adapter match the
captured pre-edit hashes. No map modal/tab, navigation architecture, native signing,
font, hero asset, global colour, original scroll container or animation changes.
Necessary edits are inside existing report/post/profile/news/alerts/results wiring.
The results empty-state labels no longer incorrectly say the implemented services
are disconnected or draft-only. News clearly labels community content. Existing
unrelated profile placeholders and the pre-existing WhatsApp handoff demonstration
were not implemented as real account/handoff features.

Two small Expo dependencies are necessary for durable, cryptographic installation
identity: `expo-secure-store ~58.0.2`, `expo-crypto ~58.0.7`. Installed with Expo's SDK
version resolver. They are available in Expo Go per the matching SDK58 docs:
[SecureStore](https://docs.expo.dev/versions/v58.0.0/sdk/securestore/),
[Crypto](https://docs.expo.dev/versions/v58.0.0/sdk/crypto/).
Expo installation could not automatically edit the project's dynamic config;
no plugin or native setting was added. Expo Go needs no new binary for these APIs.
A future custom build would need its own SecureStore plugin/backup configuration;
that is outside this Expo Go milestone. Pillow `>=12.1.1,<14` was added server-side
for [validated image handling](https://pillow.readthedocs.io/en/stable/reference/Image.html).

## Verification and limitations

- **169 backend tests pass**: 116 existing +27 civic +19 tourism +7 integrated.
  Provider unit traffic is mocked. Civic tests use temporary databases, including
  a separate-process restart check, concurrent retries, permissions, photos,
  rollback, support and feed identity consistency. No actual local reports touched.
- **40 frontend tests pass**: 26 previous +14 new component/interaction tests.
  Actual React components/App handlers execute with mocked native/network APIs:
  draft-to-existing-form, Profile history, save/retry/failure, photo multipart,
  consent, support synchronization, public sharing, itinerary marker numbering,
  old cards/photos/weather/transport/map gestures props and preservation hashes.
- TypeScript and ESLint pass. Android JavaScript export passes: **703 modules**.
  It is not an APK, device interaction or proof of native gesture behaviour.
  Generated build output was removed after validation and a secret scan.
- `git diff --check` passes. Configured credential scan found no values in source
  or the generated bundle; `.env` and durable civic data are ignored.
- No server restart, credentials change, console operation, Git commit/push/reset,
  new native build or WhatsApp delivery verification occurred.

Live tourism through actual Flask `/v1/chat` -> Groq -> Places/Open-Meteo -> actual
frontend adapter passed three scenarios:

| Scenario | Real returned stops | Photo URIs | Measured endpoint + adapter |
| --- | ---: | ---: | ---: |
| Nature six-hour plan + vegetarian food | 3 including food | 3 | 8.2854 seconds |
| Shorten retained plan to three hours | 2 including food | 2 reused | 0.3820 seconds |
| Heritage full-day plan | 5 | 2 | 3.8991 seconds |

Successful run: two Groq extractions, three Places searches, two origin resolutions,
and cached Open-Meteo access. An initial Groq timeout was retained as a previous
attempt. No key or timeout was changed to mask it. The artifact stores only counts,
statuses and timings, not Google place/photo content. Photo-media URIs were obtained
live and accepted by the frontend adapter; no image display on a device was verified.
These timings are not physical Android end-to-end latency.

The user-provided current tunnel is
`https://bomb-occurred-silent-heated.trycloudflare.com`; frontend `.env` already held
that URL when checked. A read-only `/health` returned HTTP 200 and a healthy API
envelope. No `TWILIO_PUBLIC_WEBHOOK_URL` was present in backend `.env`; none was added.
Health alone does not establish that a running non-reloading Python process has
loaded newly edited routes.

**Physical Android testing was not performed by this agent.** SecureStore persistence
on the actual phone, picker permissions/upload, pan/pinch/Back/parent scroll, native
Directions and native sharing require the checklist below. No live civic user record
was created just to claim production verification.

## Team checklist and remaining work

1. Keep the current tunnel/API keys. Reload Expo Go for the latest JS. If Flask is
   running without automatic reload, reload that existing backend process once to
   load the new route registrations; this agent did not terminate it or start a
   competing server. Dependencies are installed in this workspace.
2. Ask for a six-hour nature/vegetarian plan, shorten it, add Lalbagh, change area;
   inspect photos/credits, numbered map markers and selected-place Directions.
3. Describe a pothole in chat; Open Report Issue. Verify description/location,
   confirm an exact point or save text-only, choose visibility, optionally add a
   photo and explicit public-photo permission. Submit once; note actual reference.
4. Profile → My reports: verify record and photo. Reload app and backend in a
   controlled test; the reference/history must remain. Text-only reports must not
   produce a guessed marker. Private records must not appear on another installation.
5. Use a deliberately public test report, ask for nearby issues in its locality,
   inspect matching marker details. Tap With You twice to set/unset; reload and
   compare count/state between the map and community feed. Another installation
   may support once independently. Native Share should contain public text/ID only.
6. Create a post linked to that existing report; verify My posts/community feed and
   the same report's support count. Confirm News labels it community information.
7. Retest hotels, mixed food/hotels, source photographs, weather/hourly details,
   transport demo labels, session followups and original home/nav/scroll behaviour.

No new external account, API key, paid routing API, native signing information or
manual asset is required. Places/photos keep their existing billable provider
behaviour and bounded requests. There is no route geometry, verified ticket/menu
price, live transit guarantee, official complaint transfer, status administration,
account recovery, moderation workflow, push notification system or new city.
Those boundaries must remain explicit during the demonstration and future rollout.
WhatsApp ASR, templates, multilingual delivery, secure linking and identity binding
remain for the WhatsApp engineer; existing messaging functionality was preserved.
