# Milestone 04.1 — existing Android results integration

Implemented 25 October 2026. Real place records now reach result cards. Google
photo retrieval and the frontend contract were verified live. Actual Android
image rendering, map tiles, external app launch and physical scrolling remain
device checks; no device or APK test is claimed.

## Implemented result behaviour

The existing result card retains its title, detail pills and styling. Place
requests use a short source-grounded introduction, followed by the backend's
primary-first sections. Each section contains its actual names, addresses,
category, bounded place cards, credits and limitations. Stable card keys are
provider place IDs. The same establishment can appear under two relevant needs
without mislabelling its category; the map deduplicates shared coordinates by ID.

Cards have real Directions buttons and Google Maps source controls. URL validation
requires HTTPS, the official Maps directions path, `api=1`, nonempty destination,
matching place ID for place buttons, and a supported travel mode. Linking failures
show an alert and retain the successful result. Other source links permit HTTPS,
reject embedded credentials/key parameters and have explicit accessible labels.
No model-generated action types or route/ETA/price claims were added.

The old API adapter display block is removed because the new renderer supplies
Google attribution. It still validates the envelope/result type, strings, arrays,
place identities, source, category ownership, coordinates, links and credits.
It bounds the response to two sections and six records, and discards unsafe
optional photos/links without discarding valid cards.

