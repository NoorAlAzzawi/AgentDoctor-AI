# AgentDoctor API contract (v0.1)

The backend serves JSON on `http://127.0.0.1:8000`. CORS allows `http://localhost:5173` (the Vite dev server). It is read-only: every endpoint is `GET`.

| File | What it is |
|---|---|
| `types.ts` | TypeScript types for every response. Copy these into the frontend unchanged. |
| `openapi.json` | Generated from the backend (`scripts/build_contract_examples.py`). |
| `examples/*.json` | Real responses from the backend's demo data, except `findings.illustrative.json` (see below). |
| `examples/by-session/<id>.session.json`, `<id>.findings.json` | Real detail and findings for every demo session, for frontend mock mode. |

The source of truth is `backend/src/agentdoctor/models.py`. A backend test validates every example file against it.

## Conventions

- **Times:** `*_s` fields are seconds since the session's recording started. Show them as `m:ss.s`.
- **Nulls:** `null` means the source data didn't contain the value. Show "n/a". Never substitute 0.
- **IDs:** turns are `t001…` and events are `e001…`, stable within a session. Evidence links to them through `turn_id` and `event_id`.
- **Speakers:** `"user"` is the person talking to the voice agent and `"agent"` is the voice agent. Display labels come from `session.speaker_labels`. In the demo data, the user is a human tester playing a receptionist, and the agent plays a patient.

## Endpoints

### `GET /api/health` → `Health`
Example: `examples/health.json`. `detectors` lists the detector functions that run.

### `GET /api/sessions` → `SessionSummary[]`
Example: `examples/sessions.json`. Summaries only, without turns or events. Sorted by `id`.

### `GET /api/sessions/{id}` → `Session`
Example: `examples/session.json` (`s03-workshop-ab-script`). Contains `turns` (the transcript) and `events` (timeline: agent and user speaking states, false interruptions, overlapping speech, tool execution, session close), sorted by time. Unknown id returns `404 {"detail": "..."}`.

### `GET /api/sessions/{id}/findings` → `FindingsResponse`
- **Real detector output:** `examples/findings.json` (s03) and `examples/by-session/<id>.findings.json` (every session). Findings are sorted by severity (high first), then time. A session with no problems returns `findings: []`.
- `detectors_run` lists detector *functions*. One function can emit findings with more than one `detector` name: `endpointing_timeout` also emits `fragment_reply`.
- **Legacy:** `examples/findings.illustrative.json` is the hand-assembled Phase 2A example for s03. It's kept so existing mocks don't break. Prefer the real files. If you still show it, label it as illustrative.

### Detector catalog

| `detector` | Category | Status | Fires when |
|---|---|---|---|
| `false_interruption_pause` | turn_taking | confirmed | A `false_interruption` event: the agent held or paused its reply for user voice activity that never became a transcript. Mid-reply pauses are `medium`; holds before speaking are `low`; `high` if the user then asked the agent to repeat. The echo root cause is offered only when the trigger began 0.05–0.6s after the agent started speaking. |
| `endpointing_timeout` | latency | confirmed | One per session: user turns whose `end_of_turn_delay_s` reached `max_endpointing_delay_s`. |
| `fragment_reply` | turn_taking | suspected | A timed-out user turn whose transcript looks unfinished, and the agent answered it. |
| `stt_missing_time_number` | speech_recognition | suspected | A user transcript with a time of day but no number ("Thursday at AM"). |
| `stt_misunderstanding` | speech_recognition | suspected | The agent signals confusion ("I'm sorry, what?") right after a user turn. Root cause is `likely` if the user then corrects it ("No, I said…"), otherwise `possible`. |
| `premature_goodbye` | llm_behavior | confirmed | The agent says goodbye, the user's previous turn had no closing cue, and the user then continues with a substantive turn. |
| `talked_after_farewell` | conversation_state | confirmed | Both sides said goodbye, but the agent kept speaking afterwards. |
| `stage_direction_spoken` | llm_behavior | confirmed | Agent text contains `(...)`, `[...]` or `*...*`, which TTS would read aloud. |
| `contradicts_instructions` | missing_or_incorrect_info | confirmed | The agent states a date of birth or phone number that differs from its own `agent_instructions`. |
| `readback_mismatch` | conversation_state | confirmed | When asked to repeat the appointment, the agent's weekday or time differs from the last one the user stated. |

