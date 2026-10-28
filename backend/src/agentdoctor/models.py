"""Normalized session and finding schemas. These are the API contract (see contracts/API.md).

All times are seconds relative to the session's recording start (`*_s` fields).
Fields are None when the source report does not contain the value; never guessed.
"""

from typing import Literal

from pydantic import BaseModel, Field

Speaker = Literal["user", "agent"]

Category = Literal[
    "speech_recognition",
    "llm_behavior",
    "conversation_state",
    "turn_taking",
    "tool_execution",
    "missing_or_incorrect_info",
    "latency",
]
Severity = Literal["high", "medium", "low"]
# Is the failure itself directly observed in the data, or only suspected?
FindingStatus = Literal["confirmed", "suspected"]
# How well the data supports the stated root cause.
RootCauseConfidence = Literal["confirmed", "likely", "possible"]

EventType = Literal[
    "agent_state",
    "user_state",
    "false_interruption",
    "overlapping_speech",
    "session_close",
]


# ---------- session ----------


class SourceInfo(BaseModel):
    format: Literal["livekit_session_report"] = "livekit_session_report"
    sdk_version: str | None
    original_name: str
    imported_at: str
    synthetic: bool = False


class Models(BaseModel):
    stt: str | None = None
    llm: str | None = None
    tts: str | None = None
    turn_detector: str | None = None
    interruption_detector: str | None = None


class SessionConfig(BaseModel):
    """Agent settings recorded in the report."""

    allow_interruptions: bool | None = None
    min_interruption_duration_s: float | None = None
    min_endpointing_delay_s: float | None = None
    max_endpointing_delay_s: float | None = None
    preemptive_generation: bool | None = None


class TurnMetrics(BaseModel):
    """Per-turn timings reported by LiveKit Agents. Agent turns carry LLM/TTS timings,
    user turns carry STT/endpointing timings."""

    e2e_latency_s: float | None = None
    end_of_turn_delay_s: float | None = None
    transcription_delay_s: float | None = None
    llm_ttft_s: float | None = None
    tts_ttfb_s: float | None = None
    playback_latency_s: float | None = None


class Turn(BaseModel):
    id: str = Field(description="Stable id, e.g. 't007'.")
    index: int
    speaker: Speaker
    text: str
    start_s: float | None = Field(
        description="When speech started (LiveKit started_speaking_at). For user turns this can "
        "be anchored to an earlier voice-activity blip, so compare with committed_s."
    )
    end_s: float | None = Field(description="When speech stopped, if reported.")
    committed_s: float | None = Field(
        description="When the turn was added to the chat history. Agent turns: when the reply "
        "was created, usually before its audio starts. User turns: after the final transcript."
    )
    interrupted: bool = False
    transcript_confidence: float | None = None
    metrics: TurnMetrics = TurnMetrics()


class TimelineEvent(BaseModel):
    id: str = Field(description="Stable id, e.g. 'e042'.")
    t_s: float
    type: EventType
    data: dict = Field(
        description="agent_state/user_state: {from, to}; false_interruption: {resumed}; "
        "overlapping_speech: {is_interruption, probability, detection_delay_s}; "
        "session_close: {reason, error}."
    )


class LatencyStats(BaseModel):
    n: int
    median_s: float | None
    p90_s: float | None


class SessionStats(BaseModel):
    """Counts and timings computed directly from the report (no inference)."""

    duration_s: float | None
    user_turns: int
    agent_turns: int
    e2e_latency: LatencyStats
    llm_ttft: LatencyStats
    tts_ttfb: LatencyStats
    end_of_turn_delay: LatencyStats
    endpointing_timeouts: int = Field(
        description="User turns whose end_of_turn_delay reached max_endpointing_delay."
    )
    false_interruptions: int
    interrupted_agent_turns: int
    overlapping_speech_events: int


class SessionSummary(BaseModel):
    id: str
    title: str
    notes: str | None
    pipeline_label: str | None = Field(description="Known preset name, or None if unrecognized.")
    models: Models
    synthetic: bool
    stats: SessionStats


class Session(SessionSummary):
    source: SourceInfo
    config: SessionConfig
    speaker_labels: dict[Speaker, str]
    agent_instructions: str | None = Field(description="System prompt recorded in the report.")
    turns: list[Turn]
    events: list[TimelineEvent]
    usage: list[dict]


# ---------- findings ----------


class Evidence(BaseModel):
    kind: Literal["transcript", "metric", "event", "config"]
    label: str
    value: str
    turn_id: str | None = None
    event_id: str | None = None
    t_s: float | None = None


class RootCause(BaseModel):
    statement: str
    confidence: RootCauseConfidence
    missing_evidence: list[str] = Field(
        default_factory=list, description="What would confirm or rule out this cause."
    )


class TimeRange(BaseModel):
    start_s: float
    end_s: float


class Finding(BaseModel):
    id: str
    session_id: str
    detector: str
    title: str
    summary: str
    category: Category
    severity: Severity
    status: FindingStatus
    time_range: TimeRange | None
    turn_ids: list[str]
    evidence: list[Evidence]
    root_cause: RootCause
    next_steps: list[str]


class FindingsResponse(BaseModel):
    session_id: str
    detectors_run: list[str]
    findings: list[Finding]


# ---------- comparison ----------


class ComparedMetric(BaseModel):
    key: str
    label: str
    unit: Literal["s", "count"]
    a: float | None
    b: float | None
    better: Literal["lower", "higher"] | None


class Comparison(BaseModel):
    a: SessionSummary
    b: SessionSummary
    same_models: dict[str, bool]
    metrics: list[ComparedMetric]
    findings_by_category: dict[str, dict[Literal["a", "b"], int]]
    caveats: list[str]
