"""Detector unit tests on small SYNTHETIC sessions (positive and negative cases)."""

from agentdoctor.detectors.endpointing import endpointing_timeout, looks_unfinished
from agentdoctor.detectors.interruptions import false_interruption_pause
from agentdoctor.detectors.llm_behavior import premature_goodbye, stage_direction_spoken, talked_after_farewell
from agentdoctor.detectors.speech_recognition import stt_misunderstanding, stt_missing_time_number
from agentdoctor.detectors.state import contradicts_instructions, readback_mismatch
from agentdoctor.models import (
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

INSTRUCTIONS = """You are Pat Doe.
- Full name: Pat Doe
- Date of birth: March 14, 1973
- Phone number: 805-555-0142
Once your appointment is confirmed, thank them and say goodbye."""


def make_session(lines, events=(), instructions=INSTRUCTIONS) -> Session:
    """lines: (speaker, text) or (speaker, text, extra dict). Turns are 5s apart."""
    turns = []
    for i, line in enumerate(lines):
        speaker, text, extra = (*line, {}) if len(line) == 2 else line
        metrics = TurnMetrics(**extra.pop("metrics", {}))
        turns.append(Turn(id=f"t{i + 1:03d}", index=i, speaker=speaker, text=text, start_s=i * 5.0,
                          end_s=i * 5.0 + 4.0, committed_s=i * 5.0 + 4.2, metrics=metrics, **extra))
    timeline = [TimelineEvent(id=f"e{i + 1:03d}", t_s=t, type=k, data=d) for i, (t, k, d) in enumerate(events)]
    empty = LatencyStats(n=0, median_s=None, p90_s=None)
    return Session(
        id="synthetic", title="Synthetic", notes=None, pipeline_label=None, models=Models(), synthetic=True,
        stats=SessionStats(duration_s=None, user_turns=sum(t.speaker == "user" for t in turns),
                           agent_turns=sum(t.speaker == "agent" for t in turns), e2e_latency=empty,
                           llm_ttft=empty, tts_ttfb=empty, end_of_turn_delay=empty, endpointing_timeouts=0,
                           false_interruptions=0, interrupted_agent_turns=0, overlapping_speech_events=0),
        source=SourceInfo(sdk_version=None, original_name="synthetic", imported_at="2026-01-01T00:00:00+00:00",
                          synthetic=True),
        config=SessionConfig(max_endpointing_delay_s=2.5),
        speaker_labels={"user": "User", "agent": "Agent"},
        agent_instructions=instructions, turns=turns, events=timeline, usage=[],
    )


# ---------- false interruptions ----------

def _pause_events(trigger_after_start: float):
    return [
        (5.0, "agent_state", {"from": "thinking", "to": "speaking"}),
        (5.0 + trigger_after_start, "user_state", {"from": "listening", "to": "speaking"}),
        (5.5 + trigger_after_start, "user_state", {"from": "speaking", "to": "listening"}),
        (5.8 + trigger_after_start, "agent_state", {"from": "speaking", "to": "listening"}),
        (8.0 + trigger_after_start, "agent_state", {"from": "listening", "to": "speaking"}),
        (8.0 + trigger_after_start, "false_interruption", {"resumed": True}),
    ]


def test_mid_reply_pause_is_confirmed_with_echo_hypothesis():
    s = make_session([("user", "Hello?"), ("agent", "Hi, I'd like to book an appointment please.")],
                     _pause_events(0.3))
    [f] = false_interruption_pause(s)
    assert f.status == "confirmed" and f.severity == "medium"
    assert f.title.startswith("Agent paused mid-reply for 2.2s")
    assert "echo" in f.root_cause.statement and f.root_cause.confidence == "possible"
    assert f.turn_ids == ["t002"]
    assert {e.event_id for e in f.evidence if e.event_id} == {"e001", "e002", "e004", "e006"}


def test_late_trigger_is_not_attributed_to_echo():
    s = make_session([("user", "Hello?"), ("agent", "Hi, I'd like to book an appointment please.")],
                     _pause_events(1.5))
    [f] = false_interruption_pause(s)
    assert "echo" not in f.root_cause.statement


def test_pause_followed_by_repeat_request_is_high_severity():
    s = make_session([("user", "Hello?"), ("agent", "My birthday is March 14, 1973."),
                      ("user", "Sorry, I did not hear you.")], _pause_events(0.3))
    [f] = false_interruption_pause(s)
    assert f.severity == "high" and "t003" in f.turn_ids


def test_no_false_interruption_events_means_no_findings():
    assert false_interruption_pause(make_session([("user", "Hi"), ("agent", "Hello")])) == []


# ---------- endpointing ----------

def test_endpointing_timeouts_and_fragment_reply():
    s = make_session([
        ("user", "Could you please tell me your", {"metrics": {"end_of_turn_delay_s": 2.5}}),
        ("agent", "My name is Pat."),
        ("user", "Thanks, that's all.", {"metrics": {"end_of_turn_delay_s": 2.5}}),
        ("agent", "Bye."),
        ("user", "Okay.", {"metrics": {"end_of_turn_delay_s": 0.58}}),
    ])
    findings = endpointing_timeout(s)
    summary = next(f for f in findings if f.detector == "endpointing_timeout")
    assert summary.status == "confirmed" and summary.turn_ids == ["t001", "t003"]
    [fragment] = [f for f in findings if f.detector == "fragment_reply"]
    assert fragment.status == "suspected" and fragment.turn_ids == ["t001", "t002"]


def test_unfinished_sentence_heuristic():
    assert looks_unfinished("Could you please tell me your")
    assert looks_unfinished("In 3 weeks, Tuesday— sorry, um, Monday,")
    assert not looks_unfinished("What is your date of birth?")


# ---------- speech recognition ----------

def test_missing_time_number_is_suspected_never_confirmed():
    s = make_session([("user", "Let's go with Thursday at AM."), ("agent", "Thursday morning works.")])
    [f] = stt_missing_time_number(s)
    assert f.status == "suspected" and f.root_cause.confidence == "likely"
    assert f.turn_ids == ["t001", "t002"]
    assert stt_missing_time_number(make_session([("user", "Thursday at 8 AM works.")])) == []


def test_misunderstanding_with_user_correction_is_likely():
    s = make_session([("user", "So we are buying Patrick Swayze."),
                      ("agent", "I'm sorry, what? Who is Patrick Swayze?"),
                      ("user", "No, no, I said when is your appointment?")])
    [f] = stt_misunderstanding(s)
    assert f.status == "suspected" and f.root_cause.confidence == "likely"
    assert f.turn_ids == ["t001", "t002", "t003"]


def test_misunderstanding_without_correction_is_only_possible():
    s = make_session([("user", "Say something else."), ("agent", "Um, I'm not sure what you mean.")])
    [f] = stt_misunderstanding(s)
    assert f.root_cause.confidence == "possible" and f.severity == "low"


# ---------- LLM behavior ----------

def test_stage_direction_is_confirmed():
    [f] = stage_direction_spoken(make_session([("user", "Okay."), ("agent", "(End of call)")]))
    assert f.status == "confirmed" and f.category == "llm_behavior"
    assert stage_direction_spoken(make_session([("agent", "Sure, see you Thursday.")])) == []


def test_premature_goodbye_cites_the_instruction():
    s = make_session([("user", "Let's go with Thursday at 8 AM."), ("agent", "That works. Goodbye!"),
                      ("user", "One moment, please. I'm checking your information.")])
    [f] = premature_goodbye(s)
    assert f.status == "confirmed" and f.root_cause.confidence == "likely"
    assert any(e.kind == "config" and "goodbye" in e.value for e in f.evidence)


def test_goodbye_after_closing_cue_is_not_premature():
    s = make_session([("user", "You're all set. Anything else?"), ("agent", "No, that's all. Goodbye."),
                      ("user", "Sorry, could you repeat your phone number?")])
    assert premature_goodbye(s) == []


def test_talked_after_farewell():
    s = make_session([("user", "Bye-bye."), ("agent", "Bye-bye."), ("user", "Hello?"),
                      ("agent", "Hello? I'm still here.")])
    [f] = talked_after_farewell(s)
    assert f.status == "confirmed" and f.turn_ids == ["t001", "t002", "t004"]
    assert talked_after_farewell(make_session([("user", "Goodbye."), ("agent", "Goodbye.")])) == []


# ---------- state ----------

def test_contradicting_own_instructions():
    s = make_session([("user", "What is your date of birth?"), ("agent", "It's March 14, 1975.")])
    [f] = contradicts_instructions(s)
    assert f.status == "confirmed" and f.category == "missing_or_incorrect_info"
    ok = make_session([("user", "What is your date of birth?"), ("agent", "It's March 14, 1973.")])
    assert contradicts_instructions(ok) == []


def test_readback_mismatch():
    s = make_session([("user", "I have Thursday at 8 AM."), ("agent", "Great."),
                      ("user", "Could you repeat the appointment day and time?"),
                      ("agent", "It's Tuesday at 9 AM.")])
    [f] = readback_mismatch(s)
    assert f.category == "conversation_state" and "Tuesday vs Thursday" in f.title
    ok = make_session([("user", "I have Thursday at 8 AM."), ("agent", "Great."),
                       ("user", "Could you repeat the appointment day and time?"),
                       ("agent", "It's Thursday at eight AM.")])
    assert readback_mismatch(ok) == []
