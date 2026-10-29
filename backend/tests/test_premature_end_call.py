"""Isolated normalized unit cases: no new real recordings or stored fixtures."""

import json
from pathlib import Path

import pytest

from agentdoctor.detectors import DETECTORS, run_detectors
from agentdoctor.detectors.tool_execution import premature_end_call
from agentdoctor.ingest import load_livekit_report
from agentdoctor.models import Session, TimelineEvent, Turn
from agentdoctor.store import DEFAULT_DATA_DIR

POLICY = "After you have both said goodbye, use the end_call tool to hang up."


def dispatch(ref="tc001", at=10.0, event_id="e001", name="end_call"):
    return TimelineEvent(id=event_id, t_s=at, type="tool_execution",
                         data={"tool_name": name, "call_ref": ref, "phase": "started"})


def terminal(ref="tc001", at=10.5, event_id="e002", status="done"):
    return TimelineEvent(id=event_id, t_s=at, type="tool_execution",
                         data={"tool_name": "end_call", "call_ref": ref, "phase": "ended", "status": status})


def make_session(lines=None, events=None, policy=POLICY):
    # Reuse only the existing synthetic Session envelope; replace the conversation in memory.
    path = Path(__file__).parent / "fixtures" / "synthetic_faults" / "premature_goodbye.json"
    s = Session.model_validate(json.loads(path.read_text())["session"])
    s.id = s.source.original_name = "synthetic-end-call-unit"
    s.title = "SYNTHETIC premature_end_call unit test"
    s.agent_instructions = policy
    lines = lines if lines is not None else [
        ("user", "I need help scheduling an appointment."),
        ("agent", "What day would work for you?"),
        ("user", "Next week would be good."),
    ]
    s.turns = [Turn(id=f"t{i + 1:03d}", index=i, speaker=speaker, text=text,
                    committed_s=i * 3.0 + 1, start_s=i * 3.0 + .5, end_s=i * 3.0 + .9)
               for i, (speaker, text) in enumerate(lines)]
    s.stats.user_turns = sum(t.speaker == "user" for t in s.turns)
    s.stats.agent_turns = sum(t.speaker == "agent" for t in s.turns)
    s.events = events if events is not None else [dispatch(), terminal()]
    return s


def test_confirmed_early_dispatch_is_registered_and_traceable():
    s = make_session()
    [f] = premature_end_call(s)
    assert premature_end_call in DETECTORS
    assert run_detectors(s) == [f]
    assert (f.detector, f.status, f.category, f.severity) == ("premature_end_call", "confirmed", "tool_execution", "high")
    assert f.time_range.start_s == f.time_range.end_s == 10.0
    assert f.evidence[0].event_id == "e001" and f.evidence[0].t_s == 10.0
    assert f.evidence[0].value == "call_ref=tc001; phase=started"
    assert any(e.kind == "config" and e.value == POLICY for e in f.evidence)
    assert set(f.turn_ids) == {"t002", "t003"}
    for e in f.evidence:
        if e.turn_id:
            turn = next(t for t in s.turns if t.id == e.turn_id)
            assert e.value == turn.text and e.t_s == turn.committed_s
        if e.event_id:
            assert any(event.id == e.event_id and event.t_s == e.t_s for event in s.events)
    assert f.root_cause.confidence == "possible"
    assert "may have inferred" in f.root_cause.statement
    assert "LLM" not in f.root_cause.statement
    assert f.root_cause.missing_evidence and f.next_steps
    assert "does not prove immediate audible disconnection" in f.summary


@pytest.mark.parametrize("goodbye", ["Goodbye.", "Good bye.", "Bye-bye."])
def test_legitimate_exchange_before_dispatch_is_not_flagged(goodbye):
    s = make_session([("user", goodbye), ("agent", goodbye)])
    assert premature_end_call(s) == []


def test_assistant_committed_goodbye_before_dispatch_with_later_playback_is_not_flagged():
    s = make_session([("user", "Goodbye."), ("agent", "Thank you. Goodbye.")])
    s.turns[1].start_s = 11.0
    s.turns[1].end_s = 13.0
    assert s.turns[1].committed_s < 10.0 < s.turns[1].start_s
    assert premature_end_call(s) == []


def test_future_goodbye_does_not_retroactively_excuse_earlier_dispatch():
    s = make_session([("user", "Goodbye."), ("agent", "I have your appointment noted."),
                      ("agent", "Goodbye.")])
    s.turns[2].committed_s = 12.0
    s.turns[2].start_s = 13.0
    s.turns[2].end_s = 14.0
    s.events += [dispatch("tc002", 15.0, "e003"), terminal("tc002", 15.5, "e004")]
    [f] = premature_end_call(s)
    assert {e.event_id for e in f.evidence if e.event_id} == {"e001", "e002"}


def test_late_user_transcript_with_earlier_goodbye_speech_is_conservative():
    s = make_session([("user", "I have one more question."), ("agent", "Goodbye."),
                      ("user", "Goodbye.")])
    s.turns[2].committed_s = 12.0
    s.turns[2].start_s = 7.0
    s.turns[2].end_s = 8.0
    assert premature_end_call(s) == []


