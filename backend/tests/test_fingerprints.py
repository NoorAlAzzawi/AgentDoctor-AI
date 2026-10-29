"""Recorded recurrence is an observation, not a shared cause or independent trial."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentdoctor.api import app
from agentdoctor.detectors import run_detectors
from agentdoctor.fingerprints import build_fingerprints
from agentdoctor.ingest import privacy_scan
from agentdoctor.models import FingerprintsResponse, Session
from agentdoctor.store import DEFAULT_DATA_DIR


@pytest.fixture
def recorded():
    return [Session.model_validate_json(p.read_text()) for p in sorted(DEFAULT_DATA_DIR.glob("*.json"))]


def test_recorded_fingerprint_counts_ids_and_distributions(recorded):
    r = build_fingerprints(recorded)
    expected = {
        "fp-v1-confusion-without-correction": (3, 2, 0, 3),
        "fp-v1-endpointing-at-limit": (6, 6, 6, 0),
        "fp-v1-interruption-before-reply": (3, 2, 3, 0),
        "fp-v1-interruption-mid-reply": (8, 4, 8, 0),
        "fp-v1-premature-goodbye": (3, 3, 3, 0),
        "fp-v1-reply-to-fragment": (5, 2, 0, 5),
    }
    assert r.recorded_session_count == 7 and r.finding_count == 34
    assert r.grouped_finding_count == 28 and len(r.ungrouped) == 6
    assert {f.id: (f.occurrence_count, f.affected_session_count,
                   f.status_counts["confirmed"], f.status_counts["suspected"])
            for f in r.fingerprints} == expected
    for f in r.fingerprints:
        assert sum(f.severity_counts.values()) == sum(f.status_counts.values()) == f.occurrence_count
        assert f.affected_session_count == len(f.session_ids) >= 2
    mid = next(f for f in r.fingerprints if f.id == "fp-v1-interruption-mid-reply")
    assert mid.severity_counts == {"high": 4, "medium": 4, "low": 0}
    assert mid.pipelines == ["hybrid", "workshop"]


def test_order_independent_repeatable_grouping(recorded):
    fs = {s.id: run_detectors(s) for s in recorded}
    first = build_fingerprints(recorded, fs)
    reversed_findings = {sid: list(reversed(items)) for sid, items in fs.items()}
    assert first == build_fingerprints(list(reversed(recorded)), reversed_findings)
    assert first == build_fingerprints(recorded, fs)


def test_ids_do_not_depend_on_population_or_providers(recorded):
    first = build_fingerprints(recorded)
    subset = [s.model_copy(deep=True) for s in recorded[:2]]
    for s in subset:
        s.models.llm = None
        s.pipeline_label = None
    later = build_fingerprints(subset)
    assert {f.id for f in later.fingerprints} <= {f.id for f in first.fingerprints}
    assert "fp-v1-interruption-mid-reply" in {f.id for f in later.fingerprints}
    assert all(f.pipelines == [None] for f in later.fingerprints)


def test_root_cause_prose_does_not_affect_grouping(recorded):
    fs = {s.id: run_detectors(s) for s in recorded}
    first = build_fingerprints(recorded, fs)
    for items in fs.values():
        for f in items:
            f.root_cause.statement = "All causes are unknown; this arbitrary prose is not a grouping input."
            f.root_cause.confidence = "possible"
    assert first == build_fingerprints(recorded, fs)


def test_no_input_mutation_or_output_aliasing(recorded):
    fs = {s.id: run_detectors(s) for s in recorded}
    before_s = [s.model_dump_json() for s in recorded]
    before_f = {sid: [f.model_dump_json() for f in items] for sid, items in fs.items()}
    r = build_fingerprints(recorded, fs)
    r.fingerprints[0].occurrences[0].evidence[0].value = "changed output only"
    r.fingerprints[0].occurrences[0].models.stt = "changed output only"
    assert [s.model_dump_json() for s in recorded] == before_s
    assert {sid: [f.model_dump_json() for f in items] for sid, items in fs.items()} == before_f


@pytest.mark.parametrize("flag", ["session", "source"])
def test_either_synthetic_flag_excludes_input(recorded, flag):
    fake = recorded[0].model_copy(deep=True)
    fake.id = "synthetic-test-copy"
    if flag == "session":
        fake.synthetic = True
    else:
        fake.source.synthetic = True
    result = build_fingerprints([*recorded, fake])
    baseline = build_fingerprints(recorded)
    assert result.excluded_synthetic_session_count == 1
    assert result.fingerprints == baseline.fingerprints
    assert result.ungrouped == baseline.ungrouped
    assert result.recorded_session_count == 7 and result.finding_count == 34


def test_fault_injection_fixtures_do_not_enter_recorded_recurrence(recorded):
    directory = Path(__file__).parent / "fixtures" / "synthetic_faults"
    synthetic = [Session.model_validate(json.loads(p.read_text())["session"])
                 for p in sorted(directory.glob("*.json"))]
    response = build_fingerprints([*recorded, *synthetic])
    assert response.excluded_synthetic_session_count == 3
    assert response.fingerprints == build_fingerprints(recorded).fingerprints
    assert all(o.session_id in {s.id for s in recorded} for f in response.fingerprints for o in f.occurrences)


def test_single_session_is_not_cross_session_recurrence(recorded):
    response = build_fingerprints([recorded[0]])
    assert response.fingerprints == [] and response.grouped_finding_count == 0
    assert len(response.ungrouped) == len(run_detectors(recorded[0]))


def test_insufficient_evidence_and_unknown_detectors_are_not_grouped(recorded):
    fs = {s.id: run_detectors(s) for s in recorded}
    for items in fs.values():
        for f in items:
            f.evidence = []
    response = build_fingerprints(recorded, fs)
    assert response.fingerprints == []
    assert any(f.reason == "insufficient_evidence" for f in response.ungrouped)
    for items in fs.values():
        for f in items:
            f.detector = "unsupported_new_detector"
    response = build_fingerprints(recorded, fs)
    assert response.fingerprints == []
    assert all(f.reason == "no_rule" for f in response.ungrouped)


def test_false_interruption_subtypes_require_actual_linked_events(recorded):
    fs = {s.id: run_detectors(s) for s in recorded}
    for items in fs.values():
        for f in items:
            if f.detector == "false_interruption_pause":
                f.evidence = [e for e in f.evidence if e.kind != "event"]
    response = build_fingerprints(recorded, fs)
    assert not any(f.detector == "false_interruption_pause" for f in response.fingerprints)
    assert len([f for f in response.ungrouped if f.detector == "false_interruption_pause"]) == 11


def test_exact_traceability_and_accounting(recorded):
    sessions = {s.id: s for s in recorded}
    fs = {s.id: {f.id: f for f in run_detectors(s)} for s in recorded}
    response = build_fingerprints(recorded)
    seen = set()
    for fingerprint in response.fingerprints:
        assert fingerprint.session_ids == sorted({o.session_id for o in fingerprint.occurrences})
        for o in fingerprint.occurrences:
            original = fs[o.session_id][o.finding_id]
            assert (o.session_id, o.finding_id) not in seen
            seen.add((o.session_id, o.finding_id))
            assert o.evidence == original.evidence
            assert o.models == sessions[o.session_id].models
            assert o.config == sessions[o.session_id].config
            assert (o.severity, o.status) == (original.severity, original.status)
    for f in response.ungrouped:
        assert (f.session_id, f.finding_id) not in seen
        seen.add((f.session_id, f.finding_id))
    assert seen == {(sid, fid) for sid, items in fs.items() for fid in items}
    assert {f.reason for f in response.ungrouped if f.detector == "stt_missing_time_number"} == {"single_session"}
    assert {f.reason for f in response.ungrouped if f.detector in {"stage_direction_spoken", "talked_after_farewell"}} == {"no_rule"}


def test_privacy_and_caveats(recorded):
    response = build_fingerprints(recorded)
    text = response.model_dump_json()
    assert privacy_scan([text]) == []
    for forbidden in ("/Users/", "provider_request_ids", "audio_recording_path", "api_key"):
        assert forbidden not in text
    assert any("not fully independent" in caveat for caveat in response.caveats)
    assert all("does not prove a shared root cause" in f.caveat for f in response.fingerprints)


def test_api_matches_builder_and_saved_contract(recorded, monkeypatch):
    monkeypatch.delenv("AGENTDOCTOR_DATA_DIR", raising=False)
    with TestClient(app) as client:
        r = client.get("/api/fingerprints", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    response = FingerprintsResponse.model_validate(r.json())
    assert response == build_fingerprints(recorded)
    example = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "fingerprints.json"
    assert response == FingerprintsResponse.model_validate_json(example.read_text())


def test_api_excludes_synthetic_store_entries(recorded, monkeypatch, tmp_path):
    synthetic = recorded[0].model_copy(deep=True)
    synthetic.synthetic = True
    (tmp_path / f"{synthetic.id}.json").write_text(synthetic.model_dump_json())
    monkeypatch.setenv("AGENTDOCTOR_DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        response = FingerprintsResponse.model_validate(client.get("/api/fingerprints").json())
    assert response.recorded_session_count == response.finding_count == 0
    assert response.excluded_synthetic_session_count == 1
    assert response.fingerprints == response.ungrouped == []


def test_duplicate_ids_are_rejected(recorded):
    with pytest.raises(ValueError, match="Session IDs"):
        build_fingerprints([recorded[0], recorded[0]])
    fs = {s.id: run_detectors(s) for s in recorded}
    fs[recorded[0].id].append(fs[recorded[0].id][0])
    with pytest.raises(ValueError, match="Finding IDs"):
        build_fingerprints(recorded, fs)
