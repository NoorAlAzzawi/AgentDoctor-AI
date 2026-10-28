"""False interruptions: the agent paused for voice activity that never became a transcript."""

from ..models import Finding, RootCause, Session, TimeRange
from ._util import ASKED_TO_REPEAT, agent_turn_at, event_ev, finding_id, fmt_t, next_turn, quote

# A user voice-activity start this soon after the agent starts speaking is consistent with echo.
ONSET_WINDOW_S = (0.05, 0.6)


def false_interruption_pause(session: Session) -> list[Finding]:
    findings = []
    events = session.events
    for fi in (e for e in events if e.type == "false_interruption"):
        before = [e for e in events if e.t_s <= fi.t_s and e.id != fi.id]
        held = next((e for e in reversed(before) if e.type == "agent_state" and e.data.get("to") == "listening"), None)
        if held is None:
            continue
        trigger = next((e for e in reversed(before) if e.type == "user_state" and e.data.get("to") == "speaking"
                        and e.t_s <= held.t_s), None)
        released = next((e for e in events if trigger and e.t_s > trigger.t_s and e.type == "user_state"), None)
        pause_s = round(fi.t_s - held.t_s, 2)
        mid_reply = held.data.get("from") == "speaking"

        turn = agent_turn_at(session, fi.t_s) if mid_reply else None
        if turn is None:  # pre-reply hold, or no matching span: the next agent turn is the one delayed
            turn = next((t for t in session.turns if t.speaker == "agent" and (t.start_s or 0) >= held.t_s - 0.05), None)

        evidence = []
        if turn:
            evidence.append(quote(turn, "Agent reply affected"))
        if trigger:
            burst = f"{round(released.t_s - trigger.t_s, 2)}s burst" if released else "burst"
            evidence.append(event_ev(trigger, "User voice activity started", f"{burst}, no transcript followed"))
        evidence.append(event_ev(held, "Agent held its reply", f"{held.data.get('from')} → listening"))
        evidence.append(event_ev(fi, "False interruption, reply resumed", f"after {pause_s}s"))

        # Was the agent's own speech start right before the trigger? (echo pattern)
        started = next((e for e in reversed(before) if e.type == "agent_state" and e.data.get("to") == "speaking"
                        and trigger and e.t_s <= trigger.t_s), None)
        onset_gap = round(trigger.t_s - started.t_s, 2) if (trigger and started and mid_reply) else None
        echo_pattern = onset_gap is not None and ONSET_WINDOW_S[0] <= onset_gap <= ONSET_WINDOW_S[1]
        if echo_pattern:
            evidence.append(event_ev(started, "Agent had just started speaking", f"{onset_gap}s before the trigger"))

        follow = next_turn(session, turn, "user") if turn else None
        user_missed_it = bool(follow and ASKED_TO_REPEAT.search(follow.text))
        if user_missed_it:
            evidence.append(quote(follow, "User then asked the agent to repeat"))

        if mid_reply:
            title = f"Agent paused mid-reply for {pause_s}s, then resumed"
            summary = (f"At {fmt_t(held.t_s)} the agent stopped speaking because of user voice activity that "
                       f"never produced a transcript, then resumed the same reply {pause_s}s later.")
        else:
            title = f"Agent held its reply for {pause_s}s before speaking"
            summary = (f"At {fmt_t(held.t_s)} the agent held its prepared reply because of user voice activity "
                       f"that never produced a transcript, adding {pause_s}s before it spoke.")

        if echo_pattern:
            cause = RootCause(
                statement=f"Possible acoustic echo: the trigger began {onset_gap}s after the agent started "
                          "speaking, which fits the agent's own voice reaching the microphone.",
                confidence="possible",
                missing_evidence=["Microphone audio at the trigger time", "The same script re-run with headphones"],
            )
        else:
            cause = RootCause(
                statement="A short sound on the user's side (breath, filler, noise) was treated as a possible "
                          "interruption.",
                confidence="possible",
                missing_evidence=["Microphone audio at the trigger time"],
            )

        findings.append(Finding(
            id=finding_id(session, "false_interruption_pause", fi.id),
            session_id=session.id,
            detector="false_interruption_pause",
            title=title,
            summary=summary,
            category="turn_taking",
            severity="high" if user_missed_it else ("medium" if mid_reply else "low"),
            status="confirmed",
            time_range=TimeRange(start_s=(trigger or held).t_s, end_s=fi.t_s),
            turn_ids=[t.id for t in (turn, follow if user_missed_it else None) if t],
            evidence=evidence,
            root_cause=cause,
            next_steps=[
                "Listen to the microphone channel at the trigger time.",
                "Re-run the same script with headphones and compare false-interruption counts.",
                "Lower false_interruption_timeout (default 2.0s) to shorten the gap, or raise "
                "min_interruption_duration if short sounds are the trigger.",
            ],
        ))
    return findings