Google Maps text is shown with each Google card, inside the white card container:
normal system sans serif, 12sp, weight 400, `#5E5E5E`, normal letter spacing, no
line limit. Third-party names and available credit links are visible. These are
local attribution styles; no global font/colour/theme changes were made. This
follows the text-attribution option for constrained interfaces in the
[official Places policy](https://developers.google.com/maps/documentation/places/web-service/policies).
Public Terms of Use and Privacy Policy incorporating the required Google terms
must still be supplied before public rollout. This milestone does not add legal
screens or claim the whole product has completed a compliance review.

## Real photographs

The existing Text Search field mask gains only `places.photos`. For up to two
valid visible records per search, the backend selects provider-returned metadata
whose resource name belongs to that exact place ID. It requests:

```text
GET https://places.googleapis.com/v1/places/{place_id}/photos/{photo_resource}/media
    ?maxWidthPx=800&maxHeightPx=400&skipHttpRedirect=true
X-Goog-Api-Key: [server environment only]
```

The returned HTTPS `photoUri` is validated against Google image hosts. The server
key is never placed in the URL or frontend JSON. Media requests have a 1.5-second
network-phase timeout, no retries and the existing global concurrency limit.
Cross-place resource names, malformed author/source metadata and unsafe image
URIs are rejected. Media failure leaves the listing usable with no photo.

Additive fields on each place:

```text
image: string|null
photo: {url, source_url|null, authors:[{name, uri|null, avatar_url|null}]}|null
photo_status: available|unavailable|timeout|rate_limited|...safe provider code
```

Photo-author names, supplied avatars and profile links appear directly below the
photo. A supplied individual `googleMapsUri` becomes a **View original photo**
control, rather than substituting the general place page. Author avatar failure
does not remove the name/link. Photos have a reserved 155dp card image area; failed
images become a same-height “Photo unavailable” region. Cards without images
remain readable and usable. `expo-image` uses `cachePolicy="none"`; there is no
prefetch, photo-name cache, permanent file storage or render-triggered backend
request. Memoised cards retain the same image instance on ordinary rerenders.
See [Place Photos (New)](https://developers.google.com/maps/documentation/places/web-service/place-photos).

Single intent: at most five cards and two media calls. Mixed request: three cards
per section, six total and four media calls. Not every listing will have an image
even if Google has one, because of this explicit request budget. Photo URLs may
expire; failure is shown without automatic billable refresh. Photos are never
treated as evidence of room vacancy, price, accessibility or opening status.

## Inline map and native setup

Expo SDK 58 dependencies were installed with `expo install`:

- `react-native-maps` 1.29.11
- `expo-image` ~58.0.17
- `expo-constants` ~58.0.13
- React-matching `react-test-renderer` 19.3.0 (development testing only)

The Android map is a 180dp section within the same results scroll view, after
cards. It uses **Google Maps**, at most six actual provider coordinates and a
viewport fitted from those coordinates. It has no user-location permission or
GPS request, civic flags, separate map screen or new navigation. Pan, zoom, rotate,
pitch and the native toolbar are disabled to preserve the parent scroll flow.
Initial viewport state is stable for the result and resets only for a new result.
Google's native attribution is not obscured. Third-party place credits remain
visible on the associated cards.

No coordinates means no map. An absent native module is isolated by a React error
boundary. A map that never loads falls back after 12 seconds. Missing custom-build
configuration shows a compact unavailable message; cards and Directions continue
to work. This cannot guarantee recovery from every native SDK/OS crash, nor can
a tile-load callback prove credential correctness on every device.

**Expo Go:** the SDK 58 documentation says no additional map-key setup is needed
for its included native maps module. This path is enabled in code but was not
tested on a device here.

**Custom Android binary:** supply a separate mobile key as
`GOOGLE_MAPS_ANDROID_API_KEY` in ignored `frontend-app/.env.local` or the build
environment. Enable **Maps SDK for Android** for that key. Restrict the key to:

- Android application package **`com.smartnagrik.app`**.
- The **actual SHA-1 signing certificate fingerprint** for the intended build.
  Development/debug and Play App Signing/release certificates may differ; register
  the fingerprints actually used. None was guessed or generated in this task.
- Maps SDK for Android API usage, not the backend Places Web Service credential.

`app.config.js` injects that mobile key into the `react-native-maps` config plugin
only when configured, and exposes only a boolean readiness flag in `extra`.
Do not reuse `GOOGLE_PLACES_API_KEY`. Rebuild the native binary after configuring
the key; a JavaScript reload/OTA update cannot add missing native configuration.
No new app binary, keystore, Cloud project or billing settings were created.
See [Expo SDK 58 map setup](https://docs.expo.dev/versions/v58.0.0/sdk/map-view/).

iOS and web do not display Google Places on an Apple/non-Google map. The requested
Android map integration is implemented; other platforms retain cards and external
links. No Google Maps logo asset is missing: the permitted styled text option is
used. The missing custom-build items are the restricted mobile key, actual signing
fingerprint configuration and subsequent device verification.

## Exact frontend preservation

`App.tsx` changed in exactly two locations:

1. Added `import CityResults from './components/CityResults'`.
2. Inside `ResultView`, the existing body reads `result.summary || result.description`
   and mounts `CityResults` after the unchanged summary card.

No `App.tsx` style, shell component, input handler, timer, scroll/transition code,
hero asset, typography, navigation or demo was edited. The original file can be
reconstructed by reversing these two edits; its SHA256 matches the recorded
pre-work value `81B8D7E8C1B7D1C503663D144B9F8AF6A7C58F2B843F7140F1BCFF93427A117E`.
Automated prefix/suffix hashes verify the untouched screen regions. This proves
source preservation, not physical gesture smoothness on a device. No animation
or parallax behaviour absent from the actual source was added or claimed.

All new visual styles are confined to `CityResults` and `InlinePlacesMap`, using
the existing 16dp card radius, 14dp padding, 155dp image area, Manus colour/font
tokens and existing action treatment. `app.json` changed only by the Expo installer
adding the `expo-image` plugin; package ID, assets, orientation and branding remain.
Intent validation, GPT-OSS 120B provider and Twilio adapter hashes are unchanged.

## Verification

| Check | Actual result |
| --- | --- |
| Backend unit/integration suite | **96 passed**, including 9 new photo tests |
| Actual React result components | **17 passed**, using mocked native surfaces and no network |
| Actual API adapter/envelope tests | Passed, including real live response payloads passed through stdin |
| TypeScript | Passed |
| ESLint, including new components/config/tests | Passed, no warnings |
| Android Metro/Hermes export | Passed; local JS bundle, not APK/device verification |
| `git diff --check` | Passed; only existing line-ending notices |
| Secret-value scan | 0 matches in 15 changed application-source files |
| `.env`, `.env.local`, local export | Confirmed ignored |
| Android device / emulator / native tile rendering | **Not tested**; no adb/device interface available |
| Live WhatsApp delivery | **Not tested**; existing signature/queue/template mock tests passed |

React tests execute the actual `CityResults`, native-map component and adapter,
with host RN/Expo/Linking components mocked. They test selected-place Directions,
opening failure, attribution, image failure without card loss, author/source access,
hotel/food separation, partial failure, weather without maps, transit preference,
map bounds/gestures/stable viewport, missing native configuration, native module
failure, timeout fallback, rerender identity and original-screen preservation.
React's test renderer emits its upstream deprecation notice; no native device
behaviour is inferred from those tests. Node's isolated runner was sandbox-blocked,
so the same `node:test` cases were run directly without child-process isolation.

The first sandboxed export could not spawn the Hermes compiler (`EPERM`). The
network/permission-enabled local retry passed. Nothing was deployed.

### Small live provider check

| Request | HTTP/status | Places | Photo media URLs | Endpoint time |
| --- | --- | ---: | ---: | ---: |
| Hotels near Majestic | 200 / ok | 5 | 2 | 2.6360 s |
| Restaurants near Indiranagar | 200 / ok | 5 | 2 | 1.9486 s |
| Hotel under ₹1,500 at 4 AM plus food | 200 / ok | 6, accommodation first | 4 | 3.4791 s |

Total: **3 Groq calls, 4 Places searches, 8 photo-media calls and 3 image downloads**.
Every case's actual JSON passed the current TypeScript adapter, retaining place
cards, category ownership and valid destination/place-ID links. All eight photo
records included authors and individual Google photo source links. One image per
case was fetched into memory and verified by HTTP status, image content type and
JPEG/PNG/WebP signature. No listing content, image bytes or photo resource names
were persisted. Only metrics are in
[`evaluation/milestone041_live.json`](evaluation/milestone041_live.json).

These timings include the Flask processing path and photo metadata/URI retrieval,
but exclude device rendering, native maps, image downloads and external app launch.
They are individual smoke measurements, not percentiles or universal latency
claims. Weather was not called in these place-only cases; its existing Open-Meteo
integration and tests remain intact. No old model benchmark was repeated.

## Files changed in 04.1

Modified:

- `backend/places_service.py`, `backend/city_services.py`
- `backend/test_frontend_contract.cjs`
- `backend/README.md`, `backend/MILESTONE04.md` (historical-report pointer only)
- `frontend-app/App.tsx`, `frontend-app/api/chat.ts`
- `frontend-app/package.json`, `frontend-app/package-lock.json`
- `frontend-app/app.json`, `frontend-app/.env.example`

Added:

- `frontend-app/components/CityResults.tsx`
- `frontend-app/components/InlinePlacesMap.tsx`
- `frontend-app/components/InlinePlacesMap.native.tsx`
- `frontend-app/components/mapRegion.ts`
- `frontend-app/app.config.js`
- `frontend-app/tests/city-results.test.cjs`, `frontend-app/tests/accept-response.cjs`
- `backend/test_place_photos.py`, `backend/smoke_place_results.py`
- `backend/evaluation/milestone041_live.json`
- `backend/evaluation/milestone041_preservation.json`
- `backend/MILESTONE041.md`

No changes to real credentials, `service.py`, `intents.py`, `groq_provider.py`,
`weather_service.py`, `twilio_adapter.py` or existing civic demonstrations.
No Git commits, branches, pushes, reset, stash or history rewriting.

## Team handoff and cost

Restart the backend and reload Expo after installing updated dependencies. Use
the existing `EXPO_PUBLIC_API_URL` that the Android device can reach. Places and
Photos use the now-working backend credential. Normal Places/Photos usage is
billable according to the account and requested fields; media retrieval adds
requests. No prices/free allowances are assumed. See
[Google usage and billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing).
The only new field is `places.photos`; no ratings/reviews/hours or second LLM call.
Maps SDK usage follows the team's enabled mobile project terms. Existing Open-Meteo
noncommercial-use/attribution requirements continue to apply.

Useful local verification commands:

```powershell
# backend/
.\.venv\Scripts\python -m unittest discover -q
.\.venv\Scripts\python app.py
# frontend-app/
npm run test:results
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js App.tsx api components app.config.js tests
npm start
```

The live smoke script is explicit and refuses to overwrite its existing evidence;
it is not part of unit tests and does not silently repeat billable calls. Team
device acceptance should check A–I from the prompt, especially image display,
credit readability with font scaling, external Maps handoff and parent scrolling.

Booking/availability, civic persistence, issue flags, With You counts, community
feed storage, WhatsApp voice/secure handoff, hotel Business API and advanced tourism
remain unimplemented. The existing approved-template WhatsApp sender is unchanged;
new Android media is not claimed to be delivered through WhatsApp.
