"""Evidence-based detectors. Each takes a Session and returns a list of Findings.

Rules every detector follows:
- `status` is "confirmed" only when the failure is directly visible in the report
  (an event, a metric, or the agent's own text). Anything inferred is "suspected".
- Speech-recognition findings are always "suspected": without audio or a reference
  transcript, the true words are unknown.
- Every finding cites evidence with turn or event ids and timestamps.
"""

from ..models import Finding, Session
from .endpointing import endpointing_timeout
from .interruptions import false_interruption_pause
from .llm_behavior import premature_goodbye, stage_direction_spoken, talked_after_farewell
from .speech_recognition import stt_misunderstanding, stt_missing_time_number
from .state import contradicts_instructions, readback_mismatch

DETECTORS = [
    false_interruption_pause,
    endpointing_timeout,
    stt_missing_time_number,
    stt_misunderstanding,
    premature_goodbye,
    talked_after_farewell,
    stage_direction_spoken,
    contradicts_instructions,
    readback_mismatch,
]

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run_detectors(session: Session) -> list[Finding]:
    findings = [f for detector in DETECTORS for f in detector(session)]
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity],
                                           f.time_range.start_s if f.time_range else float("inf")))
