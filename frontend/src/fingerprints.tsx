import { Link } from "react-router-dom";
import { human, time } from "./data";
import type {
  Evidence,
  FingerprintOccurrence,
  FingerprintsResponse,
} from "./types";

export function findingPath(
  sessionId: string,
  findingId: string,
  evidence?: Evidence,
) {
  const params = new URLSearchParams({ finding: findingId });
  const target = evidence?.turn_id ?? evidence?.event_id;
  if (target) params.set("evidence", target);
  return `/sessions/${encodeURIComponent(sessionId)}?${params}`;
}
function Occurrence({ occurrence: o }: { occurrence: FingerprintOccurrence }) {
  return (
    <li className="fingerprint-occurrence">
      <Link
        className="occurrence-link"
        to={findingPath(o.session_id, o.finding_id)}
      >
        {o.session_title} ↗
      </Link>
      <p>{o.title}</p>
      <div className="tags">
        <span className={`badge ${o.status === "confirmed" ? "red" : "amber"}`}>
          {o.status}
        </span>
        <span className="badge">{o.severity} severity</span>
        <span className="mono muted">
          {o.time_range ? time(o.time_range.start_s) : "n/a"}
        </span>
      </div>
      <details className="occurrence-context">
        <summary>Observed context and evidence</summary>
        <p className="note">
          {o.session_notes ?? "No session notes available."}
        </p>
        <dl className="models">
          <div>
            <dt>Pipeline</dt>
            <dd>{o.pipeline_label ?? "n/a"}</dd>
          </div>
          {Object.entries(o.models).map(([k, v]) => (
            <div key={k}>
              <dt>{human(k)}</dt>
              <dd>{v ?? "n/a"}</dd>
            </div>
          ))}
          {Object.entries(o.config).map(([k, v]) => (
            <div key={k}>
              <dt>{human(k)}</dt>
              <dd>{v === null ? "n/a" : String(v)}</dd>
            </div>
          ))}
        </dl>
        <ul className="fingerprint-evidence">
          {o.evidence.map((e, i) => (
            <li key={i}>
              <EvidenceReference o={o} e={e} />
            </li>
          ))}
        </ul>
      </details>
    </li>
  );
}
function EvidenceReference({
  o,
  e,
}: {
  o: FingerprintOccurrence;
  e: Evidence;
}) {
  const text = (
    <>
      <span className="mono muted">
        {time(e.t_s)} · {e.turn_id ?? e.event_id ?? e.kind}
      </span>
      <strong>{e.label}</strong>
      <span>{e.value}</span>
    </>
  );
  return e.turn_id || e.event_id ? (
    <Link className="evidence" to={findingPath(o.session_id, o.finding_id, e)}>
      {text}
    </Link>
  ) : (
    <div className="evidence">{text}</div>
  );
}
export function FingerprintsView({
  response: r,
}: {
  response: FingerprintsResponse;
}) {
  return (
    <>
      <div className="page-title compact">
        <div>
          <p className="eyebrow">CROSS-SESSION OBSERVATIONS</p>
          <h1>Failure Fingerprints</h1>
          <p>Recurring observed patterns, traced to their original findings.</p>
        </div>
        <span className="badge">Exact rules · v{r.rule_version}</span>
      </div>
      <aside className="caveats" aria-label="Fingerprint limitations">
        <strong>Recurring behavior does not prove a shared root cause.</strong>
        <ul>
          {r.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </aside>
      <div className="summary fingerprint-summary">
        <div>
          <small>Recorded sessions</small>
          <strong>{r.recorded_session_count}</strong>
        </div>
        <div>
          <small>Recurring patterns</small>
          <strong>{r.fingerprints.length}</strong>
        </div>
        <div>
          <small>Grouped findings</small>
          <strong>
            {r.grouped_finding_count} / {r.finding_count}
          </strong>
        </div>
        <div>
          <small>Ungrouped findings</small>
          <strong>{r.ungrouped.length}</strong>
        </div>
      </div>
      {r.excluded_synthetic_session_count > 0 && (
        <p className="note">
          Excluded {r.excluded_synthetic_session_count} synthetic sessions.
          Counts above use recorded sessions only.
        </p>
      )}
      {r.fingerprints.length === 0 && (
        <div className="state">
          No recurring patterns meet the evidence and cross-session
          requirements.
        </div>
      )}
      <div className="fingerprint-grid">
        {r.fingerprints.map((fp) => {
          const representative = fp.occurrences[0];
          return (
            <article key={fp.id} className="panel fingerprint-card" id={fp.id}>
              <div className="tags">
                <span className="badge">{human(fp.category)}</span>
                <span className="mono muted">{fp.id}</span>
              </div>
              <h2>{fp.name}</h2>
              <p className="fingerprint-count">
                {fp.occurrence_count} findings · {fp.affected_session_count}{" "}
                sessions
              </p>
              <div className="tags">
                <span className="badge red">
                  {fp.status_counts.confirmed} confirmed
                </span>
                <span className="badge amber">
                  {fp.status_counts.suspected} suspected
                </span>
              </div>
              <p className="note">
                Severity: {fp.severity_counts.high} high ·{" "}
                {fp.severity_counts.medium} medium · {fp.severity_counts.low}{" "}
                low
              </p>
              <p className="note">
                Affected pipelines:{" "}
                {fp.pipelines.map((p) => p ?? "n/a").join(", ")} — observed
                associations, not causes.
              </p>
              <h3>Grouping observations</h3>
              <ul>
                {fp.characteristics.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
              {representative && (
                <>
                  <h3>Representative evidence</h3>
                  <p className="note">
                    {representative.session_title} · first occurrence by
                    session/finding ID, not a typicality estimate.
                  </p>
                  {representative.evidence.slice(0, 2).map((e, i) => (
                    <EvidenceReference key={i} o={representative} e={e} />
                  ))}
                </>
              )}
              <details className="fingerprint-details">
                <summary>
                  All {fp.occurrence_count} original findings and contexts
                </summary>
                <ul className="occurrence-list">
                  {fp.occurrences.map((o) => (
                    <Occurrence
                      key={`${o.session_id}:${o.finding_id}`}
                      occurrence={o}
                    />
                  ))}
                </ul>
              </details>
              <p className="note fingerprint-caveat">{fp.caveat}</p>
            </article>
          );
        })}
      </div>
      <section className="panel">
        <h2>Ungrouped findings</h2>
        <p className="note">
          Ungrouped does not mean unimportant. Each finding remains available in
          its session.
        </p>
        {r.ungrouped.length ? (
          <ul className="ungrouped-list">
            {r.ungrouped.map((f) => (
              <li key={`${f.session_id}:${f.finding_id}`}>
                <Link to={findingPath(f.session_id, f.finding_id)}>
                  {f.session_title} · {human(f.detector)} ↗
                </Link>
                <p className="note">
                  {f.status} · {f.severity} severity · {f.explanation}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p>No ungrouped findings.</p>
        )}
      </section>
    </>
  );
}
