"""Convert LiveKit Agents session reports into AgentDoctor's normalized, sanitized format.

Sanitizing is allowlist-based: only the fields copied below ever leave the raw report.
Audio, file paths, job/room ids, provider request ids and usage-update noise are dropped.
"""

import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    LatencyStats,
    Models,
    Session,
    SessionConfig,
    SessionStats,
    SourceInfo,
    TimelineEvent,
    Turn,
    TurnMetrics,
)

# Known pipeline presets, used only to label sessions in the UI.
PIPELINE_PRESETS = {
    "workshop": ("assemblyai/universal-3-6-pro", "google/gemma-4-31b-it", "fishaudio/s2.1-pro"),
    "recommended": ("deepgram/nova-3", "openai/gpt-4.1-mini", "cartesia/sonic-3"),
    "hybrid": ("assemblyai/universal-3-6-pro", "google/gemma-4-31b-it", "cartesia/sonic-3"),
}

DEFAULT_SPEAKER_LABELS = {"user": "Receptionist (human tester)", "agent": "Patient agent"}

_METRIC_KEYS = {
    "e2e_latency": "e2e_latency_s",
    "end_of_turn_delay": "end_of_turn_delay_s",
    "transcription_delay": "transcription_delay_s",
    "llm_node_ttft": "llm_ttft_s",
    "tts_node_ttfb": "tts_ttfb_s",
    "playback_latency": "playback_latency_s",
}

# Patterns that must not appear in imported text. Fictional 555 numbers are allowed.
_PRIVACY_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "url": r"https?://|wss?://|www\.",
    "phone": r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
}


class PrivacyError(ValueError):
    pass


def privacy_scan(texts: list[str], blocked_terms: list[str] = ()) -> list[str]:
    """Return a list of problems found (empty if clean)."""
    problems = []
    for text in texts:
        for name, pattern in _PRIVACY_PATTERNS.items():
            for match in re.finditer(pattern, text):
                if name == "phone" and "555" in match.group(0):
                    continue
                problems.append(f"{name}: {match.group(0)!r}")
        for term in blocked_terms:
            if term and term.lower() in text.lower():
                problems.append(f"blocked term: {term!r}")
    return sorted(set(problems))


def load_livekit_report(
    path: Path,
    *,
    session_id: str,
    title: str,
    notes: str | None = None,
    blocked_terms: list[str] = (),
    synthetic: bool = False,
) -> Session:
    raw = json.loads(Path(path).read_text())
    items = raw.get("chat_history", {}).get("items", [])
    events = raw.get("events", [])
    t0 = raw.get("audio_recording_started_at") or min(
        (e["created_at"] for e in events if "created_at" in e), default=None
    )

    def rel(t: float | None) -> float | None:
        return round(t - t0, 3) if t is not None and t0 is not None else None

    turns = _turns(items, rel)
    timeline = _events(events, rel)
    instructions = next(
        (i.get("instructions") for i in items if i.get("type") == "agent_config_update"), None
    )

    problems = privacy_scan([t.text for t in turns] + [instructions or ""], blocked_terms)
    if problems:
        raise PrivacyError(f"{path}: refusing to import, found {problems}")

    options = raw.get("options", {})
    config = SessionConfig(
        allow_interruptions=options.get("allow_interruptions"),
        min_interruption_duration_s=options.get("min_interruption_duration"),
        min_endpointing_delay_s=options.get("min_endpointing_delay"),
        max_endpointing_delay_s=options.get("max_endpointing_delay"),
        preemptive_generation=(options.get("preemptive_generation") or {}).get("enabled"),
    )
    usage = _usage(raw.get("usage") or [])
    models = _models(usage)
    close = next((e for e in reversed(timeline) if e.type == "session_close"), None)

    return Session(
        id=session_id,
        title=title,
        notes=notes,
        pipeline_label=_pipeline_label(models),
        models=models,
        synthetic=synthetic,
        stats=_stats(turns, timeline, config, duration_s=close.t_s if close else None),
        source=SourceInfo(
            sdk_version=raw.get("sdk_version"),
            original_name=Path(path).parent.name,
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            synthetic=synthetic,
        ),
        config=config,
        speaker_labels=DEFAULT_SPEAKER_LABELS,
        agent_instructions=instructions,
        turns=turns,
        events=timeline,
        usage=usage,
    )


