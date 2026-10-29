"""Test-only LiveKit 1.8.8 lifecycle shapes; never imported into the session store."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentdoctor.detectors import run_detectors
from agentdoctor.ingest import PrivacyError, load_livekit_report
from agentdoctor.models import Session, TimelineEvent
from agentdoctor.store import DEFAULT_DATA_DIR


def start(call_id="raw-call-A", at=1010.0, name="end_call"):
    return {
        "type": "tool_execution_updated", "created_at": at,
        "update": {"type": "tool_call_started", "function_call": {
            "id": "raw-item-private", "type": "function_call", "call_id": call_id,
            "name": name, "arguments": '{"secret":"private-argument"}',
            "created_at": 1008.0, "group_id": "raw-group-private",
            "extra": {"livekit": {"inference_deployment": "private-deployment"}},
        }},
    }


def end(call_id="raw-call-A", at=1010.5, status="done"):
    return {
        "type": "tool_execution_updated", "created_at": at,
        "update": {"type": "tool_call_ended", "id": "raw-terminal-private",
                   "call_id": call_id, "status": status, "message": "private-error-or-result"},
    }


def load(tmp_path, events, items=(), origin=1000.0, blocked_terms=()):
    path = tmp_path / "synthetic-tool-lifecycle" / "session_report.json"
    path.parent.mkdir(exist_ok=True)
    raw = {
        "sdk_version": "1.8.8", "audio_recording_started_at": origin,
        "job_id": "private-job", "room_id": "private-room", "room": {"private": "metadata"},
        "audio_recording_path": "/private/synthetic-audio.ogg",
        "chat_history": {"items": list(items)}, "events": events,
    }
    path.write_text(json.dumps(raw))
    return load_livekit_report(path, session_id="synthetic-tools", title="Synthetic tools", synthetic=True,
                               blocked_terms=blocked_terms)


def tools(session):
    return [e for e in session.events if e.type == "tool_execution"]


def test_successful_dispatch_and_completion_are_valid_linkable_events(tmp_path):
    session = load(tmp_path, [start(), end()])
    assert [e.model_dump() for e in tools(session)] == [
        {"id": "e001", "t_s": 10.0, "type": "tool_execution",
         "data": {"tool_name": "end_call", "call_ref": "tc001", "phase": "started"}},
        {"id": "e002", "t_s": 10.5, "type": "tool_execution",
         "data": {"tool_name": "end_call", "call_ref": "tc001", "phase": "ended", "status": "done"}},
    ]
    assert Session.model_validate_json(session.model_dump_json()) == session
    assert run_detectors(session) == []  # no premature_end_call detector is added


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_terminal_status_is_preserved_without_error_text(tmp_path, status):
    session = load(tmp_path, [start(), end(status=status)])
    assert tools(session)[1].data["status"] == status
    assert "private-error-or-result" not in session.model_dump_json()


def test_distinct_parallel_calls_are_not_merged_or_duplicated(tmp_path):
    first, second = start(), start("raw-call-B", 1010.1)
    batch = {"type": "function_tools_executed", "created_at": 1020.0,
             "function_calls": [first["update"]["function_call"], second["update"]["function_call"]],
             "function_call_outputs": [{"call_id": "raw-call-A", "output": "private-output"}]}
    session = load(tmp_path, [first, second, end(), end("raw-call-B", 1010.6), batch],
                   items=batch["function_calls"])
    assert len(tools(session)) == 4
    assert [e.data["call_ref"] for e in tools(session)] == ["tc001", "tc002", "tc001", "tc002"]
    assert [e.t_s for e in tools(session)] == [10.0, 10.1, 10.5, 10.6]
    assert all(e.data["tool_name"] == "end_call" for e in tools(session))


@pytest.mark.parametrize("source", ["chat_request", "batch", "session_close"])
def test_non_lifecycle_sources_do_not_establish_dispatch(tmp_path, source):
    items = [start()["update"]["function_call"]] if source == "chat_request" else []
    events = []
    if source == "batch":
        events = [{"type": "function_tools_executed", "created_at": 1010.0,
                   "function_calls": [start()["update"]["function_call"]],
                   "function_call_outputs": [{"name": "end_call", "is_error": False}]}]
    elif source == "session_close":
        events = [{"type": "close", "created_at": 1011.0, "reason": "user_initiated", "error": None}]
    assert tools(load(tmp_path, events, items)) == []


def test_actual_lifecycle_times_do_not_use_request_output_batch_or_speech_times(tmp_path):
    call = start()["update"]["function_call"]
    output = {"type": "function_call_output", "call_id": "raw-call-A", "name": "end_call",
              "created_at": 1009.0, "output": "private-output", "is_error": False}
    message = {"type": "message", "role": "assistant", "content": ["Goodbye."],
               "created_at": 1003.2, "metrics": {"started_speaking_at": 1004.0, "stopped_speaking_at": 1006.0}}
    events = [start(), end(), {"type": "function_tools_executed", "created_at": 1020.0},
              {"type": "close", "created_at": 1021.0, "reason": "user_initiated", "error": None}]
    s = load(tmp_path, events, [message, call, output])
    assert [e.t_s for e in tools(s)] == [10.0, 10.5]
    assert (s.turns[0].committed_s, s.turns[0].start_s, s.turns[0].end_s) == (3.2, 4.0, 6.0)
    assert s.events[-1].t_s == s.stats.duration_s == 21.0


def test_deterministic_refs_ignore_raw_id_values_and_join_unsorted_events(tmp_path):
    events = [end("raw-call-B", 1012.0), start("raw-call-B", 1011.0), end(), start()]
    before = copy.deepcopy(events)
    first = load(tmp_path, events).events
    assert first == load(tmp_path, events).events
    assert events == before
    renamed = json.loads(json.dumps(events).replace("raw-call-A", "different-private-ID-1")
                         .replace("raw-call-B", "different-private-ID-2"))
    assert first == load(tmp_path, renamed).events
    assert [e.data["call_ref"] for e in first] == ["tc001", "tc001", "tc002", "tc002"]


def test_tied_dispatch_times_have_stable_source_order(tmp_path):
    s = load(tmp_path, [start("B"), start("A"), end("A"), end("B")])
    assert [e.data["call_ref"] for e in s.events] == ["tc001", "tc002", "tc002", "tc001"]


def test_zero_recording_origin_is_not_replaced_with_first_event_time(tmp_path):
    s = load(tmp_path, [start(at=10.0), end(at=11.0)], origin=0.0)
    assert [e.t_s for e in tools(s)] == [10.0, 11.0]


def test_dispatch_without_terminal_remains_only_dispatched(tmp_path):
    s = load(tmp_path, [start(), {"type": "close", "created_at": 1011.0, "reason": "user_initiated"}])
    assert len(tools(s)) == 1
    assert tools(s)[0].data["phase"] == "started"
    assert "status" not in tools(s)[0].data


def test_duplicate_identical_lifecycle_exports_do_not_add_calls(tmp_path):
    s = load(tmp_path, [start(), copy.deepcopy(start()), end(), copy.deepcopy(end())])
    assert len(tools(s)) == 2


def test_allowlist_drops_all_nonessential_tool_payloads(tmp_path):
    events = [start(), end(), {"type": "tool_execution_updated", "created_at": 1010.2,
              "update": {"type": "tool_call_updated", "message": "private-progress"}},
              {"type": "tool_execution_updated", "created_at": 1010.3,
              "update": {"type": "tool_reply_updated", "speech_id": "private-speech", "status": "interrupted"}}]
    call = start()["update"]["function_call"]
    output = {"type": "function_call_output", "call_id": "raw-call-A", "created_at": 1009.0,
              "output": "private-output", "is_error": True}
    dumped = load(tmp_path, events, [call, output]).model_dump_json()
    for private in ("raw-call-A", "raw-item-private", "raw-group-private", "raw-terminal-private",
                    "private-argument", "private-deployment", "private-error-or-result", "private-output",
                    "private-progress", "private-speech", "private-job", "private-room", "synthetic-audio.ogg",
                    "inference_deployment", "arguments", "reply_required", "group_id", "1008.0", "1009.0"):
        assert private not in dumped


def test_tool_names_are_subject_to_existing_privacy_blocklist(tmp_path):
    with pytest.raises(PrivacyError, match="tool names failed privacy validation"):
        load(tmp_path, [start(name="private_org_tool"), end()], blocked_terms=["private_org"])


@pytest.mark.parametrize("events", [
    [end()],
    [start(), end(at=1009.0)],
    [start(), end(status="unknown")],
    [start(at=float("nan"))],
    [start(at=float("inf"))],
    [start(at=None)],
    [start(name="private address@example.com")],
    [start(), start(name="other_tool")],
    [start(), end(), end(status="cancelled")],
    [{"type": "tool_execution_updated", "created_at": 1010.0,
      "update": {"type": "tool_call_started", "function_call": {"name": "end_call"}}}],
])
def test_ambiguous_or_invalid_lifecycle_metadata_fails_closed(tmp_path, events):
    with pytest.raises(ValueError) as exc:
        load(tmp_path, events)
    assert "raw-call" not in str(exc.value)
    assert "address@example.com" not in str(exc.value)


@pytest.mark.parametrize("data", [
    {"phase": "ended"},
    {"phase": "started", "status": "done"},
    {"phase": "ended", "status": "unknown"},
    {"phase": "started", "call_ref": "raw-provider-ID"},
    {"phase": "started", "arguments": "private"},
])
def test_normalized_tool_event_shape_is_validated(data):
    with pytest.raises(ValidationError):
        TimelineEvent(id="e001", t_s=1.0, type="tool_execution",
                      data={"tool_name": "end_call", "call_ref": "tc001", **data})


def test_existing_seven_sessions_and_findings_remain_compatible():
    examples = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "by-session"
    files = sorted(DEFAULT_DATA_DIR.glob("*.json"))
    assert len(files) == 7
    for p in files:
        raw = json.loads(p.read_text())
        s = Session.model_validate(raw)
        assert [e.model_dump() for e in s.events] == raw["events"]
        assert tools(s) == []
        expected = json.loads((examples / f"{s.id}.findings.json").read_text())["findings"]
        assert [f.model_dump(mode="json") for f in run_detectors(s)] == expected
