# Recorded tool-execution regression excerpt

`nano_premature.relative-report.json` is a privacy-minimized excerpt of a real
LiveKit Agents 1.8.8 Nano recording, **not synthetic data**. It is test-only:
it is outside the application session store and does not add an eighth demo session.

This is a minimal importer-compatible projection, not a complete SDK export.
All timestamps are seconds relative to recording start (zero origin). The existing
importer creates a normalized Session in memory; no normalized session is stored.

The excerpt retains the actual three conversation messages, closing-policy block,
allowlisted configuration/usage/metrics, and normalized timeline observations.
State observations preserve the original normalized event/evidence numbering.
The two distinct end_call dispatches and their done outcomes retain their actual
relative timing and ordering. Call IDs are deterministic session-local aliases;
original call IDs, request items, batch summaries, arguments and outputs are omitted.

One clinician name in the initial message is replaced by `[clinician]`; neither
quoted finding-evidence turn is changed. The unrelated persona/profile instructions
are omitted. No audio, audio paths, absolute dates, room/job identifiers, provider
deployment metadata or free-text tool payloads are retained.

Validation against the private source confirmed identical normalized events,
configuration, models, statistics and premature_end_call findings. A later real
Recommended recording was tested in memory as a negative control and is not retained.
Dispatch and done status establish tool execution, not immediate audible disconnection.
