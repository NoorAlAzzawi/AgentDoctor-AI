"""LLM behavior visible in the agent's own words: stage directions and call-ending mistakes."""

import re

from ..models import Evidence, Finding, RootCause, Session, TimeRange
from ._util import CLOSING_CUES, FAREWELL, finding_id, fmt_t, next_turn, prev_turn, quote, turn_time

STAGE_DIRECTION = re.compile(r"\([^)]{1,80}\)|\[[^\]]{1,80}\]|\*[^*]{1,80}\*")
SUBSTANTIVE_WORDS = 3


def _instruction_evidence(session: Session, pattern: str, label: str) -> list[Evidence]:
    """Quote the agent's instructions line matching pattern, if any."""
    for line in (session.agent_instructions or "").splitlines():
        if re.search(pattern, line, re.I):
            return [Evidence(kind="config", label=label, value=line.strip(" -"))]
    return []


def stage_direction_spoken(session: Session) -> list[Finding]:
    findings = []
    for turn in session.turns:
        if turn.speaker != "agent":
            continue
        for match in STAGE_DIRECTION.finditer(turn.text):
            rule = _instruction_evidence(session, r"parenthes|bracket|stage direction",
                                         "Instruction that forbids this")
            findings.append(Finding(
                id=finding_id(session, "stage_direction_spoken", f"{turn.id}:{match.start()}"),
                session_id=session.id,
                detector="stage_direction_spoken",
                title=f"Agent output a stage direction: {match.group(0)!r}",
                summary="The agent's reply contains non-speech text, which the TTS reads aloud to the caller.",
                category="llm_behavior",
                severity="high",
                status="confirmed",
                time_range=TimeRange(start_s=turn_time(turn) or 0.0, end_s=turn.end_s or turn_time(turn) or 0.0),
                turn_ids=[turn.id],
                evidence=[quote(turn, "Agent reply"), *rule],
                root_cause=RootCause(
                    statement="The LLM produced narration instead of speech" + (
                        ", despite an instruction against it" if rule else
                        "; its instructions do not forbid this") + ", and nothing filtered it before TTS.",
                    confidence="likely",
                    missing_evidence=["A re-run with an output filter (or stricter instruction) that no "
                                      "longer produces it"],
                ),
                next_steps=["Add an instruction: only output words to be spoken aloud.",
                            "Strip bracketed or parenthesized text before it reaches TTS."],
            ))
    return findings


def premature_goodbye(session: Session) -> list[Finding]:
    """The agent said goodbye although the other side had not wrapped up, and the call went on."""
    findings = []
    for turn in session.turns:
        if turn.speaker != "agent" or not FAREWELL.search(turn.text):
            continue
        before, after = prev_turn(session, turn, "user"), next_turn(session, turn, "user")
        if after is None or FAREWELL.search(after.text) or len(after.text.split()) < SUBSTANTIVE_WORDS:
            continue
        if before is not None and CLOSING_CUES.search(before.text):
            continue  # the user was wrapping up, so a goodbye was reasonable
        rule = _instruction_evidence(session, r"goodbye|good-bye", "Agent instruction about goodbyes")
        findings.append(Finding(
            id=finding_id(session, "premature_goodbye", turn.id),
            session_id=session.id,
            detector="premature_goodbye",
            title=f"Agent said goodbye at {fmt_t(turn_time(turn))} while the call was still going",
            summary="The agent ended its side of the conversation although the other side had not wrapped up; "
                    "the conversation continued afterwards.",
            category="llm_behavior",
            severity="medium",
            status="confirmed",
            time_range=TimeRange(start_s=turn_time(before or turn) or 0.0, end_s=turn_time(after) or 0.0),
            turn_ids=[t.id for t in (before, turn, after) if t],
            evidence=[*([quote(before, "User's previous turn (no closing)")] if before else []),
                      quote(turn, "Agent said goodbye"), quote(after, "Conversation continued"), *rule],
            root_cause=RootCause(
                statement="The agent treated agreement on a detail as the end of the call" + (
                    "; its instructions tie goodbye to that moment." if rule else "."),
                confidence="likely" if rule else "possible",
                missing_evidence=["A re-run of the same script with the goodbye instruction rewritten"]
                + ([] if rule else ["The agent's instructions about ending the call"]),
            ),
            next_steps=["Instruct the agent to say goodbye only after the other side wraps up.",
                        "Re-run the same script and check that the goodbye moves to the end."],
        ))
    return findings


def talked_after_farewell(session: Session) -> list[Finding]:
    """Both sides said goodbye, but the session stayed open and the agent kept talking."""
    turns = session.turns
    for i, turn in enumerate(turns):
        if not FAREWELL.search(turn.text):
            continue
        partner = next((t for t in turns[i + 1 : i + 3] if t.speaker != turn.speaker and FAREWELL.search(t.text)), None)
        if partner is None:
            continue
        later_agent = [t for t in turns[partner.index + 1 :] if t.speaker == "agent" and not FAREWELL.search(t.text)]
        if not later_agent:
            return []
        close = next((e for e in session.events if e.type == "session_close"), None)
        last = turns[-1]
        evidence = [quote(turn, "Goodbye"), quote(partner, "Goodbye returned"),
                    *[quote(t, "Agent kept talking") for t in later_agent[:3]]]
        if close:
            evidence.append(Evidence(kind="event", label="Session closed", value=str(close.data.get("reason")),
                                     event_id=close.id, t_s=close.t_s))
        return [Finding(
            id=finding_id(session, "talked_after_farewell", partner.id),
            session_id=session.id,
            detector="talked_after_farewell",
            title=f"Session stayed open after both sides said goodbye; agent spoke {len(later_agent)} more time(s)",
            summary="After the goodbye exchange nothing ended the call, so the agent kept responding to later input.",
            category="conversation_state",
            severity="medium",
            status="confirmed",
            time_range=TimeRange(start_s=turn_time(turn) or 0.0, end_s=turn_time(last) or 0.0),
            turn_ids=[turn.id, partner.id, *[t.id for t in later_agent]],
            evidence=evidence,
            root_cause=RootCause(
                statement="The agent has no end-call action (no tool call appears in this session), so the "
                          "session only ends when the other side disconnects.",
                confidence="likely",
                missing_evidence=["The agent's tool configuration"],
            ),
            next_steps=["Give the agent an end-call tool and instruct it to use it after the goodbye exchange.",
                        "Re-run and check that the session closes right after the goodbye."],
        )]
    return []
