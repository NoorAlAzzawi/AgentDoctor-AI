"""Expanded recorded dataset: provenance, privacy and unchanged detector behavior."""

import hashlib
import json
import runpy
from collections import Counter
from pathlib import Path

import pytest

from agentdoctor.detectors import run_detectors
from agentdoctor.fingerprints import build_fingerprints
from agentdoctor.ingest import privacy_scan
from agentdoctor.models import Session
from agentdoctor.store import DEFAULT_DATA_DIR

IMPORTER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/import_expanded_dataset.py"))
NEW_IDS = [entry[1] for entry in IMPORTER["APPROVED"]]
# Frozen bytes from safety baseline 9bb5e9c, not regenerated expectations.
LEGACY_SHA256 = {'s01-workshop-improvised.json': '6f1521fbe45d15d3cc80becaebb3e7c822091143abe9e3efdf75a976d41462e1', 's02-workshop-structured.json': '1241dd4abe7ca4278d67085a0daa4682f5b0fb5fd4ad0aefcb51fd4a30d64648', 's03-workshop-ab-script.json': 'b28763c9e3f68c04e5bfc0068181c28a33c88b5d9e393c29c56957f582b649a3', 's04-recommended-ab-script.json': 'ea44fc8ac687b3d49894947bc5e8b41c218155c8cbb39b785bbeba0ec6ee3fbf', 's05-workshop-headphones.json': '8cf34d8016baf68d899a50d842d096c39cd438d332b06fd385ffa99f39a734a0', 's06-hybrid-script.json': '7edc89bfffa7d5540e1631fed6808486a1ca5353cc3b8c0565aa6a3987040bbc', 's07-workshop-persona-v2.json': '3a1c573c8a27269ac347666e7ec19134b91a153743143b0b39b7888e11bc44e2'}


def sessions():
    return [Session.model_validate_json(p.read_text()) for p in sorted(DEFAULT_DATA_DIR.glob("*.json"))]


def test_original_seven_are_byte_for_byte_unchanged():
    for name, expected in LEGACY_SHA256.items():
        assert hashlib.sha256((DEFAULT_DATA_DIR / name).read_bytes()).hexdigest() == expected


def test_only_four_approved_sources_are_added_without_fixture_duplication():
    all_sessions = sessions()
    assert len(all_sessions) == len({s.id for s in all_sessions}) == 11
    new = [s for s in all_sessions if s.id in NEW_IDS]
    assert len(new) == 4
    assert {s.source.original_name for s in new} == {e[0] for e in IMPORTER["APPROVED"]}
    assert len({s.source.original_name for s in all_sessions}) == 11
    assert not any(s.synthetic or s.source.synthetic for s in all_sessions)
    assert all(s.source.original_name not in {"session-10-28-182501", "session-10-28-184450"} for s in all_sessions)
    assert "recorded-nano-premature-end-call" not in {s.id for s in all_sessions}


def test_expanded_counts_are_actual_registry_output_and_deterministic():
    ss = sessions()
    before = [s.model_dump_json() for s in ss]
    fs = [f for s in ss for f in run_detectors(s)]
    assert Counter(f.status for f in fs) == {"confirmed": 27, "suspected": 15}
    assert len(fs) == 42
    r = build_fingerprints(ss)
    assert (len(r.fingerprints), r.grouped_finding_count, len(r.ungrouped)) == (8, 39, 3)
    assert r == build_fingerprints(list(reversed(ss)))
    assert [s.model_dump_json() for s in ss] == before


def test_dispatch_fingerprint_uses_three_distinct_real_findings_not_four_dispatches():
    ss = sessions()
    fp = next(f for f in build_fingerprints(ss).fingerprints if f.detector == "premature_end_call")
    assert fp.id == "fp-v1-premature-end-call-dispatch"
    assert fp.occurrence_count == fp.affected_session_count == 3
    assert fp.session_ids == [NEW_IDS[0], NEW_IDS[1], NEW_IDS[3]]
    assert fp.pipelines == ["nano", "recommended"]
    assert fp.status_counts == {"confirmed": 3, "suspected": 0}
    first = next(o for o in fp.occurrences if o.session_id == NEW_IDS[0])
    assert len([e for e in first.evidence if "phase=started" in e.value]) == 2
    assert {e.event_id for e in first.evidence if "phase=started" in e.value} == {"e011", "e012"}
    for o in fp.occurrences:
        source = next(s for s in ss if s.id == o.session_id)
        for e in o.evidence:
            if e.event_id:
                event = next(ev for ev in source.events if ev.id == e.event_id)
                assert event.t_s == e.t_s and event.type == "tool_execution"
        [f] = [f for f in run_detectors(source) if f.detector == "premature_end_call"]
        assert f.root_cause.confidence == "possible"
        assert "does not prove immediate audible disconnection" in f.summary


