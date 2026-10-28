"""Regenerate contracts/ from the real API and demo data.

    uv run python scripts/build_contract_examples.py

Writes real API responses (health, sessions, session, findings, compare, and per-session
detail + findings under examples/by-session/), plus findings.illustrative.json: the
hand-assembled Phase 2A example, kept so existing frontend mocks keep working. Every value
in it is looked up from session s03's data (ids, timestamps, text), not typed in.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentdoctor.api import app
from agentdoctor.models import (
    Evidence,
    Finding,
    FindingsResponse,
    RootCause,
    Session,
    TimeRange,
)
from agentdoctor.store import load_session

OUT = Path(__file__).resolve().parents[2] / "contracts"
SESSION_A, SESSION_B = "s03-workshop-ab-script", "s04-recommended-ab-script"


def write(name: str, data) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {path.relative_to(OUT.parent)}")


def false_interruption_findings(s: Session) -> list[Finding]:
    findings = []
    for fi in (e for e in s.events if e.type == "false_interruption"):
        before = [e for e in s.events if e.t_s <= fi.t_s]
        paused = next(e for e in reversed(before) if e.type == "agent_state" and e.data["to"] == "listening")
        trigger = next(e for e in reversed(before) if e.type == "user_state" and e.data["to"] == "speaking")
        released = next(e for e in s.events if e.t_s > trigger.t_s and e.type == "user_state")
        turn = next(t for t in s.turns if t.speaker == "agent" and t.start_s <= fi.t_s <= t.end_s)
        started = next(e for e in reversed(before) if e.type == "agent_state" and e.data["to"] == "speaking"
                       and e.t_s <= trigger.t_s)
        pause_s = round(fi.t_s - paused.t_s, 2)
        findings.append(Finding(
            id=f"{s.id}:false-interruption:{fi.id}",
            session_id=s.id,
            detector="false_interruption_pause",
            title=f"Agent paused mid-reply for {pause_s}s, then resumed",
            summary=(f"A {round(released.t_s - trigger.t_s, 2)}s burst of user voice activity paused the "
                     f"agent's reply. No transcript followed, so the agent resumed after {pause_s}s."),
            category="turn_taking",
            severity="medium",
            status="confirmed",
            time_range=TimeRange(start_s=started.t_s, end_s=fi.t_s),
            turn_ids=[turn.id],
            evidence=[
                Evidence(kind="transcript", label="Agent reply that was paused", value=turn.text,
                         turn_id=turn.id, t_s=turn.start_s),
                Evidence(kind="event", label="User voice activity started",
                         value=f"{round(trigger.t_s - started.t_s, 2)}s after the agent started speaking",
                         event_id=trigger.id, t_s=trigger.t_s),
                Evidence(kind="event", label="Agent audio paused", value="speaking → listening",
                         event_id=paused.id, t_s=paused.t_s),
                Evidence(kind="event", label="False interruption, speech resumed", value="resumed=true",
                         event_id=fi.id, t_s=fi.t_s),
            ],
            root_cause=RootCause(
                statement="Possible acoustic echo: the microphone may have picked up the agent's own "
                          "voice shortly after it started speaking.",
                confidence="possible",
                missing_evidence=[
                    "Microphone audio at the trigger time (voice, noise, or echo?)",
                    "A repeat of the same script with headphones",
                ],
            ),
            next_steps=[
                "Listen to the microphone channel around the trigger time.",
                "Re-run the same script with headphones and compare false-interruption counts.",
                "Consider lowering false_interruption_timeout to shorten the audible gap.",
            ],
        ))
    return findings


def suspicious_short_transcript_finding(s: Session) -> Finding:
    cut = next(t for t in s.turns if t.speaker == "agent" and t.interrupted and t.text == "Oh,")
    short = s.turns[cut.index + 1]
    reply = s.turns[cut.index + 2]
    return Finding(
        id=f"{s.id}:short-transcript:{short.id}",
        session_id=s.id,
        detector="short_transcript_interrupt",
        title=f"One-word transcript {short.text!r} cut off the agent and changed its reply",
        summary="The agent was interrupted by a very short user transcript, then answered it as if "
                "the caller had gone silent.",
        category="speech_recognition",
        severity="low",
        status="suspected",
        time_range=TimeRange(start_s=cut.start_s, end_s=reply.end_s),
        turn_ids=[cut.id, short.id, reply.id],
        evidence=[
            Evidence(kind="transcript", label="Agent reply cut off", value=cut.text, turn_id=cut.id, t_s=cut.start_s),
            Evidence(kind="transcript", label="Short user transcript", value=short.text, turn_id=short.id,
                     t_s=short.start_s),
            Evidence(kind="metric", label="User speech duration",
                     value=f"{round(short.end_s - short.start_s, 2)}s", turn_id=short.id),
            Evidence(kind="transcript", label="Agent's next reply", value=reply.text, turn_id=reply.id,
                     t_s=reply.start_s),
        ],
        root_cause=RootCause(
            statement="Speech-to-text may have transcribed a brief sound as a word. The data can't tell "
                      "whether the tester actually said it.",
            confidence="possible",
            missing_evidence=["Microphone audio for this turn", "STT confidence for the word"],
        ),
        next_steps=[
            "Listen to the microphone channel for this turn.",
            "Check whether a minimum interruption word count would have prevented the cut-off.",
        ],
    )


def main() -> None:
    client = TestClient(app)
    write("examples/health.json", client.get("/api/health").json())
    write("examples/sessions.json", client.get("/api/sessions").json())
    write("examples/session.json", client.get(f"/api/sessions/{SESSION_A}").json())
    write("examples/findings.json", client.get(f"/api/sessions/{SESSION_A}/findings").json())
    write("examples/compare.json", client.get(f"/api/compare?a={SESSION_A}&b={SESSION_B}").json())
    # Every session's detail and findings, so the frontend's mock mode can open any session.
    for summary in client.get("/api/sessions").json():
        sid = summary["id"]
        write(f"examples/by-session/{sid}.session.json", client.get(f"/api/sessions/{sid}").json())
        write(f"examples/by-session/{sid}.findings.json", client.get(f"/api/sessions/{sid}/findings").json())

    s = load_session(SESSION_A)
    illustrative = FindingsResponse(
        session_id=s.id,
        detectors_run=["false_interruption_pause", "short_transcript_interrupt"],
        findings=false_interruption_findings(s) + [suspicious_short_transcript_finding(s)],
    )
    write("examples/findings.illustrative.json", illustrative.model_dump(mode="json"))
    write("openapi.json", app.openapi())


if __name__ == "__main__":
    main()
