"""Shared helpers for detectors: text matching, turn lookup, evidence builders."""

import re

from ..models import Evidence, Session, TimelineEvent, Turn

FAREWELL = re.compile(r"\b(good ?bye|bye(-bye)?)\b", re.I)
# Receptionist-side cues that the call is legitimately wrapping up.
CLOSING_CUES = re.compile(
    r"\b(anything else|all set|that's it|that is it|have a (good|great|wonderful|nice)|"
    r"thank(s| you) for calling|good ?bye|bye)\b",
    re.I,
)
ASKED_TO_REPEAT = re.compile(
    r"\b(didn't hear|did not hear|what did you say|say that again|repeat that|come again|pardon)\b",
    re.I,
)


def fmt_t(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


def turn_time(turn: Turn) -> float | None:
    return turn.start_s if turn.start_s is not None else turn.committed_s


def quote(turn: Turn, label: str) -> Evidence:
    return Evidence(kind="transcript", label=label, value=turn.text, turn_id=turn.id, t_s=turn_time(turn))


def event_ev(event: TimelineEvent, label: str, value: str) -> Evidence:
    return Evidence(kind="event", label=label, value=value, event_id=event.id, t_s=event.t_s)


def next_turn(session: Session, turn: Turn, speaker: str | None = None) -> Turn | None:
    for t in session.turns[turn.index + 1 :]:
        if speaker is None or t.speaker == speaker:
            return t
    return None


def prev_turn(session: Session, turn: Turn, speaker: str | None = None) -> Turn | None:
    for t in reversed(session.turns[: turn.index]):
        if speaker is None or t.speaker == speaker:
            return t
    return None


def agent_turn_at(session: Session, t: float) -> Turn | None:
    """The agent turn whose speech span contains time t."""
    for turn in session.turns:
        if turn.speaker == "agent" and turn.start_s is not None and turn.end_s is not None:
            if turn.start_s <= t <= turn.end_s:
                return turn
    return None


def finding_id(session: Session, detector: str, anchor: str) -> str:
    return f"{session.id}:{detector}:{anchor}"
