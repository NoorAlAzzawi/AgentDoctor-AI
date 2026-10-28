"""Endpointing timeouts: user turns ended only because the maximum wait ran out."""

import re

from ..models import Evidence, Finding, RootCause, Session, TimeRange
from ._util import finding_id, fmt_t, next_turn, quote, turn_time

TOLERANCE_S = 0.05
# Transcript endings that suggest the sentence was not finished.
_DANGLING = re.compile(
    r"(,|—|-|\.\.\.)$|\b(the|a|an|your|my|to|and|or|but|um|uh|is|for|of|with|at|in|on|that|i)$", re.I
)


def looks_unfinished(text: str) -> bool:
    text = text.strip()
    return bool(text) and (text[-1] not in ".?!" or bool(_DANGLING.search(text.rstrip(".?!"))))


def endpointing_timeout(session: Session) -> list[Finding]:
    limit = session.config.max_endpointing_delay_s
    if limit is None:
        return []
    timed_out = [t for t in session.turns if t.speaker == "user"
                 and t.metrics.end_of_turn_delay_s is not None
                 and t.metrics.end_of_turn_delay_s >= limit - TOLERANCE_S]
    if not timed_out:
        return []

    findings = []
    replies = [next_turn(session, t) for t in timed_out]
    waits = [r.metrics.e2e_latency_s for r in replies if r and r.speaker == "agent" and r.metrics.e2e_latency_s]
    user_turns = session.stats.user_turns or 1
    evidence = [Evidence(kind="config", label="max_endpointing_delay", value=f"{limit}s")]
    evidence += [
        Evidence(kind="metric", label=f"End-of-turn delay at {fmt_t(turn_time(t))}",
                 value=f"{t.metrics.end_of_turn_delay_s}s: {t.text!r}", turn_id=t.id, t_s=turn_time(t))
        for t in timed_out
    ]
    if waits:
        evidence.append(Evidence(kind="metric", label="Agent response latency after these turns",
                                 value=f"median {sorted(waits)[len(waits) // 2]}s (n={len(waits)})"))
    share = len(timed_out) / user_turns
    findings.append(Finding(
        id=finding_id(session, "endpointing_timeout", "session"),
        session_id=session.id,
        detector="endpointing_timeout",
        title=f"{len(timed_out)} of {user_turns} user turns ended by the {limit}s endpointing timeout",
        summary="For these turns the turn detector never judged the user finished, so the agent waited the "
                f"full {limit}s before replying.",
        category="latency",
        severity="medium" if share >= 0.15 else "low",
        status="confirmed",
        time_range=None,
        turn_ids=[t.id for t in timed_out],
        evidence=evidence,
        root_cause=RootCause(
            statement="The user paused or trailed off mid-thought, so end-of-turn prediction stayed uncertain "
                      "until the maximum delay.",
            confidence="likely",
            missing_evidence=["Audio of the pauses", "Turn-detector probabilities for these turns"],
        ),
        next_steps=[
            "Read the timed-out turns: are they fragments or complete sentences?",
            "If complete, check turn-detector settings; if fragments, the wait was correct.",
            "Avoid lowering max_endpointing_delay blindly: it would cut users off mid-sentence.",
        ],
    ))

    # The agent answered a turn that looks unfinished: likely talking past the user.
    for turn, reply in zip(timed_out, replies):
        if not (reply and reply.speaker == "agent" and looks_unfinished(turn.text)):
            continue
        findings.append(Finding(
            id=finding_id(session, "fragment_reply", turn.id),
            session_id=session.id,
            detector="fragment_reply",
            title=f"Agent replied to an unfinished sentence: {turn.text!r}",
            summary="The user turn was closed by the endpointing timeout and its transcript looks unfinished; "
                    "the agent answered it anyway.",
            category="turn_taking",
            severity="medium",
            status="suspected",
            time_range=TimeRange(start_s=turn_time(turn) or 0.0, end_s=turn_time(reply) or 0.0),
            turn_ids=[turn.id, reply.id],
            evidence=[
                quote(turn, "User turn (looks unfinished)"),
                Evidence(kind="metric", label="End-of-turn delay", value=f"{turn.metrics.end_of_turn_delay_s}s "
                         f"(= max {limit}s)", turn_id=turn.id),
                quote(reply, "Agent reply"),
            ],
            root_cause=RootCause(
                statement="The user paused longer than the maximum endpointing delay mid-sentence, or the "
                          "transcript dropped the end of the sentence.",
                confidence="possible",
                missing_evidence=["Audio of the turn", "What the user said next"],
            ),
            next_steps=["Listen to the turn to see whether the user had finished.",
                        "Check whether the next user turn completes this sentence."],
        ))
    return findings
