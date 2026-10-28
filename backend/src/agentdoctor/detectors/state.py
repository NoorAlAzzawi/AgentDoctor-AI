"""Conversation-state checks that can be decided from text alone."""

import re

from ..models import Evidence, Finding, RootCause, Session, TimeRange
from ._util import finding_id, prev_turn, quote, turn_time

MONTHS = "january february march april may june july august september october november december".split()
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
PHONE = re.compile(r"\b(\d{3})[-.\s]?(\d{3})[-.\s]?(\d{4})\b")
WEEKDAY = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
TIME = re.compile(r"\b(\d{1,2}|" + "|".join(_NUM_WORDS) + r")(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
READBACK_REQUEST = re.compile(
    r"\b(repeat|confirm|remind me)\b.*\b(appointment|day and time|date and time)\b"
    r"|\bwhen is your appointment\b", re.I)


def _date(text: str):
    m = DATE.search(text)
    return (m.group(1).lower(), int(m.group(2)), int(m.group(3))) if m else None


def _phone(text: str):
    m = PHONE.search(text)
    return "".join(m.groups()) if m else None


def _time(text: str):
    found = TIME.findall(text)
    if not found:
        return None
    hour, minute, ampm = found[-1]
    hour = int(hour) if hour.isdigit() else _NUM_WORDS[hour.lower()]
    return (hour, int(minute or 0), ampm.lower().replace(".", ""))


def _weekday(text: str):
    found = WEEKDAY.findall(text)
    return found[-1].lower() if found else None


def _instruction_value(session: Session, label: str) -> str | None:
    m = re.search(rf"^\W*{label}\s*:\s*(.+)$", session.agent_instructions or "", re.I | re.M)
    return m.group(1).strip() if m else None


def contradicts_instructions(session: Session) -> list[Finding]:
    """The agent states a personal detail that differs from its own instructions."""
    checks = [
        ("Date of birth", r"birth", _date),
        ("Phone number", r"phone|number|reach", _phone),
    ]
    findings = []
    for label, context, parse in checks:
        expected_text = _instruction_value(session, label)
        expected = parse(expected_text) if expected_text else None
        if expected is None:
            continue
        for turn in session.turns:
            if turn.speaker != "agent":
                continue
            asked = prev_turn(session, turn, "user")
            if not re.search(context, turn.text + " " + (asked.text if asked else ""), re.I):
                continue
            said = parse(turn.text)
            if said is None or said == expected:
                continue
            findings.append(Finding(
                id=finding_id(session, "contradicts_instructions", f"{turn.id}:{label}"),
                session_id=session.id,
                detector="contradicts_instructions",
                title=f"Agent gave a {label.lower()} that differs from its instructions",
                summary=f"The agent's instructions give one {label.lower()}, but the agent said another.",
                category="missing_or_incorrect_info",
                severity="high",
                status="confirmed",
                time_range=TimeRange(start_s=turn_time(turn) or 0.0, end_s=turn.end_s or turn_time(turn) or 0.0),
                turn_ids=[turn.id],
                evidence=[quote(turn, "Agent said"),
                          Evidence(kind="config", label=f"Instructions: {label}", value=expected_text)],
                root_cause=RootCause(
                    statement="The LLM did not use the value from its instructions, or the instructions told it "
                              "to misstate the value on purpose (for example a self-correction test).",
                    confidence="possible",
                    missing_evidence=["The scenario's intent for this call"],
                ),
                next_steps=["Check whether the agent corrected itself in a later turn.",
                            "Re-run the scenario and see if the mismatch repeats."],
            ))
    return findings


def readback_mismatch(session: Session) -> list[Finding]:
    """When asked to repeat the appointment, the agent's day or time differs from the last one stated."""
    findings = []
    for turn in session.turns:
        if turn.speaker != "agent":
            continue
        request = prev_turn(session, turn, "user")
        if request is None or not READBACK_REQUEST.search(request.text):
            continue
        said_day, said_time = _weekday(turn.text), _time(turn.text)
        earlier = [t for t in session.turns[: request.index] if t.speaker == "user"]
        last_day = next((t for t in reversed(earlier) if _weekday(t.text)), None)
        last_time = next((t for t in reversed(earlier) if _time(t.text)), None)
        problems, evidence = [], [quote(request, "Read-back requested"), quote(turn, "Agent's read-back")]
        if said_day and last_day and said_day != _weekday(last_day.text):
            problems.append(f"day {said_day.title()} vs {_weekday(last_day.text).title()}")
            evidence.append(quote(last_day, "Last day stated by the user"))
        if said_time and last_time and said_time != _time(last_time.text):
            problems.append("time differs")
            evidence.append(quote(last_time, "Last time stated by the user"))
        if not problems:
            continue
        findings.append(Finding(
            id=finding_id(session, "readback_mismatch", turn.id),
            session_id=session.id,
            detector="readback_mismatch",
            title=f"Appointment read-back doesn't match the last slot stated ({', '.join(problems)})",
            summary="When asked to repeat the appointment, the agent gave a day or time that differs from the "
                    "last one the user stated.",
            category="conversation_state",
            severity="high",
            status="confirmed",
            time_range=TimeRange(start_s=turn_time(request) or 0.0, end_s=turn_time(turn) or 0.0),
            turn_ids=[e.turn_id for e in evidence if e.turn_id],
            evidence=evidence,
            root_cause=RootCause(
                statement="The agent lost track of the latest agreed slot, or an earlier turn was transcribed "
                          "wrongly.",
                confidence="possible",
                missing_evidence=["Audio of the turns where the slot was agreed"],
            ),
            next_steps=["Trace every turn that mentions a day or time and find where they diverge."],
        ))
    return findings
