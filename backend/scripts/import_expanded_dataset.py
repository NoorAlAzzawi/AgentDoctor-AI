"""Import only four approved recordings through a privacy-minimized projection.

Run offline: uv run python scripts/import_expanded_dataset.py <recording-directory>
Existing destination files are never overwritten. No raw report is copied or saved.
"""

import argparse
import re
from pathlib import Path

from agentdoctor.detectors import run_detectors
from agentdoctor.fingerprints import build_fingerprints
from agentdoctor.ingest import load_livekit_report, privacy_scan
from agentdoctor.models import Session, SourceInfo, TimelineEvent
from agentdoctor.store import DEFAULT_DATA_DIR

APPROVED = [
    ("session-10-28-180550", "s08-nano-role-confounded-end-call",
     "Nano pipeline · role-confounded premature end_call",
     "Role-confounded: the human spoke as a patient and the AI answered as a receptionist, contrary to its recorded patient role. "
     "Two premature end_call dispatches form one finding. This is not a normal baseline or evidence that Nano is generally worse."),
    ("session-10-28-183952", "s09-recommended-closing-policy",
     "Recommended pipeline · readback success, closing-policy failure",
     "Correct conversational roles. Thursday 9:15 AM readback succeeds; end_call follows the human goodbye without a recorded assistant goodbye."),
    ("session-10-28-185048", "s10-recommended-correction-control",
     "Recommended pipeline · corrected readback and closing control",
     "Correct conversational roles. Tuesday-to-Thursday correction is retained. Both goodbye messages are committed before dispatch, "
     "although assistant audio starts afterward. Large transcript-commit delays remain recorded; this is not a clean latency control."),
    ("session-10-28-185644", "s11-nano-correction-closing",
     "Nano pipeline · corrected readback, closing-policy failure",
     "Correct conversational roles. Thursday 8 AM readback succeeds; end_call follows only the human goodbye. "
     "The transcript contains both a complete time and a later missing-hour fragment; this does not prove an STT error or lost appointment state."),
]
SANITIZATION_NOTE = (
    " Identity/practice text is pseudonymized; DOB values are REDACTED and unrelated profile instructions omitted. "
    "Recorded wording, turn order and timing otherwise remain intact. Repeated/shared scheduling scripts are not independent trials. "
    "Audio setup is not established by the report."
)


def anonymize(text: str) -> str:
    # Plain tokens avoid introducing bracketed stage directions into transcript data.
    substitutions = [
        (r"\bMichael\s+Lopez\b", "Alex Morgan"),
        (r"\bM-I-C-H-A-E-L\b", "A-L-E-X"),
        (r"\bL-O-P-E-Z\b", "M-O-R-G-A-N"),
        (r"\bLopez\b", "Morgan"),
        (r"\b(?:Dr\.?|doctor)\s+Patel\b", "Dr. Avery"),
        (r"\b(?:Sarah|Sam)\b", "Taylor"),
        (r"\b(?:Westside|West Side|Westar) Family(?: Medicine)?\b", "Example Family Practice"),
        (r"\bMarch\s+14(?:th)?,?\s+1973\b", "REDACTED"),
    ]
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def retained_instructions(text: str) -> str:
    lines = text.splitlines()
    role_start = next(i for i, line in enumerate(lines) if line.startswith("The person you are talking to"))
    role_end = next(i for i in range(role_start, len(lines)) if not lines[i].strip())
    closing_start = lines.index("Ending the call:")
    goal_start = lines.index("Your goal on this call:")
    return anonymize("\n".join(lines[role_start:role_end] + [""] + lines[closing_start:goal_start]
                               + lines[goal_start:]).strip())


def project(path: Path, entry: tuple, *, imported_at: str | None = None) -> Session:
    folder, sid, title, notes = entry
    original = load_livekit_report(path, session_id=sid, title=title, synthetic=False)
    turns = [turn.model_copy(update={"text": anonymize(turn.text)}, deep=True) for turn in original.turns]
    events = []
    for event in original.events:
        allowed = {
            "agent_state": ("from", "to"), "user_state": ("from", "to"),
            "false_interruption": ("resumed",),
            "overlapping_speech": ("is_interruption", "probability", "detection_delay_s"),
            "session_close": ("reason",), "tool_execution": ("tool_name", "call_ref", "phase", "status"),
        }[event.type]
        data = {k: event.data[k] for k in allowed if k in event.data}
        if event.type == "session_close":
            if event.data.get("error") is not None:
                raise ValueError("Unexpected close error requires a separate privacy review")
            data["error"] = None
        events.append(TimelineEvent(id=event.id, t_s=event.t_s, type=event.type, data=data))
    # Explicit Session constructor: no other source report fields cross this boundary.
    session = Session(
        id=sid, title=title, notes=notes + SANITIZATION_NOTE,
        pipeline_label=original.pipeline_label, models=original.models.model_copy(deep=True), synthetic=False,
        stats=original.stats.model_copy(deep=True), config=original.config.model_copy(deep=True),
        source=SourceInfo(sdk_version=original.source.sdk_version, original_name=folder,
                          # This is normalization provenance, never a raw recording timestamp.
                          imported_at=imported_at or original.source.imported_at, synthetic=False),
        speaker_labels={"user": "Human tester · patient role (confounded)", "agent": "AI · receptionist role (confounded)"}
        if sid.startswith("s08-") else {"user": "Receptionist (human tester)", "agent": "Patient agent"},
        agent_instructions=retained_instructions(original.agent_instructions or ""),
        turns=turns, events=events, usage=[],  # model identities above suffice; usage payloads are unnecessary
    )
    texts = [t.text for t in session.turns] + [session.agent_instructions or "", session.notes or ""]
    if privacy_scan(texts, blocked_terms=["Michael", "Lopez", "Patel", "Sarah", "Westside", "Westar"]):
        raise ValueError("Projected text needs privacy review")
    # Sanitization may alter identifying quotes, never diagnostic outcomes or evidence links/timing.
    def observations(s):
        return [(f.detector, f.category, f.status, f.severity, f.time_range, f.turn_ids,
                 [(e.kind, e.turn_id, e.event_id, e.t_s) for e in f.evidence]) for f in run_detectors(s)]
    if observations(original) != observations(session):
        raise ValueError("Sanitization changed diagnostic observations; stop for review")
    return session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    targets = [DEFAULT_DATA_DIR / f"{entry[1]}.json" for entry in APPROVED]
    if any(p.exists() for p in targets):
        raise SystemExit("Refusing to overwrite an existing session")
    sessions = [project(args.source / entry[0] / "session_report.json", entry) for entry in APPROVED]
    existing = [Session.model_validate_json(p.read_text()) for p in sorted(DEFAULT_DATA_DIR.glob("*.json"))]
    result = build_fingerprints(existing + sessions)
    findings = [f for s in existing + sessions for f in run_detectors(s)]
    actual = (len(existing + sessions), len(findings), sum(f.status == "confirmed" for f in findings),
              sum(f.status == "suspected" for f in findings), len(result.fingerprints),
              result.grouped_finding_count, len(result.ungrouped))
    if actual != (11, 42, 27, 15, 8, 39, 3):
        raise SystemExit(f"Projection differs from approved expectations: {actual}; no files written")
    for path, session in zip(targets, sessions):
        with path.open("x") as output:
            output.write(session.model_dump_json(indent=2) + "\n")
        print(f"Added {path.name}: {len(session.turns)} turns, {len(session.events)} events")
    print("Observed counts:", actual)


if __name__ == "__main__":
    main()
