"""HTTP API. Contract: contracts/API.md (generated schema: contracts/openapi.json)."""

import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .compare import compare_sessions
from .detectors import DETECTORS, run_detectors
from .models import Comparison, Finding, FindingsResponse, Session, SessionSummary
from .store import list_sessions, load_session

app = FastAPI(title="AgentDoctor AI", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _get(session_id: str) -> Session:
    session = load_session(session_id) if _ID.match(session_id) else None
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return session


def _findings(session: Session) -> list[Finding]:
    return run_detectors(session)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "detectors": [d.__name__ for d in DETECTORS]}


@app.get("/api/sessions", response_model=list[SessionSummary])
def sessions() -> list[SessionSummary]:
    return list_sessions()


@app.get("/api/sessions/{session_id}", response_model=Session)
def session(session_id: str) -> Session:
    return _get(session_id)


@app.get("/api/sessions/{session_id}/findings", response_model=FindingsResponse)
def findings(session_id: str) -> FindingsResponse:
    s = _get(session_id)
    return FindingsResponse(
        session_id=s.id, detectors_run=[d.__name__ for d in DETECTORS], findings=_findings(s)
    )


@app.get("/api/compare", response_model=Comparison)
def compare(a: str, b: str) -> Comparison:
    sa, sb = _get(a), _get(b)
    return compare_sessions(sa, sb, _findings(sa), _findings(sb))
