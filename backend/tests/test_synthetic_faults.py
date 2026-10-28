"""Known, test-only text mutations -> expected findings, with matched clean controls.

The oracle lives outside Session. Production detectors receive only the faulty
session and retain their normal evidence/confidence semantics.
"""

import json
import random
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentdoctor.detectors import run_detectors
from agentdoctor.detectors.llm_behavior import premature_goodbye
from agentdoctor.detectors.speech_recognition import stt_missing_time_number
from agentdoctor.detectors.state import readback_mismatch
from agentdoctor.ingest import privacy_scan
from agentdoctor.models import Session

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_faults"
CASES = [
    ("stt_missing_time_number", stt_missing_time_number, "speech_recognition", "suspected", "likely"),
    ("readback_mismatch", readback_mismatch, "conversation_state", "confirmed", "possible"),
    ("premature_goodbye", premature_goodbye, "llm_behavior", "confirmed", "possible"),
]


@pytest.fixture(params=CASES, ids=[case[0] for case in CASES])
def fault_case(request):
    name, detector, category, status, confidence = request.param
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    session = Session.model_validate(raw["session"])
    return raw, session, detector, category, status, confidence


def test_fixture_validates_and_declares_synthetic_provenance(fault_case):
    raw, session, detector, category, status, _ = fault_case
    assert session.synthetic is True and session.source.synthetic is True
    assert session.id == session.source.original_name == raw["fixture_id"]
    assert session.id.startswith("synthetic-fault-")
    assert session.title.startswith("SYNTHETIC FAULT FIXTURE")
    assert raw["description"] and raw["injected_fault_type"]
    assert raw["expected"]["detector"] == detector.__name__
    assert (raw["expected"]["category"], raw["expected"]["status"]) == (category, status)
    assert [t.index for t in session.turns] == list(range(len(session.turns)))
    assert len({t.id for t in session.turns}) == len(session.turns)
    assert session.stats.user_turns == sum(t.speaker == "user" for t in session.turns)
    assert session.stats.agent_turns == sum(t.speaker == "agent" for t in session.turns)
    assert all(t.transcript_confidence is None for t in session.turns)
    assert all(v is None for t in session.turns for v in t.metrics.model_dump().values())
    assert all(v is None for v in session.models.model_dump().values())
    assert session.stats.duration_s is None
    assert session.events == session.usage == []


def test_known_fault_produces_only_the_expected_traceable_finding(fault_case):
    raw, session, detector, category, status, confidence = fault_case
    [finding] = detector(session)
    assert run_detectors(session) == [finding], "The full registry must find only the injected fault"
    assert finding.detector == raw["expected"]["detector"]
    assert finding.session_id == session.id
    assert (finding.category, finding.status) == (category, status)
    assert finding.root_cause.confidence == confidence
    assert finding.root_cause.missing_evidence, "Known injection must not erase detector uncertainty"
    assert set(finding.turn_ids) == set(raw["expected"]["evidence_turn_ids"])
    turns = {t.id: t for t in session.turns}
    transcript = [e for e in finding.evidence if e.kind == "transcript"]
    for turn_id in raw["expected"]["evidence_turn_ids"]:
        assert any(e.turn_id == turn_id and e.value == turns[turn_id].text
                   and e.t_s == turns[turn_id].start_s for e in transcript)
    injected_id = raw["injection"]["turn_id"]
    assert turns[injected_id].text == raw["injection"]["injected_text"]
    assert any(e.turn_id == injected_id and e.value == raw["injection"]["injected_text"]
               for e in transcript)
    for e in finding.evidence:
        assert e.turn_id is None or e.turn_id in turns
        assert e.event_id is None  # these text-only fixtures contain no events

    if detector is stt_missing_time_number:
        assert raw["injection"]["baseline_text"] == "Thursday at 8 AM."
        assert raw["injection"]["injected_text"] == "Thursday at AM."
        assert any(e.turn_id == "t001" and e.value.rstrip(".").lower() == "at am" for e in transcript)
        assert not any(e.value == raw["injection"]["baseline_text"] for e in finding.evidence)
        assert any("reference transcript" in gap for gap in finding.root_cause.missing_evidence)
    elif detector is readback_mismatch:
        assert "Tuesday" in turns["t001"].text
        assert "Thursday" in turns["t003"].text
        assert "repeat the appointment" in turns["t005"].text
        assert "Tuesday" in turns["t006"].text
        assert all("8 AM" in turns[tid].text for tid in ("t001", "t003", "t006"))
    else:
        assert "directions" in turns["t001"].text and "?" in turns["t001"].text
        assert turns["t002"].text == "Goodbye."
        assert "agreement" not in finding.root_cause.statement.lower()
        assert "may" in finding.root_cause.statement.lower()
        assert "instructions mention" not in finding.root_cause.statement.lower()
        assert "main entrance" in turns["t003"].text
        assert len(turns["t003"].text.split()) >= 3


