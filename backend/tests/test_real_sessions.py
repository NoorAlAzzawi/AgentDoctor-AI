"""Detectors on the 7 real, sanitized demo sessions.

Expected results were verified manually against the transcripts and event timelines.
"""

from collections import Counter

import pytest

from agentdoctor.detectors import run_detectors
from agentdoctor.store import list_sessions, load_session

SESSIONS = {s.id: load_session(s.id) for s in list_sessions()}
FINDINGS = {sid: run_detectors(s) for sid, s in SESSIONS.items()}


def by_detector(sid: str) -> Counter:
    return Counter(f.detector for f in FINDINGS[sid])


@pytest.mark.parametrize("sid", sorted(SESSIONS))
def test_every_finding_is_traceable_to_real_evidence(sid):
    session = SESSIONS[sid]
    turn_ids = {t.id for t in session.turns}
    event_ids = {e.id for e in session.events}
    ids = [f.id for f in FINDINGS[sid]]
    assert len(ids) == len(set(ids)), "finding ids must be unique"
    for f in FINDINGS[sid]:
        assert f.evidence, f.id
        assert set(f.turn_ids) <= turn_ids, f.id
        for e in f.evidence:
            assert e.turn_id is None or e.turn_id in turn_ids, f.id
            assert e.event_id is None or e.event_id in event_ids, f.id
        if f.time_range:
            assert f.time_range.start_s <= f.time_range.end_s, f.id
        if f.category == "speech_recognition":
            assert f.status == "suspected", "STT errors can't be confirmed without audio"
        if f.root_cause.confidence != "confirmed":
            assert f.root_cause.missing_evidence, f.id


def test_false_interruptions_match_session_events():
    for sid, s in SESSIONS.items():
        assert by_detector(sid)["false_interruption_pause"] == s.stats.false_interruptions


def test_s03_mid_reply_pauses():
    pauses = [f.title for f in FINDINGS["s03-workshop-ab-script"] if f.detector == "false_interruption_pause"]
    assert pauses == ["Agent paused mid-reply for 2.21s, then resumed",
                      "Agent paused mid-reply for 2.31s, then resumed"]


def test_s02_date_of_birth_reply_paused_twice_and_user_missed_it():
    high = [f for f in FINDINGS["s02-workshop-structured"]
            if f.detector == "false_interruption_pause" and f.severity == "high"]
    assert any("March 14, 1973" in e.value for f in high for e in f.evidence)
    assert any("did not hear" in e.value for f in high for e in f.evidence)


def test_s05_holds_happen_before_speaking_not_mid_reply():
    titles = [f.title for f in FINDINGS["s05-workshop-headphones"] if f.detector == "false_interruption_pause"]
    assert titles and all(t.startswith("Agent held its reply") for t in titles)


def test_s04_missing_time_numbers():
    hits = [f for f in FINDINGS["s04-recommended-ab-script"] if f.detector == "stt_missing_time_number"]
    assert len(hits) == 3
    assert all(f.status == "suspected" for f in hits)
    others = [sid for sid in SESSIONS if sid != "s04-recommended-ab-script" and by_detector(sid)["stt_missing_time_number"]]
    assert others == []


def test_s01_patrick_swayze_misrecognition_is_likely():
    [f] = [f for f in FINDINGS["s01-workshop-improvised"]
           if f.detector == "stt_misunderstanding" and "Swayze" in f.title]
    assert f.root_cause.confidence == "likely"
    assert any("when is your appointment" in e.value for e in f.evidence)


def test_premature_goodbyes():
    flagged = {sid for sid in SESSIONS if by_detector(sid)["premature_goodbye"]}
    assert flagged == {"s02-workshop-structured", "s04-recommended-ab-script", "s05-workshop-headphones"}


def test_s01_stage_direction_and_talking_after_farewell():
    counts = by_detector("s01-workshop-improvised")
    assert counts["stage_direction_spoken"] == 1 and counts["talked_after_farewell"] == 1
    assert sum(by_detector(sid)["stage_direction_spoken"] for sid in SESSIONS) == 1


def test_persona_v2_session_is_clean_of_llm_and_interruption_failures():
    counts = by_detector("s07-workshop-persona-v2")
    for detector in ("premature_goodbye", "stage_direction_spoken", "talked_after_farewell",
                     "false_interruption_pause"):
        assert counts[detector] == 0


def test_no_false_positives_for_state_detectors():
    for sid in SESSIONS:
        assert by_detector(sid)["contradicts_instructions"] == 0
        assert by_detector(sid)["readback_mismatch"] == 0
