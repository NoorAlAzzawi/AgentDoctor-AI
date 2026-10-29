# Recorded application dataset

The application store contains 11 real console recordings: seven original sanitized
sessions plus four privacy-minimized additions. Synthetic fault fixtures and the
recorded Nano regression excerpt under `tests/fixtures/` are not store entries.
The excerpt and s08 derive from the same recording and must never count twice.

| Application ID | Source recording | Context |
|---|---|---|
| s08-nano-role-confounded-end-call | session-10-28-180550 | Human acts as patient; AI answers as receptionist despite its patient instructions. Two premature dispatches, one finding. |
| s09-recommended-closing-policy | session-10-28-183952 | Correct roles and successful readback; no recorded assistant goodbye before dispatch. |
| s10-recommended-correction-control | session-10-28-185048 | Corrected readback and both goodbye messages committed before dispatch; assistant audio follows dispatch. Large commitment delays are retained. |
| s11-nano-correction-closing | session-10-28-185644 | Corrected readback succeeds; missing-hour transcript fragment remains suspected, and dispatch lacks a recorded assistant goodbye. |

The four additions use `scripts/import_expanded_dataset.py`, which accepts only these
source folders, refuses existing targets, and checks actual detector observations before
writing anything. It keeps all recorded turns/order and relative timing, allowlisted
state/latency observations, exact closing-policy text, relevant role/goal instructions,
model identities and distinct normalized tool lifecycles. The original seven files are
unchanged. The legacy `import_manifest.json` describes only those original seven.

Identities are pseudonymized consistently (Alex Morgan, Taylor, Dr. Avery, Example Family
Practice); DOB values become the plain `REDACTED` token. Notes disclose these changes.
Unrelated profile instructions, usage payloads, audio references, raw identifiers,
arguments/results and provider deployment metadata are omitted. No privacy-minimized
raw-report copy is retained. `source.imported_at` is a generated normalization receipt,
not a copied source recording timestamp; all diagnostic times remain relative seconds.

These sessions share scheduling scripts and are not independent trials. The role-confounded
case is not evidence that Nano is generally worse. The successful Recommended closing run
is a negative control for premature dispatch, not a clean latency control. Tool dispatch
and terminal `done` do not establish immediate audible disconnection. Fingerprint counts
are findings, not calls or failure rates.
