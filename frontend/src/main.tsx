import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, human, mockMode, seconds, time, winner } from "./data";
import type {
  Evidence,
  Finding,
  FindingsResponse,
  Session,
  SessionSummary,
  TimelineEvent,
} from "./types";
import "./style.css";

function useLoad<T>(load: () => Promise<T>, key: string) {
  const [state, set] = useState<{ data?: T; error?: string }>({});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    set({});
    load()
      .then((data) => {
        if (active) set({ data });
      })
      .catch((e: unknown) => {
        if (active)
          set({
            error: e instanceof Error ? e.message : "Unable to load data.",
          });
      });
    return () => {
      active = false;
    };
  }, [key, retry]);
  return { ...state, retry: () => setRetry((n) => n + 1) };
}
function State({ error, retry }: { error?: string; retry: () => void }) {
  return (
    <div className="state" role={error ? "alert" : "status"}>
      {error ? (
        <>
          <h2>Unable to load this view</h2>
          <p>{error}</p>
          <button onClick={retry}>Try again</button>
        </>
      ) : (
        <>
          <span className="pulse" /> Loading session evidence…
        </>
      )}
    </div>
  );
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function Metrics({ session: s }: { session: SessionSummary }) {
  return (
    <div className="metrics">
      <div>
        <small>Duration</small>
        <strong>{time(s.stats.duration_s)}</strong>
      </div>
      <div>
        <small>Response · median</small>
        <strong>{seconds(s.stats.e2e_latency.median_s)}</strong>
      </div>
      <div>
        <small>False interruptions</small>
        <strong>{s.stats.false_interruptions}</strong>
      </div>
      <div>
        <small>Endpointing timeouts</small>
        <strong>{s.stats.endpointing_timeouts}</strong>
      </div>
    </div>
  );
}
function Models({ session }: { session: SessionSummary }) {
  return (
    <dl className="models">
      {Object.entries(session.models).map(([k, v]) => (
        <div key={k}>
          <dt>{human(k)}</dt>
          <dd>{v ?? "n/a"}</dd>
        </div>
      ))}
    </dl>
  );
}
export function Dashboard() {
  const state = useLoad(async () => {
    const sessions = await api.sessions();
    return Promise.all(
      sessions.map(async (s) => ({ s, f: await api.findings(s.id) })),
    );
  }, "sessions");
  const [selected, select] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  if (!state.data) return <State {...state} />;
  const all = state.data;
  const findings = all.flatMap((x) => x.f.findings);
  const shown = all.filter(({ s }) =>
    `${s.title} ${Object.values(s.models).join(" ")}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">OBSERVABILITY / SESSIONS</p>
          <h1>
            Every conversation.
            <br />
            <span>A clearer diagnosis.</span>
          </h1>
          <p>Trace voice-agent failures back to the evidence.</p>
        </div>
        <Badge tone="green">
          ● {mockMode ? "Real session fixtures" : "Live API"}
        </Badge>
      </div>
      <div className="summary">
        <div>
          <small>Sessions analyzed</small>
          <strong>{all.length.toString().padStart(2, "0")}</strong>
        </div>
        <div>
          <small>Confirmed findings</small>
          <strong>
            {findings.filter((f) => f.status === "confirmed").length}
          </strong>
          <span>Directly observed failures</span>
        </div>
        <div>
          <small>Suspected findings</small>
          <strong>
            {findings.filter((f) => f.status === "suspected").length}
          </strong>
          <span>Require further evidence</span>
        </div>
        <div>
          <small>Evidence source</small>
          <strong className="text-value">LiveKit</strong>
          <span>Sanitized session reports</span>
        </div>
      </div>
      <section>
        <div className="section-head">
          <div>
            <h2>
              Session explorer <span className="muted">/ {all.length}</span>
            </h2>
            <p>Select two sessions to inspect their differences.</p>
          </div>
          <div className="tools">
            <input
              aria-label="Search sessions"
              placeholder="Search sessions or models…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {selected.length === 2 ? (
              <Link
                className="button primary"
                to={`/compare?${new URLSearchParams({ a: selected[0], b: selected[1] })}`}
              >
                Compare sessions →
              </Link>
            ) : (
              <button disabled>Compare ({selected.length}/2)</button>
            )}
          </div>
        </div>
        <div className="session-grid">
          {shown.map(({ s, f }) => (
            <article className="session-card" key={s.id}>
              <div className="card-top">
                <Badge>{s.pipeline_label ?? "n/a"}</Badge>
                <label className="select">
                  <input
                    type="checkbox"
                    aria-label={`Compare ${s.title}`}
                    checked={selected.includes(s.id)}
                    disabled={selected.length === 2 && !selected.includes(s.id)}
                    onChange={(e) =>
                      select(
                        e.target.checked
                          ? [...selected, s.id]
                          : selected.filter((id) => id !== s.id),
                      )
                    }
                  />
                  Compare
                </label>
              </div>
              <p className="mono muted">{s.id}</p>
              <h3>
                <Link to={`/sessions/${s.id}`}>
                  {s.title} <span>↗</span>
                </Link>
              </h3>
              <Models session={s} />
              <Metrics session={s} />
              <div className="card-bottom">
                <Badge tone="red">
                  {f.findings.filter((x) => x.status === "confirmed").length}{" "}
                  confirmed
                </Badge>
                <Badge tone="amber">
                  {f.findings.filter((x) => x.status === "suspected").length}{" "}
                  suspected
                </Badge>
                <span>{s.stats.user_turns + s.stats.agent_turns} turns</span>
              </div>
            </article>
          ))}
        </div>
        {!shown.length && (
          <p className="state">No sessions match your search.</p>
        )}
      </section>
    </>
  );
}
export function EvidenceLink({
  e,
  onNavigate,
}: {
  e: Evidence;
  onNavigate: (id: string) => void;
}) {
  const id = e.turn_id ?? e.event_id;
  return id ? (
    <button className="evidence" onClick={() => onNavigate(id)}>
      <span className="mono">{time(e.t_s)} ↗</span>
      <strong>{e.label}</strong>
      <span>{e.value}</span>
    </button>
  ) : (
    <div className="evidence">
      <strong>{e.label}</strong>
      <span>{e.value}</span>
    </div>
  );
}
function FindingCard({
  f,
  onNavigate,
}: {
  f: Finding;
  onNavigate: (id: string) => void;
}) {
  return (
    <article className="finding">
      <div className="tags">
        <Badge tone={f.status === "confirmed" ? "red" : "amber"}>
          {f.status}
        </Badge>
        <Badge>{human(f.category)}</Badge>
        <span className="mono muted">
          {f.time_range ? time(f.time_range.start_s) : "n/a"}
        </span>
      </div>
      <h3>{f.title}</h3>
      <p>{f.summary}</p>
      <h4>Observed evidence</h4>
      {f.evidence.map((e, i) => (
        <EvidenceLink key={i} e={e} onNavigate={onNavigate} />
      ))}
      <div className="hypothesis">
        <h4>Root cause · {f.root_cause.confidence}</h4>
        <p>{f.root_cause.statement}</p>
        {f.root_cause.missing_evidence.length > 0 && (
          <>
            <h4>Missing evidence</h4>
            <ul>
              {f.root_cause.missing_evidence.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </>
        )}
      </div>
      <h4>Suggested debugging steps</h4>
      <ol>
        {f.next_steps.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ol>
    </article>
  );
}
export function Timeline({
  events,
  active,
}: {
  events: TimelineEvent[];
  active: string | null;
}) {
  return (
    <div className="event-list">
      {events.length === 0 && <p>No recorded events.</p>}
      {events.map((e) => (
        <div
          tabIndex={-1}
          id={`evidence-${e.id}`}
          key={e.id}
          className={`event ${active === e.id ? "highlight" : ""}`}
        >
          <time>{time(e.t_s)}</time>
          <span
            className={`dot ${e.type === "false_interruption" ? "alert-dot" : ""}`}
          />
          <div>
            <strong>{human(e.type)}</strong>
            <p>
              {Object.entries(e.data)
                .map(
                  ([k, v]) => `${human(k)}: ${v === null ? "n/a" : String(v)}`,
                )
                .join(" · ")}
            </p>
          </div>
          <small>{e.id}</small>
        </div>
      ))}
    </div>
  );
}
export function Investigation() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const sessionId = id ?? params.get("id") ?? "";
  const state = useLoad(async () => {
    if (!sessionId) throw new Error("Open a session from the dashboard.");
    const [s, f] = await Promise.all([
      api.session(sessionId),
      api.findings(sessionId),
    ]);
    return { s, f };
  }, sessionId);
  const [active, setActive] = useState<string | null>(null);
  const [jump, setJump] = useState(0);
  const [tab, setTab] = useState<"transcript" | "timeline">("transcript");
  const [filter, setFilter] = useState("all");
  function navigate(target: string) {
    setTab(target.startsWith("t") ? "transcript" : "timeline");
    setActive(target);
    setJump((n) => n + 1);
  }
  useEffect(() => {
    if (active) {
      const element = document.getElementById(`evidence-${active}`);
      element?.scrollIntoView?.({ behavior: "smooth", block: "center" });
      element?.focus({ preventScroll: true });
    }
  }, [active, tab, jump]);
  if (!state.data) return <State {...state} />;
  const { s, f } = state.data;
  return (
    <>
      <Link className="back" to="/">
        ← All sessions
      </Link>
      <div className="page-title compact">
        <div>
          <p className="eyebrow">SESSION INVESTIGATION / {s.id}</p>
          <h1>{s.title}</h1>
          <p>{s.notes ?? "No session notes available."}</p>
        </div>
        <Badge>{s.pipeline_label ?? "n/a"}</Badge>
      </div>
      <Metrics session={s} />
      <details className="panel">
        <summary>Pipeline & configuration</summary>
        <Models session={s} />
        <dl className="models">
          {Object.entries(s.config).map(([k, v]) => (
            <div key={k}>
              <dt>{human(k)}</dt>
              <dd>{v === null ? "n/a" : String(v)}</dd>
            </div>
          ))}
        </dl>
        <p>
          SDK {s.source.sdk_version ?? "n/a"} ·{" "}
          {s.source.synthetic ? "Synthetic" : "Sanitized real session"}
        </p>
        <h4>Agent instructions</h4>
        <pre>{s.agent_instructions ?? "n/a"}</pre>
      </details>
      <Latency session={s} />
      <div className="investigation">
        <section className="panel conversation">
          <div className="section-head">
            <h2>Conversation</h2>
            <span className="muted">{s.turns.length} turns</span>
          </div>
          <div
            className="tabs"
            role="tablist"
            aria-label="Conversation view"
            onKeyDown={(e) => {
              if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
                e.preventDefault();
                const next =
                  e.key === "Home"
                    ? "transcript"
                    : e.key === "End"
                      ? "timeline"
                      : tab === "transcript"
                        ? "timeline"
                        : "transcript";
                setTab(next);
                document.getElementById(`${next}-tab`)?.focus();
              }
            }}
          >
            {(["transcript", "timeline"] as const).map((t) => (
              <button
                key={t}
                id={`${t}-tab`}
                role="tab"
                tabIndex={tab === t ? 0 : -1}
                aria-selected={tab === t}
                aria-controls="conversation-panel"
                onClick={() => setTab(t)}
              >
                {human(t)} {t === "timeline" ? `(${s.events.length})` : ""}
              </button>
            ))}
          </div>
          <p className="note">
            Times are relative to recording start. User starts may reflect an
            earlier voice-activity blip. No audio is available.
          </p>
          <div
            id="conversation-panel"
            role="tabpanel"
            aria-labelledby={`${tab}-tab`}
            className="scroll-pane"
          >
            {tab === "timeline" ? (
              <Timeline events={s.events} active={active} />
            ) : s.turns.length ? (
              s.turns.map((t) => (
                <article
                  tabIndex={-1}
                  id={`evidence-${t.id}`}
                  key={t.id}
                  className={`turn ${t.speaker} ${active === t.id ? "highlight" : ""}`}
                >
                  <div>
                    <strong>{s.speaker_labels[t.speaker]}</strong>
                    <span className="mono">
                      {time(t.start_s)} · {t.id}
                    </span>
                  </div>
                  <p>{t.text}</p>
                  <small>
                    End {time(t.end_s)} · Committed {time(t.committed_s)}
                    {t.interrupted ? " · Interrupted" : ""}
                  </small>
                  <details>
                    <summary>Turn measurements</summary>
                    <dl>
                      {Object.entries(t.metrics).map(([k, v]) => (
                        <div key={k}>
                          <dt>{human(k)}</dt>
                          <dd>{seconds(v)}</dd>
                        </div>
                      ))}
                    </dl>
                    <p>
                      Transcript confidence: {t.transcript_confidence ?? "n/a"}
                    </p>
                  </details>
                </article>
              ))
            ) : (
              <p>No transcript available.</p>
            )}
          </div>
        </section>
        <section className="findings">
          <div className="section-head">
            <h2>
              Findings <span className="muted">/ {f.findings.length}</span>
            </h2>
            <select
              aria-label="Filter findings"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="confirmed">Confirmed</option>
              <option value="suspected">Suspected</option>
            </select>
          </div>
          <p className="note">
            Failure status describes what happened. Root-cause confidence
            describes why.
          </p>
          {(["high", "medium", "low"] as const).map((severity) => {
            const items = f.findings.filter(
              (x) =>
                x.severity === severity &&
                (filter === "all" || x.status === filter),
            );
            return items.length ? (
              <div key={severity}>
                <h3 className={`severity ${severity}`}>
                  {severity} severity · {items.length}
                </h3>
                {items.map((item) => (
                  <FindingCard key={item.id} f={item} onNavigate={navigate} />
                ))}
              </div>
            ) : null;
          })}
          {!f.findings.some((x) => filter === "all" || x.status === filter) && (
            <p className="state">No findings for this selection.</p>
          )}
          <small className="muted">
            {f.detectors_run.length} detector functions ran:{" "}
            {f.detectors_run.map(human).join(", ")}.
          </small>
        </section>
      </div>
    </>
  );
}
function Latency({ session: s }: { session: Session }) {
  const rows = [
    ["Response latency", s.stats.e2e_latency],
    ["LLM first token", s.stats.llm_ttft],
    ["TTS first audio", s.stats.tts_ttfb],
    ["End-of-turn delay", s.stats.end_of_turn_delay],
  ] as const;
  const max = Math.max(
    ...rows.flatMap(([, r]) => [r.median_s ?? 0, r.p90_s ?? 0]),
    0.001,
  );
  return (
    <section className="panel latency">
      <div className="section-head">
        <h2>Latency breakdown</h2>
        <span className="muted">Median / p90 · seconds</span>
      </div>
      {rows.map(([name, r]) => (
        <div className="latency-row" key={name}>
          <span>
            {name}
            <small>n = {r.n}</small>
          </span>
          <div className="bars" aria-hidden="true">
            <i style={{ width: `${((r.p90_s ?? 0) / max) * 100}%` }} />
            <b style={{ width: `${((r.median_s ?? 0) / max) * 100}%` }} />
          </div>
          <span className="mono">
            {seconds(r.median_s)} / {seconds(r.p90_s)}
          </span>
        </div>
      ))}
    </section>
  );
}
export function Compare() {
  const [params] = useSearchParams();
  const a = params.get("a") ?? "",
    b = params.get("b") ?? "";
  const state = useLoad(() => {
    if (!a || !b || a === b)
      return Promise.reject(
        new Error("Select two different sessions from the dashboard."),
      );
    return api.compare(a, b);
  }, `${a}:${b}`);
  if (!state.data) return <State {...state} />;
  const c = state.data;
  return (
    <>
      <Link className="back" to="/">
        ← All sessions
      </Link>
      <div className="page-title compact">
        <div>
          <p className="eyebrow">SIDE-BY-SIDE ANALYSIS</p>
          <h1>Compare the evidence.</h1>
          <p>Understand what changed between two conversations.</p>
        </div>
      </div>
      <aside className="caveats">
        <strong>Different sessions do not prove model superiority.</strong>
        <ul>
          {c.caveats.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </aside>
      <div className="compare-head">
        {[c.a, c.b].map((s, i) => (
          <article className="panel" key={s.id}>
            <Badge>
              Session {i === 0 ? "A" : "B"} · {s.pipeline_label ?? "n/a"}
            </Badge>
            <h2>
              <Link to={`/sessions/${s.id}`}>{s.title} ↗</Link>
            </h2>
            <Models session={s} />
          </article>
        ))}
      </div>
      <div className="panel">
        <h2>Pipeline differences</h2>
        <p>
          {Object.entries(c.same_models)
            .map(
              ([key, same]) =>
                `${key.toUpperCase()}: ${same ? "same model" : "different models"}`,
            )
            .join(" · ")}
        </p>
        <div className="table-wrap">
          <table>
            <caption>
              Reported metrics · highlighted values follow the API’s better
              direction
            </caption>
            <thead>
              <tr>
                <th scope="col">Measurement</th>
                <th scope="col">Session A</th>
                <th scope="col">Session B</th>
              </tr>
            </thead>
            <tbody>
              {c.metrics.map((m) => (
                <tr key={m.key}>
                  <th scope="row">{m.label}</th>
                  {(["a", "b"] as const).map((side) => (
                    <td
                      key={side}
                      className={winner(m) === side ? "better" : ""}
                    >
                      {m.unit === "s" ? seconds(m[side]) : (m[side] ?? "n/a")}
                      {winner(m) === side && (
                        <small> {m.better} observed</small>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h2>Findings by category</h2>
        <p className="note">
          Includes confirmed and suspected findings. Counts alone do not
          establish quality.
        </p>
        {Object.keys(c.findings_by_category).length ? (
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Session A</th>
                <th>Session B</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(c.findings_by_category).map(([k, v]) => (
                <tr key={k}>
                  <th scope="row">{human(k)}</th>
                  <td>{v.a}</td>
                  <td>{v.b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No findings in either session.</p>
        )}
      </div>
    </>
  );
}
function App() {
  const [dark, setDark] = useState(
    () => localStorage.getItem("agentdoctor-theme") !== "light",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("agentdoctor-theme", dark ? "dark" : "light");
  }, [dark]);
  return (
    <BrowserRouter>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header>
        <Link className="brand" to="/">
          <span className="brand-icon">✳</span> AgentDoctor <span>AI</span>
        </Link>
        <nav aria-label="Main navigation">
          <NavLink to="/">Sessions</NavLink>
        </nav>
        <div className="header-right">
          <span className="environment">LOCAL WORKSPACE</span>
          <button
            aria-label={`Use ${dark ? "light" : "dark"} mode`}
            onClick={() => setDark(!dark)}
          >
            {dark ? "☀" : "☾"}
          </button>
        </div>
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sessions/:id" element={<Investigation />} />
          <Route path="/sessions" element={<Investigation />} />
          <Route path="/compare" element={<Compare />} />
          <Route
            path="*"
            element={
              <div className="state">
                <h1>Page not found</h1>
                <Link to="/">Return to sessions</Link>
              </div>
            }
          />
        </Routes>
      </main>
      <footer>
        <span>AgentDoctor AI</span>
        <span>Evidence first. Hypotheses clearly labeled.</span>
        <span>{mockMode ? "LOCAL FIXTURES" : "READ-ONLY API"}</span>
      </footer>
    </BrowserRouter>
  );
}
if (document.getElementById("root"))
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
