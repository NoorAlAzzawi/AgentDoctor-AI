"""In-memory controlled cases, never additional recorded production recurrence."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentdoctor.api import app
from agentdoctor.detectors import run_detectors
from agentdoctor.detectors.tool_execution import premature_end_call
from agentdoctor.fingerprints import build_fingerprints
from agentdoctor.ingest import load_livekit_report, privacy_scan
from agentdoctor.models import Session, TimelineEvent, Turn
from agentdoctor.store import DEFAULT_DATA_DIR

POLICY = "After you have both said goodbye, use the end_call tool to hang up."
FP_ID = "fp-v1-premature-end-call-dispatch"


def unit_session(sid, status="done"):
    template = Path(__file__).parent / "fixtures/synthetic_faults/premature_goodbye.json"
    s = Session.model_validate(json.loads(template.read_text())["session"])
    s.id = s.source.original_name = sid
    s.title = "Controlled grouping unit case (not a recorded observation)"
    # Exercise recorded-eligibility branches in memory only, never save these cases.
    s.synthetic = s.source.synthetic = False
    s.agent_instructions = POLICY
    s.turns = [Turn(id="t001", index=0, speaker="user", text="I need directions.", committed_s=1, start_s=None, end_s=None),
               Turn(id="t002", index=1, speaker="agent", text="Where are you starting from?", committed_s=2, start_s=None, end_s=None)]
    s.events = []
    for i, at in enumerate((3.0, 4.0)):
        ref = f"tc{i + 1:03d}"
        s.events.extend([
            TimelineEvent(id=f"e{i * 2 + 1:03d}", t_s=at, type="tool_execution",
                          data={"tool_name": "end_call", "call_ref": ref, "phase": "started"}),
            TimelineEvent(id=f"e{i * 2 + 2:03d}", t_s=at + .1, type="tool_execution",
                          data={"tool_name": "end_call", "call_ref": ref, "phase": "ended", "status": status}),
        ])
    return s


def test_two_sessions_group_stably_with_one_occurrence_per_aggregated_finding():
    a, b = unit_session("unit-a"), unit_session("unit-b")
    r = build_fingerprints([a, b])
    [fp] = r.fingerprints
    assert (fp.id, fp.occurrence_count, fp.affected_session_count) == (FP_ID, 2, 2)
    assert fp.status_counts == {"confirmed": 2, "suspected": 0}
    assert fp.session_ids == ["unit-a", "unit-b"]
    for o in fp.occurrences:
        assert o.finding_id == f"{o.session_id}:premature_end_call:e001"
        assert len([e for e in o.evidence if "phase=started" in e.value]) == 2
    assert r == build_fingerprints([b, a]) == build_fingerprints([a, b])
    assert "does not prove a shared root cause" in fp.caveat
    assert any("not fully independent" in c for c in r.caveats)
    assert any("not independent trials" in c for c in r.caveats)


def test_one_session_stays_ungrouped_despite_two_dispatches():
    r = build_fingerprints([unit_session("unit-a")])
    assert r.fingerprints == [] and r.finding_count == 1
    assert [f.reason for f in r.ungrouped] == ["single_session"]


@pytest.mark.parametrize("mismatch", ["event_id", "timestamp", "tool", "phase", "policy",
                                     "policy_evidence", "event_value", "transcript", "status", "category"])
def test_invalid_source_or_evidence_cannot_qualify(mismatch):
    a, b = unit_session("unit-a"), unit_session("unit-b")
    findings = {s.id: premature_end_call(s) for s in (a, b)}
    f = findings[a.id][0]
    if mismatch == "event_id":
        f.evidence[0].event_id = "e002"  # existing event, but terminal, not dispatch
    elif mismatch == "timestamp":
        f.evidence[0].t_s += .01
    elif mismatch == "tool":
        a.events[0].data["tool_name"] = "hold_call"
    elif mismatch == "phase":
        a.events[0].data.update(phase="ended", status="done")
    elif mismatch == "policy":
        a.agent_instructions = "Use end_call when appropriate."
    elif mismatch == "policy_evidence":
        next(e for e in f.evidence if e.kind == "config").value = "Use end_call when appropriate."
    elif mismatch == "event_value":
        f.evidence[0].value = "call_ref=tc999; phase=started"
    elif mismatch == "transcript":
        next(e for e in f.evidence if e.turn_id).value = "Unsupported text"
    elif mismatch == "status":
        f.status = "suspected"
    else:
        f.category = "llm_behavior"
    r = build_fingerprints([a, b], findings)
    assert not r.fingerprints
    assert next(f for f in r.ungrouped if f.session_id == a.id).reason == "insufficient_evidence"


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_terminal_status_does_not_define_occurrence_identity(status):
    a, b = unit_session("unit-a", status), unit_session("unit-b", status)
    [fp] = build_fingerprints([a, b]).fingerprints
    assert fp.id == FP_ID and fp.occurrence_count == 2
    for o in fp.occurrences:
        ended = [e for e in o.evidence if "phase=ended" in e.value]
        assert len(ended) == 2 and all(f"status={status}" in e.value for e in ended)
        assert all("not disconnection timing" in e.label for e in ended)


def test_request_only_and_close_cannot_validate_supplied_dispatch_findings(tmp_path):
    a, b = unit_session("unit-a"), unit_session("unit-b")
    original = {s.id: premature_end_call(s) for s in (a, b)}
    raw = {"audio_recording_started_at": 0, "chat_history": {"items": [
        {"type": "agent_config_update", "instructions": POLICY},
        {"type": "message", "role": "user", "content": ["I need help."], "created_at": 1},
        {"type": "message", "role": "assistant", "content": ["How can I help?"], "created_at": 2},
        {"type": "function_call", "name": "end_call", "call_id": "private-unit-request", "created_at": 3},
    ]}, "events": [{"type": "close", "created_at": 4, "reason": "user_initiated", "error": None}]}
    p = tmp_path / "request.json"
    p.write_text(json.dumps(raw))
    a = load_livekit_report(p, session_id=a.id, title=a.title, synthetic=False)
    assert not premature_end_call(a)
    assert not build_fingerprints([a, b], original).fingerprints


def test_legitimate_closing_with_later_audio_has_no_tool_fingerprint():
    a = unit_session("unit-a")
    a.turns[0].text = a.turns[1].text = "Goodbye."
    a.turns[1].start_s, a.turns[1].end_s = 5, 6
    assert not premature_end_call(a)
    assert not build_fingerprints([a, unit_session("unit-b")]).fingerprints


def test_tool_grouping_ignores_cause_prose_and_preserves_inputs_and_privacy():
    sessions = [unit_session("unit-a"), unit_session("unit-b")]
    fs = {s.id: premature_end_call(s) for s in sessions}
    expected = build_fingerprints(sessions, fs)
    for items in fs.values():
        items[0].root_cause.statement = "Unrelated hypothesis, not a grouping input."
        items[0].root_cause.confidence = "likely"
    before = [s.model_dump_json() for s in sessions], {k: [f.model_dump_json() for f in v] for k, v in fs.items()}
    r = build_fingerprints(sessions, fs)
    assert r == expected
    assert not privacy_scan([r.model_dump_json()])
    for forbidden in ('"arguments":', '"output":', "raw_call_id", "/Users/", "api_key"):
        assert forbidden not in r.model_dump_json()
    r.fingerprints[0].occurrences[0].evidence[0].value = "output mutation"
    assert before == ([s.model_dump_json() for s in sessions],
                      {k: [f.model_dump_json() for f in v] for k, v in fs.items()})


def test_api_does_not_discover_recorded_regression_excerpts(monkeypatch):
    monkeypatch.delenv("AGENTDOCTOR_DATA_DIR", raising=False)
    fixture = Path(__file__).parent / "fixtures/recorded_tool_execution/nano_premature.relative-report.json"
    assert fixture.exists() and DEFAULT_DATA_DIR not in fixture.resolve().parents
    with TestClient(app) as client:
        r = client.get("/api/fingerprints").json()
    assert (r["recorded_session_count"], r["finding_count"], r["grouped_finding_count"]) == (11, 42, 39)
    assert next(f for f in r["fingerprints"] if f["id"] == FP_ID)["occurrence_count"] == 3
    assert all(o["session_id"] != "recorded-nano-premature-end-call" for f in r["fingerprints"] for o in f["occurrences"])
    expected = Path(__file__).resolve().parents[2] / "contracts/examples/fingerprints.json"
    assert r == json.loads(expected.read_text())
    assert len(list(DEFAULT_DATA_DIR.glob("*.json"))) == 11
