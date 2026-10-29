"""Early end_call dispatch under an explicit, recognized recorded closing policy.

This is deliberately not a general natural-language policy interpreter. Unknown
or conflicting end_call directives abstain. A transcript goodbye committed before
an invocation may still be awaiting playback; it must not become a false positive.
"""

import math
import re

from ..models import Evidence, Finding, RootCause, Session, TimeRange, Turn
from ._util import FAREWELL, event_ev, finding_id

# Narrow, inspectable policy forms. The first is the motivating recorded policy.
_BOTH_GOODBYES = re.compile(
    r"(?:after you have both said good[ -]?bye,\s*use the end_call tool to hang up"
    r"|only (?:call|use) (?:the )?end_call(?: tool)? after both (?:sides|parties) have said good[ -]?bye)\.?",
    re.I,
)


def _closing_policy(session: Session) -> str | None:
    directives = [line.strip().lstrip("-* ") for line in (session.agent_instructions or "").splitlines()
                  if re.search(r"\bend_call\b", line, re.I)]
    if not directives or any(not _BOTH_GOODBYES.fullmatch(line) for line in directives):
        return None
    return directives[0]


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _possibly_before(turn: Turn, at: float) -> bool:
    """Conservative temporal bound for farewell text, not a claim of audible delivery.

    STT may commit late; audio may still be playing after assistant commitment.
    Unknown timing cannot prove absence of a pre-dispatch goodbye. Contradictory
    timestamps likewise must not manufacture a confirmed policy violation.
    """
    if _finite(turn.committed_s) and turn.committed_s <= at:
        return True
    if _finite(turn.start_s) and turn.start_s <= at:
        return True
    if _finite(turn.end_s) and turn.end_s <= at:
        return True
    if turn.speaker == "agent" and _finite(turn.committed_s):
        return False  # creation and all available speech times are later
    if _finite(turn.start_s):
        return False  # a user turn whose speech starts later cannot close earlier
    return True  # incomplete timing: abstain rather than infer the absence of speech


def premature_end_call(session: Session) -> list[Finding]:
    policy = _closing_policy(session)
    if policy is None:
        return []
    starts = sorted((e for e in session.events if e.type == "tool_execution"
                     and e.data.get("tool_name") == "end_call" and e.data.get("phase") == "started"),
                    key=lambda e: (e.t_s, e.id))
    # Each normalized call reference identifies one dispatch, even if supplied twice.
    seen = set()
    violations = []
    for event in starts:
        ref = event.data["call_ref"]
        if ref in seen:
            continue
        seen.add(ref)
        prior = [t for t in session.turns if _finite(t.committed_s) and t.committed_s <= event.t_s]
        # No claim of premature closure with no usable conversational context.
        if {t.speaker for t in prior} != {"user", "agent"}:
            continue
        possible_goodbyes = {t.speaker for t in session.turns
                             if FAREWELL.search(t.text) and _possibly_before(t, event.t_s)}
        if possible_goodbyes == {"user", "agent"}:
            continue
        context = []
        for speaker in ("user", "agent"):
            context.append(max((t for t in prior if t.speaker == speaker), key=lambda t: (t.committed_s, t.index)))
        violations.append((event, context))
    if not violations:
        return []

    # One unresolved recorded closing condition: aggregate repeated dispatches.
    evidence = []
    quoted_ids = set()
    for i, (event, context) in enumerate(violations):
        evidence.append(event_ev(event, "end_call dispatched" if i == 0 else "Repeated end_call dispatch",
                                 f"call_ref={event.data['call_ref']}; phase=started"))
        if i == 0:
            evidence.append(Evidence(kind="config", label="Recorded end_call closing condition", value=policy))
        for turn in sorted(context, key=lambda t: (t.committed_s, t.index)):
            if turn.id not in quoted_ids:
                evidence.append(Evidence(kind="transcript", label=f"Pre-dispatch {turn.speaker} transcript (commit time)",
                                         value=turn.text, turn_id=turn.id, t_s=turn.committed_s))
                quoted_ids.add(turn.id)
        terminals = [e for e in session.events if e.type == "tool_execution"
                     and e.data.get("tool_name") == "end_call" and e.data.get("phase") == "ended"
                     and e.data.get("call_ref") == event.data["call_ref"] and e.t_s >= event.t_s]
        for terminal in sorted(terminals, key=lambda e: (e.t_s, e.id)):
            evidence.append(event_ev(terminal, "Tool terminal status (not disconnection timing)",
                                     f"call_ref={terminal.data['call_ref']}; phase=ended; status={terminal.data['status']}"))
    first, last = violations[0][0], violations[-1][0]
    return [Finding(
        id=finding_id(session, "premature_end_call", first.id), session_id=session.id,
        detector="premature_end_call", title="end_call dispatched before the recorded closing condition",
        summary="The agent dispatched end_call before the recorded closing condition was satisfied. "
                "The recorded policy requires both sides to say goodbye; the available transcript did not "
                "establish that exchange before dispatch. Dispatch does not prove immediate audible disconnection.",
        category="tool_execution", severity="high", status="confirmed",
        time_range=TimeRange(start_s=first.t_s, end_s=last.t_s),
        turn_ids=[t.id for t in sorted(session.turns, key=lambda t: t.index) if t.id in quoted_ids],
        evidence=evidence,
        root_cause=RootCause(
            statement="The agent may have inferred that the conversation was complete and triggered the call-ending tool too early.",
            confidence="possible",
            missing_evidence=["The model/tool-selection trace explaining the dispatch",
                              "Audio or playout confirmation of when farewells and disconnection occurred"],
        ),
        next_steps=["Review the recorded closing instruction and tool availability at dispatch.",
                    "Gate end_call on an explicit completed closing state, then repeat the scenario.",
                    "Compare dispatch, terminal status, transcript commitment and audio/playout timing separately."],
    )]
