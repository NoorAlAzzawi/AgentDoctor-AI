"""Smoke tests: ingest + sanitization, API endpoints, demo data, and the API contract examples."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentdoctor.ingest import PrivacyError, load_livekit_report, privacy_scan
from agentdoctor.models import Comparison, FindingsResponse, Session, SessionSummary
from agentdoctor.store import DEFAULT_DATA_DIR

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_session" / "session_report.json"
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "examples"


@pytest.fixture
def synthetic() -> Session:
    return load_livekit_report(FIXTURE, session_id="synthetic-1", title="Synthetic", synthetic=True)


def test_ingest_normalizes_turns_and_times(synthetic):
    assert [t.speaker for t in synthetic.turns] == ["user", "agent", "user", "agent"]
    assert [t.id for t in synthetic.turns] == ["t001", "t002", "t003", "t004"]
    first = synthetic.turns[0]
    assert (first.start_s, first.end_s, first.committed_s) == (1.0, 2.5, 3.0)
    assert synthetic.turns[1].metrics.e2e_latency_s == 1.2
    assert synthetic.turns[3].interrupted is True
    assert synthetic.models.stt == "assemblyai/universal-3-6-pro"
    assert synthetic.pipeline_label == "workshop"
    assert synthetic.agent_instructions == "You are a fictional test patient."


def test_ingest_keeps_only_allowlisted_events_in_time_order(synthetic):
    types = [e.type for e in synthetic.events]
    assert types == ["user_state", "agent_state", "false_interruption", "overlapping_speech", "session_close"]
    assert [e.t_s for e in synthetic.events] == sorted(e.t_s for e in synthetic.events)


def test_stats_are_computed_from_reported_values(synthetic):
    s = synthetic.stats
    assert (s.user_turns, s.agent_turns) == (2, 2)
    assert s.e2e_latency.n == 2 and s.e2e_latency.median_s == 2.1
    assert s.endpointing_timeouts == 1  # end_of_turn_delay hit max_endpointing_delay (2.5s)
    assert (s.false_interruptions, s.interrupted_agent_turns, s.overlapping_speech_events) == (1, 1, 1)
    assert s.duration_s == 15.0


def test_sanitized_output_drops_paths_ids_and_request_ids(synthetic):
    dumped = synthetic.model_dump_json()
    for secret in ("/Users/someone", "AJ_synthetic", "RM_synthetic", "secret-request-id", "audio.ogg"):
        assert secret not in dumped


def test_privacy_scan_blocks_contact_details_but_allows_fictional_555_numbers():
    assert privacy_scan(["call me at 805-555-0142"]) == []
    assert privacy_scan(["email jane@example.com"])
    assert privacy_scan(["see https://example.com"])
    assert privacy_scan(["my number is 415-867-5309"])
    assert privacy_scan(["for the Acme Health demo"], blocked_terms=["acme health"])


def test_ingest_refuses_reports_with_private_data(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw["chat_history"]["items"][1]["content"] = ["Reach me at jane@example.com"]
    report = tmp_path / "s" / "session_report.json"
    report.parent.mkdir()
    report.write_text(json.dumps(raw))
    with pytest.raises(PrivacyError):
        load_livekit_report(report, session_id="x", title="x")


@pytest.fixture
def client(tmp_path, monkeypatch, synthetic):
    monkeypatch.setenv("AGENTDOCTOR_DATA_DIR", str(tmp_path))
    (tmp_path / "synthetic-1.json").write_text(synthetic.model_dump_json())
    other = synthetic.model_copy(update={"id": "synthetic-2"})
    (tmp_path / "synthetic-2.json").write_text(other.model_dump_json())
    from agentdoctor.api import app

    return TestClient(app)


def test_api_endpoints(client):
    assert client.get("/api/health").json()["status"] == "ok"

    sessions = client.get("/api/sessions").json()
    assert [s["id"] for s in sessions] == ["synthetic-1", "synthetic-2"]
    assert "turns" not in sessions[0]  # list returns summaries only

    detail = client.get("/api/sessions/synthetic-1").json()
    assert len(detail["turns"]) == 4 and len(detail["events"]) == 5

    findings = FindingsResponse.model_validate(client.get("/api/sessions/synthetic-1/findings").json())
    assert findings.session_id == "synthetic-1"

    cmp = Comparison.model_validate(client.get("/api/compare?a=synthetic-1&b=synthetic-2").json())
    assert all(cmp.same_models.values())
    assert any("synthetic" in c for c in cmp.caveats)


def test_api_rejects_unknown_and_malformed_ids(client):
    assert client.get("/api/sessions/does-not-exist").status_code == 404
    assert client.get("/api/sessions/..%2F..%2Fpyproject").status_code == 404


def test_demo_data_is_valid_and_sanitized():
    files = sorted(DEFAULT_DATA_DIR.glob("*.json"))
    assert len(files) == 7
    for path in files:
        text = path.read_text()
        session = Session.model_validate_json(text)
        for forbidden in ("/Users/", "audio_recording_path", "provider_request_ids"):
            assert forbidden not in text, f"{path.name} contains {forbidden!r}"
        texts = [t.text for t in session.turns] + [session.agent_instructions or "", session.notes or ""]
        assert privacy_scan(texts) == [], path.name


def test_contract_examples_match_schemas():
    schemas = {
        "sessions.json": lambda d: [SessionSummary.model_validate(x) for x in d],
        "session.json": Session.model_validate,
        "findings.json": FindingsResponse.model_validate,
        "findings.illustrative.json": FindingsResponse.model_validate,
        "compare.json": Comparison.model_validate,
    }
    for name, validate in schemas.items():
        validate(json.loads((CONTRACTS / name).read_text()))
    by_session = sorted((CONTRACTS / "by-session").glob("*.json"))
    assert len(by_session) == 14
    for path in by_session:
        model = Session if path.name.endswith(".session.json") else FindingsResponse
        model.model_validate_json(path.read_text())