def test_ambiguous_farewell_timing_does_not_create_a_confirmed_violation():
    s = make_session([("user", "I need assistance."), ("agent", "How can I help?"),
                      ("user", "Goodbye."), ("agent", "Goodbye.")])
    for t in s.turns[2:]:
        t.committed_s = t.start_s = t.end_s = None
    assert premature_end_call(s) == []


def test_request_only_raw_function_call_is_not_dispatch(tmp_path):
    path = tmp_path / "synthetic-request-only.json"
    raw = {"audio_recording_started_at": 1000.0, "sdk_version": "1.8.8", "events": [],
           "chat_history": {"items": [
               {"type": "agent_config_update", "instructions": POLICY},
               {"type": "message", "role": "user", "content": ["I need help."], "created_at": 1001.0},
               {"type": "message", "role": "assistant", "content": ["How can I help?"], "created_at": 1002.0},
               {"type": "function_call", "name": "end_call", "arguments": "{}", "call_id": "synthetic-raw-id", "created_at": 1003.0},
           ]}}
    path.write_text(json.dumps(raw))
    s = load_livekit_report(path, session_id="synthetic-request", title="Synthetic request", synthetic=True)
    assert len(s.turns) == 2 and s.events == []
    assert premature_end_call(s) == []


def test_session_close_alone_is_not_dispatch():
    s = make_session(events=[TimelineEvent(id="e001", t_s=10.0, type="session_close",
                                          data={"reason": "user_initiated", "error": None})])
    assert premature_end_call(s) == []


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_terminal_outcome_does_not_change_the_observed_dispatch_violation(status):
    [f] = premature_end_call(make_session(events=[dispatch(), terminal(status=status)]))
    assert f.status == "confirmed"
    ended = [e for e in f.evidence if e.event_id == "e002"]
    assert len(ended) == 1 and f"status={status}" in ended[0].value
    assert "not disconnection timing" in ended[0].label
    assert ended[0].t_s == 10.5
    assert f.time_range.end_s == 10.0  # this finding concerns dispatch, not completion


def test_dispatch_without_completion_does_not_invent_a_terminal_outcome():
    [f] = premature_end_call(make_session(events=[dispatch()]))
    assert [e.event_id for e in f.evidence if e.kind == "event"] == ["e001"]
    assert not any("status=" in e.value for e in f.evidence)


def test_completion_without_dispatch_does_not_trigger():
    assert premature_end_call(make_session(events=[terminal()])) == []


def test_repeated_distinct_dispatches_share_one_finding_without_losing_evidence():
    events = [terminal("tc002", 10.8, "e004", "cancelled"), dispatch("tc002", 10.1, "e003"),
              terminal(), dispatch()]
    s = make_session(events=events)
    before = s.model_dump_json()
    [f] = premature_end_call(s)
    assert f.id == "synthetic-end-call-unit:premature_end_call:e001"
    assert {e.event_id for e in f.evidence if e.event_id} == {"e001", "e002", "e003", "e004"}
    assert len([e for e in f.evidence if "phase=started" in e.value]) == 2
    assert f.time_range.start_s == 10.0 and f.time_range.end_s == 10.1
    assert f == premature_end_call(s)[0]
    assert f == premature_end_call(s.model_copy(update={"events": list(reversed(events))}))[0]
    assert s.model_dump_json() == before


def test_duplicate_reference_is_not_counted_twice():
    s = make_session(events=[dispatch(), dispatch(event_id="e003"), terminal()])
    [f] = premature_end_call(s)
    assert len([e for e in f.evidence if "phase=started" in e.value]) == 1


@pytest.mark.parametrize("policy", [None, "", "You are a helpful agent.",
    "Use end_call when the user is done.", "Say goodbye after confirming the appointment.",
    "Example of a bad instruction: " + POLICY,
    POLICY + "\nIf the call is urgent, call end_call immediately.",
    "Do not follow this instruction: " + POLICY])
def test_unknown_or_conflicting_policy_abstains(policy):
    assert premature_end_call(make_session(policy=policy)) == []


def test_supported_only_after_policy_variant():
    [f] = premature_end_call(make_session(policy="Only call end_call after both sides have said goodbye."))
    assert f.status == "confirmed"


def test_natural_wrapup_does_not_substitute_for_stricter_recorded_rule():
    s = make_session([("user", "Is there anything else?"), ("agent", "No, that's all. Thank you.")])
    assert len(premature_end_call(s)) == 1


def test_other_tools_and_missing_context_are_not_flagged():
    assert premature_end_call(make_session(events=[dispatch(name="hold_call")])) == []
    assert premature_end_call(make_session(lines=[])) == []
    s = make_session()
    for t in s.turns:
        t.committed_s = None
    assert premature_end_call(s) == []


def test_all_seven_legacy_recorded_findings_are_unchanged():
    examples = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "by-session"
    files = sorted(DEFAULT_DATA_DIR.glob("*.json"))
    assert len(files) == 7
    for p in files:
        s = Session.model_validate_json(p.read_text())
        assert premature_end_call(s) == []
        expected = json.loads((examples / f"{s.id}.findings.json").read_text())["findings"]
        assert [f.model_dump(mode="json") for f in run_detectors(s)] == expected
