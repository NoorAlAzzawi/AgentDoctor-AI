"""Side-by-side comparison of two sessions, using only reported metrics."""

from .models import ComparedMetric, Comparison, Finding, Session, SessionSummary

_METRICS = [
    # key, label, unit, better, getter
    ("duration_s", "Session duration", "s", None, lambda s: s.duration_s),
    ("agent_turns", "Agent turns", "count", None, lambda s: s.agent_turns),
    ("e2e_latency_median", "Response latency (median)", "s", "lower", lambda s: s.e2e_latency.median_s),
    ("e2e_latency_p90", "Response latency (p90)", "s", "lower", lambda s: s.e2e_latency.p90_s),
    ("llm_ttft_median", "LLM time to first token (median)", "s", "lower", lambda s: s.llm_ttft.median_s),
    ("tts_ttfb_median", "TTS time to first audio (median)", "s", "lower", lambda s: s.tts_ttfb.median_s),
    ("end_of_turn_median", "End-of-turn delay (median)", "s", "lower", lambda s: s.end_of_turn_delay.median_s),
    ("endpointing_timeouts", "Turns ended by endpointing timeout", "count", "lower", lambda s: s.endpointing_timeouts),
    ("false_interruptions", "False interruptions", "count", "lower", lambda s: s.false_interruptions),
    ("interrupted_agent_turns", "Agent turns interrupted", "count", None, lambda s: s.interrupted_agent_turns),
]


def compare_sessions(
    a: Session, b: Session, findings_a: list[Finding] = (), findings_b: list[Finding] = ()
) -> Comparison:
    same_models = {k: getattr(a.models, k) == getattr(b.models, k) for k in ("stt", "llm", "tts")}
    by_category: dict[str, dict[str, int]] = {}
    for side, findings in (("a", findings_a), ("b", findings_b)):
        for f in findings:
            by_category.setdefault(f.category, {"a": 0, "b": 0})[side] += 1

    caveats = ["One session per side: differences may be run-to-run variation, not the pipeline."]
    if sum(not v for v in same_models.values()) > 1:
        caveats.append("More than one pipeline component differs, so a difference can't be "
                       "attributed to a single component.")
    if a.synthetic or b.synthetic:
        caveats.append("At least one session is synthetic test data.")

    return Comparison(
        a=SessionSummary.model_validate(a.model_dump()),
        b=SessionSummary.model_validate(b.model_dump()),
        same_models=same_models,
        metrics=[
            ComparedMetric(key=k, label=label, unit=unit, better=better, a=get(a.stats), b=get(b.stats))
            for k, label, unit, better, get in _METRICS
        ],
        findings_by_category=by_category,
        caveats=caveats,
    )
