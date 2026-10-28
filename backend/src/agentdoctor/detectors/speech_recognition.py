"""Suspicious speech-to-text output. Without audio or a reference transcript these are never
confirmed: the status is always "suspected"."""

import re

from ..models import Evidence, Finding, RootCause, Session, TimeRange
from ._util import finding_id, next_turn, quote, turn_time

# A time of day with no number, e.g. "Thursday at AM".
MISSING_TIME_NUMBER = re.compile(r"\b(at|by|around|for|after|before)\s+(a\.?m\.?|p\.?m\.?)(?=\W|$)", re.I)
# The agent signals it could not make sense of what it heard.
AGENT_CONFUSED = re.compile(
    r"(i'm sorry, what|sorry, what\?|not sure what you mean|i (think i )?missed (something|that)|"
    r"didn't catch|who is [^?]+\?|what do you mean)",
    re.I,
)
# The user corrects what was understood.
USER_CORRECTION = re.compile(r"\b(no,? (no,? )?i said|i didn't say|that's not what i said|i meant)\b", re.I)
LOW_CONFIDENCE = 0.75


def _confidence_evidence(turn) -> list[Evidence]:
    c = turn.transcript_confidence
    if c is None:
        return []
    note = " (low)" if c < LOW_CONFIDENCE else ""
    return [Evidence(kind="metric", label="STT transcript confidence", value=f"{c:.2f}{note}", turn_id=turn.id)]


def stt_missing_time_number(session: Session) -> list[Finding]:
    findings = []
    hits = [t for t in session.turns if t.speaker == "user" and MISSING_TIME_NUMBER.search(t.text)]
    for turn in hits:
        match = MISSING_TIME_NUMBER.search(turn.text).group(0)
        evidence = [quote(turn, "User transcript"), Evidence(kind="transcript", label="Time without a number",
                                                            value=match, turn_id=turn.id)]
        evidence += _confidence_evidence(turn)
        reply = next_turn(session, turn, "agent")
        if reply:
            evidence.append(quote(reply, "Agent's next reply (what it understood)"))
        findings.append(Finding(
            id=finding_id(session, "stt_missing_time_number", turn.id),
            session_id=session.id,
            detector="stt_missing_time_number",
            title=f"Time of day transcribed without its number: {match!r}",
            summary="The transcript contains a time of day with no hour, so the agent never received the "
                    "actual time.",
            category="speech_recognition",
            severity="high",
            status="suspected",
            time_range=TimeRange(start_s=turn_time(turn) or 0.0, end_s=turn.end_s or turn_time(turn) or 0.0),
            turn_ids=[t.id for t in (turn, reply) if t],
            evidence=evidence,
            root_cause=RootCause(
                statement="Speech-to-text likely dropped a spoken number (for example \"8\" in \"8 AM\").",
                confidence="likely",
                missing_evidence=["Audio of the turn, or a reference transcript of what was said"],
            ),
            next_steps=[
                "Listen to the turn to confirm what was said.",
                "Re-run the same script with a different STT model and compare the transcript.",
                "Add the expected times as STT keyterms, or have the agent ask for the exact time when it's missing.",
            ],
        ))
    if len(hits) > 1:
        for f in findings:
            f.evidence.append(Evidence(kind="metric", label="Same pattern in this session",
                                       value=f"{len(hits)} user turns"))
    return findings


def stt_misunderstanding(session: Session) -> list[Finding]:
    """A user turn the agent couldn't make sense of, ideally followed by the user correcting it."""
    findings = []
    for reply in session.turns:
        if reply.speaker != "agent" or not AGENT_CONFUSED.search(reply.text):
            continue
        heard = session.turns[reply.index - 1] if reply.index > 0 else None
        if heard is None or heard.speaker != "user":
            continue
        correction = next_turn(session, reply, "user")
        corrected = bool(correction and USER_CORRECTION.search(correction.text))
        evidence = [quote(heard, "What the agent heard"), *_confidence_evidence(heard),
                    quote(reply, "Agent could not make sense of it")]
        if corrected:
            evidence.append(quote(correction, "User then corrected it"))
        findings.append(Finding(
            id=finding_id(session, "stt_misunderstanding", heard.id),
            session_id=session.id,
            detector="stt_misunderstanding",
            title=f"Agent could not make sense of {heard.text!r}",
            summary=("The agent was confused by this transcript and the user then said they had said something "
                     "else." if corrected else
                     "The agent was confused by this transcript. It may be a recognition error or genuinely "
                     "unclear speech."),
            category="speech_recognition",
            severity="medium" if corrected else "low",
            status="suspected",
            time_range=TimeRange(start_s=turn_time(heard) or 0.0,
                                 end_s=turn_time(correction if corrected else reply) or 0.0),
            turn_ids=[t.id for t in (heard, reply, correction if corrected else None) if t],
            evidence=evidence,
            root_cause=RootCause(
                statement="Speech-to-text misrecognized the user's words." if corrected else
                          "Speech-to-text misrecognized the user, or the user's phrasing was ambiguous.",
                confidence="likely" if corrected else "possible",
                missing_evidence=["Audio of the turn, or a reference transcript of what was said"],
            ),
            next_steps=["Listen to the turn and compare it with the transcript.",
                        "Check whether the user was speaking over the agent at the time."],
        ))
    return findings
