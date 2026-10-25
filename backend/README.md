# Smart Nagrik local backend (Milestone 04.1)

This Flask API serves Android chat requests and the signed Twilio webhook through
one `process_text(TextRequest)` boundary in `service.py`. The Twilio adapter
reuses the existing connector's signature validator and outbound Messages API
function in `../whatsapp-integration/app.py`. Run only this backend server for
the integrated flow; the old standalone connector remains as a proof of concept.
The hybrid intent engine routes read-only requests to Open-Meteo weather, configured
Google Places discovery and Google Maps URL handoff. Python composes the response;
there is no second LLM call. Civic persistence and action execution remain unbuilt.
See [MILESTONE041.md](MILESTONE041.md) for working result cards, attribution,
photo integration, Directions, inline-map configuration and current verification.
[MILESTONE04.md](MILESTONE04.md) retains the earlier backend contract/history.

## Start on Windows

From `backend/` in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

By default, the API listens at `http://127.0.0.1:5000`. Check
`http://127.0.0.1:5000/health`. To let a physical Android device on the same
trusted Wi-Fi reach it, start the backend with:

```powershell
$env:SMART_NAGRIK_HOST = '0.0.0.0'
.\.venv\Scripts\python app.py
```

Set `EXPO_PUBLIC_API_URL=http://<your-laptop-LAN-IP>:5000` in
`frontend-app/.env.local`, then run `npx expo start` from `frontend-app/` and
reload the app. For an Android emulator, `http://10.0.2.2:5000` is the usual
host loopback address. Windows Firewall may need to allow inbound TCP 5000 on
your private network. The API URL is public app configuration, never a secret.

`SMART_NAGRIK_CORS_ORIGINS` can be a comma-separated list of exact web origins
for local browser development. Native Android requests do not need CORS.
Environment variables are read from the process environment or `backend/.env`.
Copy `.env.example` to `.env` and fill in only the values you use. Never put
Twilio credentials in `EXPO_PUBLIC_*` variables.

## Contract

- `GET /health`: `data.status = "ok"`
- `GET /v1/cities`: Bengaluru is the only supported real-data city
- `POST /v1/chat`: JSON `{"message":"...","city":"Bengaluru"}`
- `POST /twilio/webhook`: signed Twilio form request (`Body`, `From`, `MessageSid`)

All responses use `{"success": boolean, "data": ..., "error": ...}`.
The chat `data` object contains `type`, `title`, `description`, `details`,
`places`, and `request_id`. Missing Groq configuration or an unconfigured
Places-only request responds with `success: false`,
`error.code: "service_not_configured"`, and an honest displayable `data` object.
Other city lookup failures/partial results use explicit data/section status codes.
Clarification can return HTTP 200 without claiming
that an action succeeded. Verified WhatsApp text reaches the same processor,
with its channel, provider message ID and sender/session reference. No city is
assumed from the phone number.

## Twilio and Cloudflare

Start the backend on port 5000, then, in a second terminal, start a tunnel:

```powershell
cloudflared tunnel --url http://localhost:5000
```

In Twilio Console, set **WHEN A MESSAGE COMES IN** to `POST` at
`https://<your-tunnel-host>/twilio/webhook`. Set the exact same full HTTPS URL
as `TWILIO_PUBLIC_WEBHOOK_URL` in `backend/.env`, then restart the backend.
The same tunnel hostname also forwards `/v1/chat` and other API paths.
Quick Tunnel hostnames change on restart, requiring both values to be updated.
No tunnel or console setting is created by this code.

Set `TWILIO_AUTH_TOKEN`, `TWILIO_ACCOUNT_SID`, `TWILIO_WHATSAPP_NUMBER`,
`TWILIO_CONTENT_SID` and explicit JSON `TWILIO_CONTENT_VARIABLES` for your
approved trial template. Use `{}` if that template has no variables. The
adapter will not use the connector's freeform-text fallback when template
settings are missing. Its outbound body is the approved template, not a live
AI answer. Twilio's initial `queued` or `sent` status is not delivery proof.

Verified messages are queued in memory and answered outside the webhook request.
`MessageSid` is deduplicated for 24 hours while this one server process runs;
the deduplication state is lost on restart. Keep one backend process running
for this local milestone. Incoming voice notes are acknowledged but not processed.

## Tests

From `backend/`:

```powershell
.\.venv\Scripts\python -m unittest discover -v
```

These tests use Flask's local test client, mocked Groq HTTP responses through
the official SDK, and mocked Twilio outbound calls. They require no live
credentials. They test the integration and validation, not live model accuracy.

