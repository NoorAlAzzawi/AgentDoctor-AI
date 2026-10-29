"""Portable regression checks using a sanitized real recording excerpt."""

import json
import re
from pathlib import Path

from agentdoctor.detectors import run_detectors
from agentdoctor.ingest import load_livekit_report, privacy_scan
from agentdoctor.store import DEFAULT_DATA_DIR

FIXTURE = Path(__file__).parent / "fixtures/recorded_tool_execution/nano_premature.relative-report.json"


def load_excerpt():
    return load_livekit_report(FIXTURE, session_id="recorded-nano-premature-end-call",
                              title="Recorded Nano validation", synthetic=False)


def test_real_recording_produces_one_confirmed_dispatch_finding():
    session = load_excerpt()
    findings = run_detectors(session)
    assert [f.detector for f in findings] == ["premature_end_call", "endpointing_timeout"]
    finding = findings[0]
    assert (finding.status, finding.category, finding.severity) == ("confirmed", "tool_execution", "high")
    assert finding.id == "recorded-nano-premature-end-call:premature_end_call:e011"
    assert (finding.time_range.start_s, finding.time_range.end_s) == (48.702, 48.708)
    assert finding.root_cause.confidence == "possible"
    assert "may have inferred" in finding.root_cause.statement
    assert "does not prove immediate audible disconnection" in finding.summary
    assert finding.root_cause.missing_evidence and finding.next_steps


def test_both_actual_lifecycles_and_evidence_links_are_preserved():
    session = load_excerpt()
    events = [e for e in session.events if e.type == "tool_execution"]
    assert [(e.id, e.t_s, e.data) for e in events] == [
        ("e011", 48.702, {"tool_name": "end_call", "call_ref": "tc001", "phase": "started"}),
        ("e012", 48.708, {"tool_name": "end_call", "call_ref": "tc002", "phase": "started"}),
        ("e013", 48.708, {"tool_name": "end_call", "call_ref": "tc001", "phase": "ended", "status": "done"}),
        ("e014", 48.711, {"tool_name": "end_call", "call_ref": "tc002", "phase": "ended", "status": "done"}),
    ]
    finding = run_detectors(session)[0]
    assert finding.evidence[0].event_id == "e011"
    assert {e.event_id for e in finding.evidence if e.event_id} == {e.id for e in events}
    assert finding.turn_ids == ["t002", "t003"]
    for evidence in finding.evidence:
        if evidence.event_id:
            event = next(e for e in events if e.id == evidence.event_id)
            assert evidence.t_s == event.t_s
            assert f"call_ref={event.data['call_ref']}" in evidence.value
        if evidence.turn_id:
            turn = next(t for t in session.turns if t.id == evidence.turn_id)
            assert (evidence.value, evidence.t_s) == (turn.text, turn.committed_s)
    assert any(e.kind == "config" and e.value ==
               "After you have both said goodbye, use the end_call tool to hang up."
               for e in finding.evidence)


def test_excerpt_privacy_and_relative_coordinates():
    text = FIXTURE.read_text()
    raw = json.loads(text)
    assert privacy_scan([text]) == []
    assert set(raw) == {"_comment", "sdk_version", "audio_recording_started_at", "options",
                        "usage", "chat_history", "events"}
    assert raw["audio_recording_started_at"] == 0
    assert "[clinician]" in text
    for forbidden in ("/Users/", "/private/", "job_id", "room_id", "group_id", "arguments",
                      '"output":', "deployment", "imported_at", "audio_recording_path"):
        assert forbidden not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)
    for event in raw["events"]:
        assert 0 <= event["created_at"] <= 50.784
        if event["type"] == "tool_execution_updated":
            assert set(event) == {"type", "update", "created_at"}
            update = event["update"]
            if update["type"] == "tool_call_started":
                assert set(update) == {"type", "function_call"}
                assert set(update["function_call"]) == {"name", "call_id"}
                ref = update["function_call"]["call_id"]
            else:
                assert set(update) == {"type", "call_id", "status"}
                ref = update["call_id"]
            assert re.fullmatch(r"tc\d{3}", ref)
    for item in raw["chat_history"]["items"]:
        if item["type"] == "message":
            assert 0 <= item["created_at"] <= 50.784
            assert 0 <= item["metrics"]["started_speaking_at"] <= 50.784
            assert 0 <= item["metrics"]["stopped_speaking_at"] <= 50.784


def test_reimport_is_deterministic_and_does_not_mutate_session():
    first, second = load_excerpt(), load_excerpt()
    before = first.model_dump_json()
    assert first.events == second.events
    assert first.turns == second.turns
    assert run_detectors(first) == run_detectors(second)
    assert first.model_dump_json() == before


def test_real_excerpt_stays_outside_application_dataset():
    session = load_excerpt()
    assert not session.synthetic and not session.source.synthetic
    assert DEFAULT_DATA_DIR not in FIXTURE.resolve().parents
    recorded = list(DEFAULT_DATA_DIR.glob("*.json"))
    assert len(recorded) == 11
    assert session.id not in {json.loads(p.read_text())["id"] for p in recorded}