def test_single_declared_text_mutation_separates_fault_from_clean_control(fault_case):
    raw, faulty, detector, *_ = fault_case
    injection = raw["injection"]
    assert injection["baseline_text"] != injection["injected_text"]
    control = faulty.model_copy(deep=True)
    target = next(t for t in control.turns if t.id == injection["turn_id"])
    target.text = injection["baseline_text"]
    control = Session.model_validate(control.model_dump())
    assert detector(control) == []
    assert run_detectors(control) == [], "The matched clean control must not trigger any detector"
    reinjected = control.model_copy(deep=True)
    next(t for t in reinjected.turns if t.id == injection["turn_id"]).text = injection["injected_text"]
    assert reinjected == faulty, "The declared edit must be the only difference"
    assert run_detectors(reinjected) == run_detectors(faulty)


def test_detection_is_repeatable_offline_and_does_not_mutate_input(fault_case, monkeypatch):
    _, session, _, *_ = fault_case
    before = session.model_dump_json()

    def forbidden(*args, **kwargs):
        raise AssertionError("Controlled detection must not use network access or randomness")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(random.Random, "random", forbidden)
    monkeypatch.setattr(random.Random, "getrandbits", forbidden)
    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(random, "getrandbits", forbidden)
    first = run_detectors(session)
    assert first
    assert run_detectors(session) == run_detectors(Session.model_validate_json(before)) == first
    assert session.model_dump_json() == before


def test_fault_fixtures_remain_outside_the_default_store_and_api(monkeypatch):
    from agentdoctor.api import app
    from agentdoctor.store import DEFAULT_DATA_DIR, list_sessions, load_session

    monkeypatch.delenv("AGENTDOCTOR_DATA_DIR", raising=False)
    recorded = list_sessions()
    assert len(recorded) == 7
    assert all(not s.synthetic for s in recorded)
    assert not FIXTURES.is_relative_to(DEFAULT_DATA_DIR)
    fixture_ids = {json.loads(p.read_text())["fixture_id"] for p in FIXTURES.glob("*.json")}
    assert len(fixture_ids) == 3
    assert fixture_ids.isdisjoint(s.id for s in recorded)
    with TestClient(app) as client:
        response = client.get("/api/sessions")
        assert response.status_code == 200
        assert {s["id"] for s in response.json()} == {s.id for s in recorded}
        for fixture_id in fixture_ids:
            assert load_session(fixture_id) is None
            assert client.get(f"/api/sessions/{fixture_id}").status_code == 404
            assert client.get(f"/api/sessions/{fixture_id}/findings").status_code == 404


@pytest.mark.parametrize("name", [case[0] for case in CASES])
def test_fixture_content_passes_privacy_scan(name):
    text = (FIXTURES / f"{name}.json").read_text()
    assert privacy_scan([text]) == []
    for forbidden in ("/Users/", "/private/", "api_key", "audio_recording_path", "provider_request_ids"):
        assert forbidden not in text
