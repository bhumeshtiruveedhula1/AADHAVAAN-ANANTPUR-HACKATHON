# Milestone 03.1 result

The correction and live comparison are complete. Use `openai/gpt-oss-120b` by
default, with `GROQ_MODEL` retaining explicit configuration of either tested
model. The source default, `.env.example`, and only the model setting in the
existing ignored `backend/.env` were updated. Credentials were not printed.

## Evidence and failure boundaries

The original five reported model payloads were not saved in the repository.
Their exact rejected values therefore cannot all be reconstructed. Do not
interpret the following controlled reproduction as proof of the original
provider output.

- **Accommodation:** the old validator raised `ValueError: Ungrounded budget`
  at `intents.py:137` for a normalized `budget.raw_text="1500 INR"` against
  the supplied phrase `1500 rupees`. Other optional normalized values could
  similarly invalidate the entire request. This specific boundary was
  reproduced before replacement. The original failing field remains unknown.
  In the new live data, 20B omitted Majestic; 120B extracted the relevant fields
  but surrounded its evidence phrases with quotation marks. The first local
  validator rejected those wrappers. Quote normalization restores grounded
  fields; numeric budget/currency normalization now uses supporting evidence.
- **Transport:** the old response layer copied destination directly and added
  a missing-destination question when it was null. It did not remove an already
  valid destination. New live 20B output for case B had an empty entity list;
  120B supplied destination and metro mode with quoted evidence. Revalidation
  preserves Majestic/metro and asks only for origin with Bengaluru context.
- **Pothole:** classification is preserved, a personal college reference remains
  insufficiently precise, and no report is created. Both the mocked regression
  and corrected 120B live-output replay pass.
- **Weather:** the old validator accepted absent time information and delegated
  missing-field decisions partly to the model. New live 20B again omitted
  entities; 120B supplied the time with quoted evidence. Python now owns all
  clarification, normalizes quoted evidence, and preserves explicit city/time
  spans without treating a landmark as a known city.
- **Tourism:** the old model-selected `city_guidance` value was valid according
  to the schema, and Python did not reclassify it. The revised instructions
  explicitly distinguish visits/itineraries from general newcomer guidance.
  Both tested models now classified case E as tourism, but omitted duration.
  A generic literal duration grammar preserves the stated one-day constraint.

The compact schema has three top-level fields: `primary_intent`,
`secondary_intents`, and `entities`. Every entity has `type`, `value`, and
`evidence`. There are no model-produced handlers, actions, missing-field lists,
HTTP statuses or execution claims. Existing downstream fields are composed in
Python. Optional entity errors produce safe field-path warnings and do not
discard independent valid entities. Invalid routing/schema remains a separate
error; unsupported intent remains a valid classification requiring clarification.

Literal duration/time/city preservation is deliberately small. It matches
explicit phrases and records `source=literal_normalizer`, never chooses an
intent, resolves GPS or fetches city facts. Evidence-derived entities are marked
`groq`; city reconciled from explicit request context is marked `request_context`.

## Live comparison and local revalidation

Exactly **50 Groq calls** were attempted: **25 per model**. Both models received
the same 25 fixed cases, prompt, strict schema, temperature 0, low reasoning
effort, excluded reasoning and completion cap. Calls were interleaved and paced;
there were no automatic retries, second-model cascades or additional diagnostic
inferences. One 20B provider error stopped the first segment after 17 calls.
Only the remaining 33 pairs were subsequently attempted. The failed request was
not repeated. Its earlier diagnostics did not retain an HTTP status, so it is
reported as a provider error, not assumed to be a rate limit.

The immutable original responses/scores are in `live_results.json`. During the
benchmark, quoted evidence was diagnosed and local normalizers were corrected.
All saved compact outputs were then passed through the same final Python
validator in `revalidated_results.json`, with **zero additional Groq calls**.
This separates model omissions from recoverable Python validation losses. The
original failures have not been overwritten or hidden.

| Measure | GPT-OSS 20B | GPT-OSS 120B |
|---|---:|---:|
| Primary intent correct | 24/25 (96%) | 25/25 (100%) |
| Secondary intent set correct | 24/25 | 25/25 |
| Explicit required entity slots correct | 35/50 (70%) | 48/50 (96%) |
| All required slots including expected unknowns | 40/55 | 53/55 |
| Explicit entities absent | 15 | 1 |
| Unsupported/ungrounded optional normalizations rejected | 0 | 1 |
| Schema/validation failures | 0 | 0 |
| Provider errors | 1 | 0 |
| Timeouts / rate limits | 0 / 0 | 0 / 0 |
| Core cases passing all checks after revalidation | 3/5 (C, D, E) | 5/5 |
| Live backend median | 1.007 s | 1.109 s |
| Live backend p95 (nearest rank) | 1.221 s | 2.129 s |
| Quality gate after local revalidation | Fail | Pass |

Latency is the actual monotonic `process_text()` measurement, including Groq
HTTP time, validation and response mapping for the live call. Eight-second
pacing is excluded. These are the original measured live durations, not fresh
latencies for the final revalidation pass; local replay duration is stored
separately. They are not Android network measurements or token-generation rates.

120B was selected because it retained 96% of the important explicit entities
versus 70%, passed all five core cases, and cost about 0.102 seconds more median
latency in this run. Its p95 was higher, and the sample is too small to guarantee
accuracy or latency outside this dataset. The choice is empirical, not based
on parameter count or advertised throughput.

## Remaining failures

- 20B failed cases A, B, 06, 09, 11, 12, 16, 18 and 19. Case 09 is the preserved
  provider error; other failures principally omit stated location/route/issue
  entities. Full per-case fields remain in the JSON artifacts.
- 120B case 08 returns the narrower accessibility value `wheelchair` rather than
  the expected `wheelchair accessible`. The strict scorer keeps this as a
  failure; it was not adjusted after observing the output.
- 120B case 16 omits `beside Lalbagh main gate` as the incident location and
  unnecessarily asks for a location. This real extraction omission remains.
- 120B's extra `check-in` normalization in case 06 is rejected because its value
  is not directly grounded in the supplied `check into` phrase. Other valid
  entities survive. This is an optional normalization rejection, not evidence
  that a fabricated city result was returned.

## Verification and scope

Final verification: **48 backend tests passed, zero failed**. TypeScript,
ESLint and `git diff --check` passed. The call ledger was independently checked
for exactly 50 unique case/model pairs, 25 per model, and an unchanged
prompt/schema/dataset fingerprint.

Backend tests exercise the five fixed regressions, held-out dataset structure,
single-call SDK path, both models, strict schemas, optional recovery, safe
diagnostics, normalization, ledger limits/resumption, and all prior Flask/Twilio
signature, duplicate and bounded-worker behavior. Ordinary tests mock Groq and
Twilio; the separate benchmark above used live Groq.

Modified implementation: `intents.py`, `groq_provider.py`, `service.py`, model
configuration and backend documentation. Added the dataset, benchmark runner,
zero-call revalidation utility, regression/benchmark tests and result artifacts.
Android UI, API route, Twilio route/signatures/sender, 64-item queue, deployment
and Cloudflare setup remain unchanged in this correction. No new dependency,
city service, report persistence, ASR or external account operation was added.
Live WhatsApp delivery and reliable Hindi/Kannada understanding are unverified.
The in-memory queue/deduplication remain non-durable. No Git history operation,
commit or push was performed.

Model support references: [Groq strict outputs](https://console.groq.com/docs/structured-outputs),
[GPT-OSS reasoning parameters](https://console.groq.com/docs/reasoning),
[published rate limits](https://console.groq.com/docs/rate-limits).
