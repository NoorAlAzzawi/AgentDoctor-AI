import type {
  Comparison,
  ComparedMetric,
  FindingsResponse,
  FingerprintsResponse,
  Session,
  SessionStats,
  SessionSummary,
} from "./types";
export const mockMode = import.meta.env.VITE_USE_MOCKS === "true";
const base = (import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "Session not found."
        : `Request failed (${response.status}). Check that the API is running.`,
    );
  return response.json() as Promise<T>;
}
export const api = {
  fingerprints: () =>
    get<FingerprintsResponse>(
      mockMode ? "/mock/fingerprints.json" : `${base}/api/fingerprints`,
    ),
  sessions: () =>
    get<SessionSummary[]>(
      mockMode ? "/mock/sessions.json" : `${base}/api/sessions`,
    ),
  session: (id: string) =>
    get<Session>(
      mockMode
        ? `/mock/${encodeURIComponent(id)}.session.json`
        : `${base}/api/sessions/${encodeURIComponent(id)}`,
    ),
  findings: (id: string) =>
    get<FindingsResponse>(
      mockMode
        ? `/mock/${encodeURIComponent(id)}.findings.json`
        : `${base}/api/sessions/${encodeURIComponent(id)}/findings`,
    ),
  compare: async (a: string, b: string): Promise<Comparison> =>
    mockMode
      ? compareMock(
          await api.session(a),
          await api.session(b),
          await api.findings(a),
          await api.findings(b),
        )
      : get<Comparison>(`${base}/api/compare?${new URLSearchParams({ a, b })}`),
};
type MetricSpec = [
  string,
  string,
  ComparedMetric["unit"],
  ComparedMetric["better"],
  (s: SessionStats) => number | null,
];
const specs: MetricSpec[] = [
  ["duration_s", "Session duration", "s", null, (s) => s.duration_s],
  ["agent_turns", "Agent turns", "count", null, (s) => s.agent_turns],
  [
    "e2e_latency_median",
    "Response latency (median)",
    "s",
    "lower",
    (s) => s.e2e_latency.median_s,
  ],
  [
    "e2e_latency_p90",
    "Response latency (p90)",
    "s",
    "lower",
    (s) => s.e2e_latency.p90_s,
  ],
  [
    "llm_ttft_median",
    "LLM time to first token (median)",
    "s",
    "lower",
    (s) => s.llm_ttft.median_s,
  ],
  [
    "tts_ttfb_median",
    "TTS time to first audio (median)",
    "s",
    "lower",
    (s) => s.tts_ttfb.median_s,
  ],
  [
    "end_of_turn_median",
    "End-of-turn delay (median)",
    "s",
    "lower",
    (s) => s.end_of_turn_delay.median_s,
  ],
  [
    "endpointing_timeouts",
    "Turns ended by endpointing timeout",
    "count",
    "lower",
    (s) => s.endpointing_timeouts,
  ],
  [
    "false_interruptions",
    "False interruptions",
    "count",
    "lower",
    (s) => s.false_interruptions,
  ],
  [
    "interrupted_agent_turns",
    "Agent turns interrupted",
    "count",
    null,
    (s) => s.interrupted_agent_turns,
  ],
];
export function compareMock(
  a: SessionSummary,
  b: SessionSummary,
  fa: FindingsResponse,
  fb: FindingsResponse,
): Comparison {
  const same_models = {
    stt: a.models.stt === b.models.stt,
    llm: a.models.llm === b.models.llm,
    tts: a.models.tts === b.models.tts,
  };
  const findings_by_category: Comparison["findings_by_category"] = {};
  for (const [side, response] of [
    ["a", fa],
    ["b", fb],
  ] as const)
    for (const f of response.findings) {
      findings_by_category[f.category] ??= { a: 0, b: 0 };
      findings_by_category[f.category][side]++;
    }
  const caveats = [
    "One session per side: differences may be run-to-run variation, not the pipeline.",
  ];
  if (Object.values(same_models).filter((v) => !v).length > 1)
    caveats.push(
      "More than one pipeline component differs, so a difference can't be attributed to a single component.",
    );
  if (a.synthetic || b.synthetic)
    caveats.push("At least one session is synthetic test data.");
  const summary = (s: SessionSummary): SessionSummary => ({
    id: s.id,
    title: s.title,
    notes: s.notes,
    pipeline_label: s.pipeline_label,
    models: s.models,
    synthetic: s.synthetic,
    stats: s.stats,
  });
  return {
    a: summary(a),
    b: summary(b),
    same_models,
    findings_by_category,
    caveats,
    metrics: specs.map(([key, label, unit, better, read]) => ({
      key,
      label,
      unit,
      better,
      a: read(a.stats),
      b: read(b.stats),
    })),
  };
}
export function winner(m: ComparedMetric): "a" | "b" | null {
  if (!m.better || m.a === null || m.b === null || m.a === m.b) return null;
  return (m.better === "lower" ? m.a < m.b : m.a > m.b) ? "a" : "b";
}
export function time(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "n/a";
  const tenths = Math.round(value * 10);
  return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
}
export const seconds = (value: number | null) =>
  value === null ? "n/a" : `${value.toFixed(3)} s`;
export const human = (value: string) => value.replaceAll("_", " ");
