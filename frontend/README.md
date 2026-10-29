# AgentDoctor AI frontend

Read-only React + TypeScript dashboard for the FastAPI contract. Requires Node.js 22.12+ (or 24+).

```sh
cd frontend
npm ci
npm run dev
```

Live mode defaults to `http://127.0.0.1:8000`. Start the backend separately using its existing instructions. Open `http://localhost:5173` (the backend permits this origin).

```sh
# Run entirely locally with sanitized real detector output
VITE_USE_MOCKS=true npm run dev
# Use another API
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

Environment variables are read by Vite at startup/build. Restart after changing them. No API keys are needed. There is no automatic mock fallback on API failure.

Routes: `/`, `/sessions/:id` (also `/sessions?id=...`), `/fingerprints`, `/compare?a=...&b=...`. Select two dashboard checkboxes to compare. Evidence links switch the conversation pane, focus and highlight the referenced turn or event. A theme toggle saves your preference locally.

## Validation

```sh
npm run typecheck
npm test
npm run build
```

Mock files under `public/mock/` are verbatim copies of `contracts/examples/sessions.json` and `contracts/examples/by-session/`. They contain all eleven sessions and 42 detector findings (27 confirmed, 15 suspected). The illustrative fixture is not used. To refresh, copy those source files into `frontend/public/mock/` without editing the originals. `src/types.ts` is copied unchanged from the contract.

Mock comparison follows the backend's comparison implementation; a test checks equality with its s03/s04 example. Tests also validate all evidence targets, counts, null/tie ranking, timestamp rollover, event rendering and evidence link destinations. These are component/data tests; they do not exercise a running API or substitute for browser/device QA.

Missing metrics display `n/a`. Confidence values retain their contract semantics. Latency bars show actual aggregate median and p90 values with sample counts; they are not additive. Comparisons display the API caveats and never declare overall model superiority. No audio playback is offered because the sanitized reports contain no audio.

`npm run build` produces `dist/`. For local preview use `npx vite preview`. Production hosting would need a fallback to `index.html` for client routes; nothing is deployed by this project.

## Full application and live integration checks

From the repository root, start the API in one terminal:

```sh
cd backend
uv sync
uv run agentdoctor serve
```

Start Vite in another terminal (explicitly disable mocks):

```sh
cd frontend
npm ci
VITE_USE_MOCKS=false VITE_API_BASE=http://127.0.0.1:8000 npm run dev -- --host localhost --strictPort
```

With both servers running, run the opt-in suite in a third terminal:

```sh
cd frontend
npm run test:live
```

The 18 live tests call the actual API without stubbing fetch or loading fixtures. They render the dashboard, all eleven investigations and a comparison; click every linked evidence item and check focus/highlighting; compare all 55 distinct session pairs; check actual API metric directions, both permitted CORS origins and preflights, Vite route fallback, live-mode configuration and a real API 404. Normal `npm test` skips these tests so it needs no running servers. These jsdom/HTTP tests do not verify browser layout or browser-enforced CORS; the live suite checks the server's CORS headers explicitly.

For manual browser inspection, open `http://localhost:5173`:

1. **Sessions:** confirm the “Live API” badge, eleven sessions, 27 confirmed and 15 suspected findings. Open DevTools → Network, reload, and confirm API requests go to `127.0.0.1:8000` and succeed without CORS errors. There should be no `/mock/` requests.
2. **Investigation:** open `http://localhost:5173/sessions/s03-workshop-ab-script`. It has two confirmed findings. Click “Agent reply affected” to focus/highlight a transcript turn; click “User voice activity started” to switch to the timeline and focus/highlight its event. Check the separate failure status, root-cause confidence, missing evidence and debugging steps.
3. **Compare:** select s03 and s04 from the dashboard, or open `http://localhost:5173/compare?a=s03-workshop-ab-script&b=s04-recommended-ab-script`. Confirm the pipeline/model differences, metrics, finding categories, comparison caveats and neutral ties. Model superiority must not be claimed.
4. Check all four views at narrow/mobile widths and in both themes. Tab through controls and evidence links; check for clipping and visible keyboard focus. Check DevTools → Console for runtime or CORS errors.

The integration run passed all backend tests (172), frontend tests (31), live tests (18), typechecking and production build. Manual browser/UI review of all four views has been completed.

## Final frontend polish

Findings start collapsed. Each card retains its original summary and shows status, category, severity, timestamp, evidence count and root-cause confidence. **View investigation** reveals all original diagnostic details and evidence links. Independent native buttons expose `aria-expanded`/`aria-controls`; hidden details stay out of keyboard navigation until opened. Detection coverage uses the same disclosure pattern and displays the exact detector identifiers.

The compact Findings controls are sticky only above 1000px wide and at least 700px tall. They remain in normal flow on narrow/short viewports to avoid consuming reading space. No new nested scrolling was added. Existing desktop layouts, typography, colors, conversation navigation and comparison caveats remain intact. Both comparison tables have named, keyboard-focusable overflow regions. Long source strings wrap within their columns.

Source review covered the existing desktop/laptop/tablet/mobile breakpoints. Text palette checks passed 4.5:1 against the three main surfaces in both themes. The latest checks passed typecheck, 31 frontend tests, production build and 18 live API tests. No frontend robot/widget/overlay was found, so no unrelated tooling was removed. No data, metrics, findings, API logic or dependencies changed.

Manual browser/UI review is complete. For future responsive checks, inspect **1440px**, **1280px**, **768px**, **390px** and **320px** widths:

- Sessions: keep the existing wide card grid; check narrow cards, search/compare controls and absence of page-level horizontal scrolling.
- Investigation: check two columns above 1000px and stacked panes below; open/close a finding, follow transcript/event evidence, and confirm the desktop sticky controls never cover the focused content. Test a session with many findings, such as s01 or s02.
- Compare: check all values/caveats and use the deliberate table overflow on mobile; the page itself should not scroll horizontally.
- In both themes, Tab to the disclosure buttons, activate them with Enter and Space, verify visible focus and expanded/collapsed state, and expand detector coverage. Check status/severity text and unchanged diagnostic content.

The Nano preset is identified from its recorded STT/LLM/TTS tuple. Session s08 is explicitly role-confounded; s10 is a legitimate closing control with preserved timing anomalies. Fingerprints shows 8 patterns, 39/42 grouped findings and 3 ungrouped findings. Test-only fixtures are excluded, including the regression excerpt of the same recording used for s08.