**Speech-recognition findings are never `confirmed`:** without audio or a reference transcript, the true words are unknown.

### `GET /api/compare?a={id}&b={id}` → `Comparison`
Example: `examples/compare.json` (s03 vs s04, the same receptionist script on two pipelines).
- `metrics`: rows to show side by side. `better` says which direction is better (`lower`, `higher`, or `null` for no direction).
- `same_models`: which pipeline components match.
- `findings_by_category`: counts per side, from the detectors. A category appears only if at least one side has a finding in it.
- `caveats`: always show these with the comparison.

## Finding semantics (important for the UI)

A finding separates **what happened** from **why it happened**:

| Field | Meaning |
|---|---|
| `status: "confirmed"` | The failure is directly visible in the data, such as an event, a metric or a transcript. |
| `status: "suspected"` | The data is consistent with a failure but doesn't prove it. |
| `root_cause.confidence` | How strongly the data supports the *cause*: `confirmed`, `likely` or `possible`. A confirmed failure can have only a `possible` cause. |
| `root_cause.missing_evidence` | What would settle the cause. Always show it when the confidence isn't `confirmed`. |
| `evidence[]` | Ordered proof. Each item links to a turn or event when `turn_id` or `event_id` is set, so clicking it should scroll to and highlight that turn or event. |
| `next_steps[]` | Concrete follow-ups. |

**Categories:** `speech_recognition`, `llm_behavior`, `conversation_state`, `turn_taking` (including interruptions), `tool_execution`, `missing_or_incorrect_info`, `latency`.

## Data notes and limitations

- **Demo data:** 11 sanitized local console sessions. They contain no audio, file paths, job or room IDs, or provider request IDs. Original profile details are fictional; newer identities are pseudonymized and DOB values redacted. One role-confounded session is explicitly labeled.
- **User turn start times:** `start_s` for a user turn can be anchored to an earlier voice-activity blip, so it can start before the real speech. `committed_s` is when the transcript was finalized.
- **Console recordings don't show agent pauses.** Pauses are inferred from `agent_state` events, not audio.
- **Tool calls:** four application sessions contain normalized execution telemetry. Three have confirmed premature-dispatch findings; one is a legitimate-closing control with delayed assistant playback. The separate Nano regression excerpt remains test-only and is never counted as another application session. Tool findings require recorded execution telemetry; function-call requests and session closure alone are insufficient.

## Tool-execution telemetry

A normalized `TimelineEvent` with `type: "tool_execution"` has a stable event ID,
relative `t_s`, and allowlisted `data`: `tool_name`, session-local `call_ref`,
`phase: "started" | "ended"`, and `status: "done" | "error" | "cancelled"` only
when ended. Started events omit status; ended events require it. No extra tool
payload fields are accepted. Names match `^[A-Za-z_][A-Za-z0-9_-]{0,63}$` and
references match `^tc[0-9]{3,}$`. Tool-event timestamps must be finite.

The importer uses `tool_execution_updated` dispatch/completion records and replaces
raw call IDs with deterministic session-local references. It does not copy arguments,
outputs or raw identifiers, duplicate request/batch records, or infer execution from
closure. Dispatch, completion, transcript commitment and audio delivery remain distinct.

`premature_end_call` is confirmed/high/tool_execution only when actual dispatch
violates a recognized recorded both-sides-goodbye policy under conservative transcript
timing checks. A committed assistant goodbye can precede dispatch even when its audio
plays later. Unknown/conflicting policies abstain. Repeated premature dispatches share
one finding; root cause remains possible, and dispatch does not prove audible disconnection.

## Failure Fingerprints

`GET /api/fingerprints` returns deterministic observed-pattern groups, their original
findings/evidence, and explicit ungrouped reasons. A rule needs at least two distinct
recorded session IDs. `fp-v1-premature-end-call-dispatch` groups confirmed dispatch
violations only after revalidating source-linked evidence with the unchanged detector.
One occurrence is one finding, including repeated dispatches within that finding.
Unsupported, insufficiently evidenced or single-session findings remain ungrouped.
Recurrence is not a shared root cause or a failure rate; pipeline associations are
descriptive and repeated/shared scripts are not independent trials.
