"""Local JSON storage: one normalized session per file."""

import os
from pathlib import Path

from .models import Session, SessionSummary

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "sessions"


def data_dir() -> Path:
    return Path(os.getenv("AGENTDOCTOR_DATA_DIR", DEFAULT_DATA_DIR))


def save_session(session: Session) -> Path:
    path = data_dir() / f"{session.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(indent=2))
    return path


def load_session(session_id: str) -> Session | None:
    path = data_dir() / f"{session_id}.json"
    if not path.is_file() or path.parent != data_dir():
        return None
    return Session.model_validate_json(path.read_text())


def list_sessions() -> list[SessionSummary]:
    sessions = [Session.model_validate_json(p.read_text()) for p in sorted(data_dir().glob("*.json"))]
    return [SessionSummary.model_validate(s.model_dump()) for s in sessions]
