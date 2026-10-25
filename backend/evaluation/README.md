# Intent evaluation

`intent_cases.json` contains 25 fixed synthetic English requests with expected
primary/secondary intents, important entities, and required clarification.
Cases A–E reproduce the five reported regressions. The other 20 requests vary
phrasing, intent, constraints, location and supplied context. None is embedded
as a prompt example or matched as a whole sentence in application code.

Ordinary tests mock Groq. Run them from `backend/`:

```powershell
.\.venv\Scripts\python -m unittest discover -v
```

The explicit live benchmark command uses the configured server-side Groq key:

```powershell
.\.venv\Scripts\python benchmark_intents.py
```

It interleaves 20B and 120B with the same prompt, strict schema, temperature 0,
low reasoning effort and output cap. There is one call per case/model pair,
at most 25 per model / 50 total. The default eight-second pacing is outside
the timed boundary and helps respect published token limits. SDK retries are
disabled. A monotonic timer surrounds the actual `process_text` call, including
Groq HTTP time, Python validation and response mapping. This is not Android
network latency or a token-generation-rate estimate.

The output is checkpointed before each call. Re-running refuses to overwrite
the ledger. `--resume` can continue only unattempted pairs after an interruption;
failed/reserved pairs never run again. A rate-limit stop cannot be resumed.
No unrelated or diagnostic provider requests are made. Model errors remain
failed cases and are included in denominators.

`live_results.json` preserves the original measured responses, compact raw
extractions, scores, failures and latency. Only synthetic dataset content is
stored, never authorization headers, keys or provider error bodies.

`revalidate_benchmark.py` applies current Python validation to those saved raw
outputs with **zero Groq calls**. It writes `revalidated_results.json` and keeps
the original artifact intact. This makes recoverable validation losses visible
without repeating billable inference. Its local replay duration is separate;
the `seconds` field retains the original live backend measurement. Report any
normalizer changes and distinguish original scores from replayed scores.

Scoring uses explicit expected alternatives, exact category/secondary sets,
and the expected missing-information set. `area|landmark` accepts either role
for proximity; transit destination is a separate required role. No fuzzy
semantic judge or second LLM is used. All required slots and explicit non-null
entity slots are counted separately, so correctly unknown fields do not inflate
explicit entity accuracy. Rejected ungrounded/unsupported values, missing
explicit entities, schema failures, provider errors, timeouts and rate limits
remain visible in the per-case records. Evidence checks cannot prove complete
semantic correctness; scores may conservatively reject equivalent wording.

The quality gate requires 25 completed cases, at least 90% correct primary
intents and explicit entities, and every core case passing all required checks.
This small dataset does not guarantee accuracy outside the tested requests.