## Hybrid intent understanding (Milestone 03.1)

Set the server-only key in `backend/.env`. Model selection is configurable:

```ini
GROQ_API_KEY=YOUR_GROQ_API_KEY
GROQ_MODEL=openai/gpt-oss-120b
GROQ_TIMEOUT_SECONDS=6
```

The measured default is `openai/gpt-oss-120b`; both `openai/gpt-oss-20b` and `openai/gpt-oss-120b` remain allowed. The official
Groq SDK uses strict JSON schema, temperature 0, low reasoning effort, excluded
reasoning output, and one extraction call per free-form request. No model
cascade, repair inference, or automatic SDK retries are used.

The compact provider object has only `primary_intent`, `secondary_intents`, and `entities`.
Each entity has a supported `type`, `value`, and directly supporting `evidence`.
All schema properties are required and objects forbid extra properties.
Entity types cover city, area, landmark, origin, destination, budget, currency,
time, duration, transport preference, civic category, exact location, explicit
preference, accessibility, and report reference. Unknown entities are absent.

The intent enum remains accommodation, food_places, places_services, transport,
weather_conditions, civic_report, civic_lookup, tourism, emergency, city_guidance,
and unsupported. Groq performs semantic classification/extraction only. Python
normalizes evidence and values, merges supplied context, derives missing
information, selects fixed handlers, and composes the public response.

Pydantic rejects unusable schemas with safe field-path diagnostics. Invalid
independent optional entities are dropped with `validation_warnings` while
valid information survives. Currency codes need currency evidence; budget
amounts need matching digits. Quotation wrappers around evidence are normalized.
Time normalization preserves original phrases rather than inventing dates.
A small literal duration grammar preserves stated unit-bearing spans; a literal
Bengaluru mention can establish the named city, but a landmark or phone number
cannot. These normalizers never select an intent or infer GPS coordinates.

The existing internal fields remain available in `data.understanding`,
including primary/secondary intents, original text, nullable language, city,
area, landmark, origin, destination, exact_location, report_id, budget,
time_constraint, preferences, accessibility_constraints, civic_category,
missing_information, clarification_needed, suggested_service_handler, action,
method, and processing_status. Additive fields include duration,
transport_preference, validated entities, and validation_warnings.
Language remains unknown rather than claiming tested multilingual accuracy.

Python asks only for necessary city, route origin/destination, incident location,
or action/report reference. Unsupported goals ask one useful question.
Missing optional information does not invalidate classification. Future handler
names are fixed routing labels. Milestone 04 adds bounded city lookups after
validation; explicit action execution remains unimplemented.

Exact action phrases or a validated explicit `action` bypass Groq. Action IDs
remain report_issue, with_you, view_report_status, open_directions, and select_city.
The API still accepts the original message/city request, with optional action
and bounded context (three recent messages plus explicit location/report fields).
No persistent history or cross-channel identity linking exists.

Missing key: HTTP 503 / service_not_configured. Provider timeout: HTTP 504 /
intent_timeout. Other provider errors have safe distinct intent_* statuses.
An invalid extraction schema retains intent_invalid_output for API compatibility
and exposes interpretation_status=invalid_schema with safe diagnostics.
A valid unsupported request, a request needing clarification, and an understood
request with optional warnings remain distinct in data.understanding.
Unavailable providers retain truthful limitations. Configured read-only weather
and Places providers can supply results; no action success is claimed.

Groq read/write timeout defaults to six seconds (range 0.5–8), with a two-second
connection timeout and two simultaneous calls per process. WhatsApp retains its
single worker, 64-item bounded queue, signature checks, in-memory deduplication
and approved template sender. Jobs and deduplication are not durable across
restarts. Durable records are required before consequential civic persistence.

See [evaluation/README.md](evaluation/README.md) for the 25-case dataset, bounded
live benchmark, immutable call ledger, zero-call revalidation, scoring and
measured results. Original live failures are retained; replayed scores must not
be represented as fresh inference or fresh HTTP latency measurements.

See [Final pre-WhatsApp implementation](PRE_WHATSAPP_IMPLEMENTATION.md) for tourism,
durable civic reports/media, community/support APIs, test evidence and device checks.

See [Milestone 04.1.2](MILESTONE0412.md) for bounded session context, dynamic
transport endpoints, in-place map expansion, contextual replies and verification.

Groq references: [strict outputs](https://console.groq.com/docs/structured-outputs)
and [GPT-OSS reasoning settings](https://console.groq.com/docs/reasoning).
