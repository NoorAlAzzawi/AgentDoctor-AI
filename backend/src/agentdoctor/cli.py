"""Command line: import sessions, serve the API, export the API contract."""

import argparse
import json
from pathlib import Path

from .ingest import PrivacyError, load_livekit_report
from .store import data_dir, save_session


def cmd_import(args: argparse.Namespace) -> None:
    """Import every <dir>/*/session_report.json listed in a manifest (id, title, notes)."""
    manifest = json.loads(Path(args.manifest).read_text())
    blocked = manifest.get("blocked_terms", [])
    for entry in manifest["sessions"]:
        report = Path(args.source) / entry["source"] / "session_report.json"
        try:
            session = load_livekit_report(
                report, session_id=entry["id"], title=entry["title"],
                notes=entry.get("notes"), blocked_terms=blocked,
            )
        except PrivacyError as e:
            raise SystemExit(str(e))
        path = save_session(session)
        print(f"imported {entry['source']} -> {path.name} ({len(session.turns)} turns, "
              f"{len(session.events)} events)")
    print(f"data dir: {data_dir()}")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("agentdoctor.api:app", host="127.0.0.1", port=args.port)


def cmd_export_openapi(args: argparse.Namespace) -> None:
    from .api import app

    Path(args.out).write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"wrote {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentdoctor")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("import", help="import LiveKit session reports listed in a manifest")
    p.add_argument("source", help="folder containing <session>/session_report.json")
    p.add_argument("--manifest", required=True, help="JSON manifest of sessions to import")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("serve", help="run the API on http://127.0.0.1:<port>")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("export-openapi", help="write the OpenAPI schema")
    p.add_argument("--out", default="../contracts/openapi.json")
    p.set_defaults(func=cmd_export_openapi)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
