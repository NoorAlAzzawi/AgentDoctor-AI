"""Versioned, exact rules over detector identity + source observations, never causes.

An occurrence is one existing Finding (including session-level timeout summaries).
Require >=2 distinct recorded sessions, exclude either synthetic provenance flag,
and keep unknown/insufficient/single-session patterns visible but ungrouped.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from .detectors import run_detectors
from .detectors.endpointing import TOLERANCE_S, looks_unfinished
from .detectors.llm_behavior import SUBSTANTIVE_WORDS
from .detectors.speech_recognition import AGENT_CONFUSED, MISSING_TIME_NUMBER, USER_CORRECTION
from .detectors._util import CLOSING_CUES, FAREWELL, next_turn, prev_turn
from .detectors.tool_execution import premature_end_call
from .models import (
    FailureFingerprint, Finding, FingerprintOccurrence, FingerprintsResponse, Session,
    UngroupedFinding,
)

CAVEAT = "Recurring behavior does not prove a shared root cause. Pipeline, model and configuration associations are descriptive, not causal."
CAVEATS = [
    CAVEAT,
    "Some recorded sessions repeat or share scripts/questions. Recurrence across them is not fully independent evidence; scenario independence is not quantified.",
    "Counts are existing findings, not independent trials, failure rates or raw events. An endpointing-timeout finding can summarize multiple turns, and different findings can overlap on the same turn.",
    "At least two distinct recorded sessions must support a fingerprint. Both synthetic provenance flags are checked; synthetic validation fixtures are excluded.",
]
# key -> (detector, category, expected status, name, exact observable grouping conditions)
RULES = {
    "premature-end-call-dispatch": ("premature_end_call", "tool_execution", "confirmed",
        "Premature end_call dispatch under the recorded closing policy",
        ["Linked end_call dispatch before the recognized recorded both-sides-goodbye policy was satisfied.",
         "Dispatch timestamps and evidence match the source timeline; terminal outcomes are separate observations.",
         "One occurrence is one finding, including any repeated dispatches; dispatch does not prove immediate audible disconnection."]),
    "interruption-mid-reply": ("false_interruption_pause", "turn_taking", "confirmed",
        "Agent pauses mid-reply after user voice activity",
        ["Linked resumed false-interruption event and user speaking-state trigger.", "Linked agent state changes from speaking to listening."]),
    "interruption-before-reply": ("false_interruption_pause", "turn_taking", "confirmed",
        "Agent holds a reply before speaking after user voice activity",
        ["Linked resumed false-interruption event and user speaking-state trigger.", "Linked agent state changes from thinking to listening."]),
    "endpointing-at-limit": ("endpointing_timeout", "latency", "confirmed",
        "User turns reach the configured endpointing limit",
        ["Linked user-turn delay metrics reach the configured maximum within the detector's 0.05s tolerance.", "One occurrence is one session-level detector finding, not one timed-out turn."]),
    "reply-to-fragment": ("fragment_reply", "turn_taking", "suspected",
        "Agent answers an apparently unfinished timed-out user turn",
        ["Linked user transcript matches the detector's unfinished-text heuristic and reaches the endpointing limit.", "A linked agent reply follows; unfinished speech is suspected, not proven without audio."]),
    "confusion-without-correction": ("stt_misunderstanding", "speech_recognition", "suspected",
        "Agent signals confusion after a user transcript",
        ["Linked agent transcript matches the existing confusion rule after a user turn.", "The next user turn does not match the detector's explicit correction rule; this does not prove an STT error."]),
    "confusion-with-correction": ("stt_misunderstanding", "speech_recognition", "suspected",
        "Agent confusion is followed by an explicit user correction",
        ["Linked user transcript, confused agent reply and explicit user correction.", "Recognition error remains suspected without audio/reference ground truth."]),
    "missing-time-number": ("stt_missing_time_number", "speech_recognition", "suspected",
        "User transcript contains AM or PM without an hour",
        ["Linked user transcript matches the missing-time-number rule.", "The transcript defect is visible; an STT error remains suspected without audio/reference ground truth."]),
    "premature-goodbye": ("premature_goodbye", "llm_behavior", "confirmed",
        "Agent says goodbye before the user finishes the conversation",
        ["Linked agent farewell with no preceding user closing cue.", "A linked substantive user turn follows without a farewell."]),
}
SUPPORTED = {rule[0] for rule in RULES.values()}


def _pattern(s: Session, f: Finding) -> str | None:
    """Require supporting linked source data; do not inspect root-cause prose."""
    turns = {t.id: t for t in s.turns}
    events = {e.id: e for e in s.events}
    if (f.session_id != s.id or not f.evidence
            or any(tid not in turns for tid in f.turn_ids)
            or any(e.turn_id is not None and e.turn_id not in turns
                   or e.event_id is not None and e.event_id not in events for e in f.evidence)):
        return None

    def quoted(t):
        return t is not None and any(e.kind == "transcript" and e.turn_id == t.id
                                     and e.value == t.text for e in f.evidence)

    def at_limit(t):
        limit = s.config.max_endpointing_delay_s
        return (t.speaker == "user" and limit is not None
                and t.metrics.end_of_turn_delay_s is not None
                and t.metrics.end_of_turn_delay_s >= limit - TOLERANCE_S
                and any(e.kind == "metric" and e.turn_id == t.id for e in f.evidence))

    linked = [turns[tid] for tid in f.turn_ids]
    key = None
    if f.detector == "premature_end_call":
        # Revalidate the observation using the unchanged detector's policy and timing
        # semantics. Match source-derived evidence, never diagnostic/cause prose.
        # This also rejects forged timestamps, request-only calls and stale terminal
        # evidence without creating a second interpretation of the closing policy.
        observed = next((item for item in premature_end_call(s) if item.id == f.id), None)
        if (observed is not None and f.evidence == observed.evidence
                and f.turn_ids == observed.turn_ids and f.time_range == observed.time_range):
            key = "premature-end-call-dispatch"
    elif f.detector == "false_interruption_pause":
        ev = [events[e.event_id] for e in f.evidence if e.kind == "event" and e.event_id]
        resumed = [e for e in ev if e.type == "false_interruption" and e.data.get("resumed") is True]
        held = [e for e in ev if e.type == "agent_state" and e.data.get("to") == "listening"]
        trigger = [e for e in ev if e.type == "user_state" and e.data.get("to") == "speaking"]
        if len(resumed) == len(held) == len(trigger) == 1 and trigger[0].t_s <= held[0].t_s < resumed[0].t_s:
            key = {"speaking": "interruption-mid-reply", "thinking": "interruption-before-reply"}.get(held[0].data.get("from"))
    elif f.detector == "endpointing_timeout" and linked and all(at_limit(t) for t in linked):
        key = "endpointing-at-limit"
    elif f.detector == "fragment_reply" and len(linked) == 2:
        user, reply = linked
        if at_limit(user) and looks_unfinished(user.text) and reply.speaker == "agent" and next_turn(s, user) == reply and quoted(user) and quoted(reply):
            key = "reply-to-fragment"
    elif f.detector == "stt_misunderstanding" and len(linked) >= 2:
        user, reply = linked[:2]
        if user.speaker == "user" and reply.speaker == "agent" and prev_turn(s, reply) == user and AGENT_CONFUSED.search(reply.text) and quoted(user) and quoted(reply):
            correction = next_turn(s, reply, "user")
            if correction and USER_CORRECTION.search(correction.text):
                if quoted(correction):
                    key = "confusion-with-correction"
            else:
                key = "confusion-without-correction"
    elif f.detector == "stt_missing_time_number":
        if any(t.speaker == "user" and MISSING_TIME_NUMBER.search(t.text) and quoted(t) for t in linked):
            key = "missing-time-number"
    elif f.detector == "premature_goodbye":
        for agent in linked:
            if agent.speaker != "agent" or not FAREWELL.search(agent.text):
                continue
            before, after = prev_turn(s, agent, "user"), next_turn(s, agent, "user")
            if (before and after and not CLOSING_CUES.search(before.text)
                    and not FAREWELL.search(after.text) and len(after.text.split()) >= SUBSTANTIVE_WORDS
                    and all(quoted(t) for t in (before, agent, after))):
                key = "premature-goodbye"
    if key and (f.detector, f.category, f.status) == RULES[key][:3]:
        return key
    return None


def build_fingerprints(sessions: Sequence[Session], findings: Mapping[str, Sequence[Finding]] | None = None) -> FingerprintsResponse:
    if len({s.id for s in sessions}) != len(sessions):
        raise ValueError("Session IDs must be unique")
    recorded = sorted((s for s in sessions if not s.synthetic and not s.source.synthetic), key=lambda s: s.id)
    buckets = defaultdict(list)
    ungrouped = []
    finding_count = 0

    def leave(s, f, reason):
        explanation = {
            "no_rule": "No supported observable grouping rule in version 1.",
            "insufficient_evidence": "Required linked source observations are missing, inconsistent or unsupported by the version 1 rule.",
            "single_session": "This observable pattern occurs in fewer than two distinct recorded sessions.",
        }[reason]
        ungrouped.append(UngroupedFinding(session_id=s.id, session_title=s.title, finding_id=f.id,
            detector=f.detector, category=f.category, severity=f.severity, status=f.status,
            reason=reason, explanation=explanation))

    for s in recorded:
        fs = findings[s.id] if findings is not None else run_detectors(s)
        unique = {f.id: f for f in fs}
        if len(unique) != len(fs):
            raise ValueError("Finding IDs must be unique within a session")
        for f in sorted(unique.values(), key=lambda f: f.id):
            finding_count += 1
            key = _pattern(s, f)
            if key:
                buckets[key].append((s, f))
            else:
                leave(s, f, "no_rule" if f.detector not in SUPPORTED else "insufficient_evidence")
    fingerprints = []
    for key, items in sorted(buckets.items()):
        session_ids = sorted({s.id for s, _ in items})
        if len(session_ids) < 2:
            for s, f in items:
                leave(s, f, "single_session")
            continue
        detector, category, _, name, characteristics = RULES[key]
        occurrences = [FingerprintOccurrence(
            session_id=s.id, session_title=s.title, session_notes=s.notes, pipeline_label=s.pipeline_label,
            models=s.models.model_copy(deep=True), config=s.config.model_copy(deep=True),
            finding_id=f.id, title=f.title, severity=f.severity, status=f.status,
            time_range=f.time_range.model_copy(deep=True) if f.time_range else None,
            evidence=[e.model_copy(deep=True) for e in f.evidence],
        ) for s, f in items]
        severity = Counter(f.severity for _, f in items)
        status = Counter(f.status for _, f in items)
        fingerprints.append(FailureFingerprint(
            id=f"fp-v1-{key}", name=name, detector=detector, category=category,
            characteristics=list(characteristics), occurrence_count=len(items),
            affected_session_count=len(session_ids), session_ids=session_ids,
            pipelines=sorted({s.pipeline_label for s, _ in items}, key=lambda p: (p is None, p or "")),
            severity_counts={k: severity[k] for k in ("high", "medium", "low")},
            status_counts={k: status[k] for k in ("confirmed", "suspected")},
            occurrences=occurrences, caveat=CAVEAT,
        ))
    return FingerprintsResponse(
        recorded_session_count=len(recorded), finding_count=finding_count,
        grouped_finding_count=sum(f.occurrence_count for f in fingerprints),
        excluded_synthetic_session_count=len(sessions) - len(recorded),
        fingerprints=fingerprints, ungrouped=sorted(ungrouped, key=lambda f: (f.session_id, f.finding_id)),
        caveats=list(CAVEATS),
    )
