# Controlled synthetic fault fixtures

These three **test-only, authored normalized sessions** are not recordings, production calls, or additions to the seven recorded/sanitized demo sessions. They exercise the existing `Session` schema and deterministic detectors directly; no raw LiveKit importer, model, STT engine, audio, or external service is run.

Each JSON document separates the test oracle from the detector input:

- `fixture_id`, `injected_fault_type`, and `description` identify the controlled experiment.
- `injection` names one turn and its clean `baseline_text` and faulty `injected_text`.
- `expected` declares the detector, category, status, and required evidence turn IDs.
- `session` is the faulty normalized `Session`. Both `synthetic` flags are true, IDs/titles/notes clearly mark its provenance, and provider/model fields remain unspecified.

Tests reconstruct a clean control by restoring just `baseline_text`, verify that it produces no findings, and verify that applying the declared mutation produces exactly the intended finding through the complete detector registry. Oracle metadata is never supplied to detectors. These files live outside `backend/data/sessions/`, are never saved to the application store, and must not be copied into demo data or frontend mocks.

| Fixture | Controlled fault | Required detector semantics |
|---|---|---|
| `stt_missing_time_number.json` | Authored reference `Thursday at 8 AM.` becomes simulated transcript `Thursday at AM.` | A user transcript contains `at AM` without an hour. The detector still returns **suspected**, with a **likely** root cause and its reference/audio evidence gap. The known omission belongs to the test oracle, not the detector's observations. |
| `readback_mismatch.json` | After Tuesday is explicitly changed to Thursday, the final readback incorrectly says Tuesday; the hour remains unchanged. | A separate user request to repeat the appointment precedes the faulty reply. Evidence links the latest Thursday correction, readback request, and Tuesday response. Failure **confirmed**; cause remains **possible**. |
| `premature_goodbye.json` | A helpful reply to an unresolved directions request is replaced with `Goodbye.` | The preceding user has no closing cue; a subsequent substantive user request proves the conversation continued. Failure **confirmed**; cause remains **possible**. |

Turn times follow an explicitly artificial five-second schedule. The fixed `source.imported_at` is a fixture constant, not an actual import timestamp. Latencies, transcript confidence, SDK version, duration, and model names are not fabricated measurements. Counts follow the authored turns; no audio, tool calls, or timeline events are invented.

This validates recognition of these specific controlled faults, not real-world accuracy, recall, or causal attribution. Recorded-session STT findings cannot normally prove an error without audio or reference ground truth; none of these fixtures changes that rule.