def _turns(items: list[dict], rel) -> list[Turn]:
    turns = []
    for item in items:
        if item.get("type") != "message" or item.get("role") not in ("user", "assistant"):
            continue
        m = item.get("metrics") or {}
        turns.append(
            Turn(
                id=f"t{len(turns) + 1:03d}",
                index=len(turns),
                speaker="user" if item["role"] == "user" else "agent",
                text=" ".join(c for c in item.get("content", []) if isinstance(c, str)).strip(),
                start_s=rel(m.get("started_speaking_at")),
                end_s=rel(m.get("stopped_speaking_at")),
                committed_s=rel(item.get("created_at")),
                interrupted=bool(item.get("interrupted")),
                transcript_confidence=item.get("transcript_confidence"),
                metrics=TurnMetrics(
                    **{ours: round(m[theirs], 3) for theirs, ours in _METRIC_KEYS.items() if theirs in m}
                ),
            )
        )
    return turns


def _events(events: list[dict], rel) -> list[TimelineEvent]:
    kept = []
    for e in events:
        kind, data = e.get("type"), None
        if kind == "agent_state_changed":
            kind, data = "agent_state", {"from": e.get("old_state"), "to": e.get("new_state")}
        elif kind == "user_state_changed":
            kind, data = "user_state", {"from": e.get("old_state"), "to": e.get("new_state")}
        elif kind == "agent_false_interruption":
            kind, data = "false_interruption", {"resumed": e.get("resumed")}
        elif kind == "overlapping_speech":
            data = {
                "is_interruption": e.get("is_interruption"),
                "probability": e.get("probability"),
                "detection_delay_s": e.get("detection_delay"),
            }
        elif kind == "close":
            kind, data = "session_close", {"reason": e.get("reason"), "error": e.get("error")}
        if data is not None and e.get("created_at") is not None:
            kept.append((rel(e["created_at"]), kind, data))
    kept.sort(key=lambda x: x[0])  # raw events are not strictly time-ordered
    return [TimelineEvent(id=f"e{i + 1:03d}", t_s=t, type=k, data=d) for i, (t, k, d) in enumerate(kept)]


def _usage(usage: list[dict]) -> list[dict]:
    allowed = {"model", "input_tokens", "input_cached_tokens", "output_tokens",
               "characters_count", "audio_duration", "total_requests"}
    return [{k: v for k, v in u.items() if k in allowed} for u in usage]


def _models(usage: list[dict]) -> Models:
    models = Models()
    for u in usage:
        name = u.get("model", "")
        if name.startswith("turn-detector"):
            models.turn_detector = name
        elif "interruption" in name:
            models.interruption_detector = name
        elif "input_tokens" in u:
            models.llm = name
        elif "characters_count" in u:
            models.tts = name
        elif "audio_duration" in u:
            models.stt = name
    return models


def _pipeline_label(models: Models) -> str | None:
    key = (models.stt, models.llm, models.tts)
    return next((name for name, preset in PIPELINE_PRESETS.items() if preset == key), None)


def latency_stats(values: list[float]) -> LatencyStats:
    """Median and nearest-rank p90."""
    if not values:
        return LatencyStats(n=0, median_s=None, p90_s=None)
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    return LatencyStats(n=len(values), median_s=round(statistics.median(ordered), 3), p90_s=p90)


def _stats(turns: list[Turn], events: list[TimelineEvent], config: SessionConfig, duration_s) -> SessionStats:
    agent = [t for t in turns if t.speaker == "agent"]
    user = [t for t in turns if t.speaker == "user"]

    def collect(ts, attr):
        return [getattr(t.metrics, attr) for t in ts if getattr(t.metrics, attr) is not None]

    max_eot = config.max_endpointing_delay_s
    eot = collect(user, "end_of_turn_delay_s")
    return SessionStats(
        duration_s=duration_s,
        user_turns=len(user),
        agent_turns=len(agent),
        e2e_latency=latency_stats(collect(agent, "e2e_latency_s")),
        llm_ttft=latency_stats(collect(agent, "llm_ttft_s")),
        tts_ttfb=latency_stats(collect(agent, "tts_ttfb_s")),
        end_of_turn_delay=latency_stats(eot),
        endpointing_timeouts=sum(1 for v in eot if max_eot is not None and v >= max_eot - 0.05),
        false_interruptions=sum(1 for e in events if e.type == "false_interruption"),
        interrupted_agent_turns=sum(1 for t in agent if t.interrupted),
        overlapping_speech_events=sum(1 for e in events if e.type == "overlapping_speech"),
    )