def test_control_preserves_commit_vs_playback_and_other_timing_anomalies():
    control = next(s for s in sessions() if s.id == NEW_IDS[2])
    [dispatch] = [e for e in control.events if e.type == "tool_execution" and e.data["phase"] == "started"]
    goodbye = control.turns[-1]
    assert goodbye.committed_s == 240.062 < dispatch.t_s == 240.863 < goodbye.start_s == 241.284
    assert control.stats.e2e_latency.median_s == 10.659
    assert {f.detector for f in run_detectors(control)} == {"fragment_reply", "endpointing_timeout"}


def test_role_confound_and_successful_corrected_readback_are_not_hidden():
    new = {s.id: s for s in sessions() if s.id in NEW_IDS}
    role = new[NEW_IDS[0]]
    assert "Role-confounded" in role.notes and "not a normal baseline" in role.notes
    assert "patient role (confounded)" in role.speaker_labels["user"]
    assert "receptionist role (confounded)" in role.speaker_labels["agent"]
    nano = new[NEW_IDS[3]]
    assert nano.pipeline_label == role.pipeline_label == "nano"
    assert nano.turns[12].text == "Sure. It's Thursday at eight AM."
    assert not any(f.detector == "readback_mismatch" for f in run_detectors(nano))
    [stt] = [f for f in run_detectors(nano) if f.detector == "stt_missing_time_number"]
    assert stt.status == "suspected" and "does not prove an STT error" in nano.notes


def test_public_projection_omits_private_payloads_and_unrelated_profile():
    for s in sessions():
        if s.id not in NEW_IDS:
            continue
        text = s.model_dump_json()
        assert not privacy_scan([t.text for t in s.turns] + [s.agent_instructions, s.notes])
        for value in ("Michael", "Lopez", "Patel", "Sarah", "Westside", "Westar", "1973", "/Users/", "/private/",
                      "audio_recording_path", "job_id", "room_id", "group_id", "arguments", "inference_deployment"):
            assert value not in text
        assert s.usage == [] and "Your details" not in s.agent_instructions
        assert "both said goodbye" in s.agent_instructions
        for event in s.events:
            assert 0 <= event.t_s <= s.stats.duration_s
            if event.type == "tool_execution":
                assert set(event.data) <= {"tool_name", "call_ref", "phase", "status"}
        for turn in s.turns:
            for time in (turn.committed_s, turn.start_s, turn.end_s):
                assert time is None or 0 <= time <= s.stats.duration_s


def test_projection_is_reproducible_and_does_not_introduce_stage_directions(tmp_path):
    instructions = next(s for s in sessions() if s.id == NEW_IDS[0]).agent_instructions
    raw = {"audio_recording_started_at": 1000, "sdk_version": "1.8.8",
           "job_id": "unit-private-job", "room_id": "unit-private-room",
           "chat_history": {"items": [
               {"type": "agent_config_update", "instructions": instructions},
               {"type": "message", "role": "user", "content": ["I need help next week."], "created_at": 1001},
               {"type": "message", "role": "assistant", "content": ["Hello Sarah, this is Michael Lopez. March 14, 1973."], "created_at": 1002},
           ]}, "events": [
               {"type": "tool_execution_updated", "created_at": 1003, "update": {"type": "tool_call_started",
                "function_call": {"name": "end_call", "call_id": "unit-private-call", "arguments": "unit-private-payload"}}},
               {"type": "tool_execution_updated", "created_at": 1003.1, "update": {"type": "tool_call_ended",
                "call_id": "unit-private-call", "status": "done", "message": "unit-private-output"}},
           ]}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    kwargs = {"imported_at": "2026-01-01T00:00:00+00:00"}  # explicit unit provenance, not source-clock evidence
    a = IMPORTER["project"](path, IMPORTER["APPROVED"][0], **kwargs)
    b = IMPORTER["project"](path, IMPORTER["APPROVED"][0], **kwargs)
    assert a == b and path.read_bytes() == before
    assert a.turns[1].text == "Hello Taylor, this is Alex Morgan. REDACTED."
    assert not any(f.detector == "stage_direction_spoken" for f in run_detectors(a))
    assert "unit-private" not in a.model_dump_json()


def test_import_command_refuses_existing_targets_before_reading_sources(tmp_path, monkeypatch):
    main = IMPORTER["main"]
    monkeypatch.setitem(main.__globals__, "DEFAULT_DATA_DIR", tmp_path)
    target = tmp_path / f"{NEW_IDS[0]}.json"
    target.write_text("existing work")
    monkeypatch.setattr("sys.argv", ["import", str(tmp_path / "missing-source")])
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        main()
    assert target.read_text() == "existing work"
