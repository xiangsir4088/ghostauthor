"""Web transport: a read-only HTTP UI over the same ledger.

Stdlib only (http.server) — the "an audit tool must run on the machine it
audits" invariant holds for the web version too: no framework, no venv
ritual, no supply chain of its own. The single self-contained app.html is
served from the package; there is no build step and no CDN.

Like mcp_server.py, this module is pure transport: it parses an HTTP
request, calls the SAME iter_commits -> build_report -> render_json
pipeline, and hands back the result. It performs no judgment, no matching,
no recomputation, and executes nothing but the read-only `git log` inside
the repository it is pointed at. The byte-stable source of truth is
render_json: the JSON served here is identical, byte for byte, to the CLI's
`--format json` output for the same repo state.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .gitparse import GitError, iter_commits
from .models import parse_window_value
from .report import render_json
from .roster import (Roster, RosterError, builtin_roster_path, load_roster,
                     load_roster_dir)

HTML_PATH = Path(__file__).resolve().parent / "web" / "app.html"

# GitError.code -> HTTP status. A missing repo is 404 (nothing to audit
# there); an empty repo is 422 (well-formed request, nothing in it); any
# other git/environment failure is a server-side 500.
_GIT_STATUS = {"not-a-repo": 404, "no-commits": 422}


def _err(code: str, detail: str) -> Dict[str, Any]:
    return {"code": code, "detail": detail}


def _label(all_refs: bool, ref: Optional[str]) -> str:
    return "all" if all_refs else (ref or "HEAD")


def _coerce_ratio(value: Any) -> Optional[float]:
    """The optional 0..1 gate. JSON numbers arrive as float; form strings
    are tolerated. None / "" means the gate is off."""
    if value is None or value == "":
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        raise ValueError("gate-limit-invalid: max-ai-ratio must be a "
                         "number in [0, 1]") from None
    if not (0.0 <= ratio <= 1.0):
        raise ValueError("gate-limit-invalid: must be within [0, 1]")
    return ratio


def _audit_one(entry: Dict[str, Any], roster: Roster, since: Optional[int],
               until: Optional[int], gap: int,
               max_ratio: Optional[float]) -> Dict[str, Any]:
    """Audit one repository entry and return its result dict.

    Per-entry failures (missing path, ref conflict, not-a-repo, empty
    window, ...) are reported INSIDE the entry — they never abort the other
    entry, which is what makes side-by-side comparison useful.
    """
    path = entry.get("path")
    all_refs = bool(entry.get("all_refs", False))
    ref = entry.get("ref") or None
    result: Dict[str, Any] = {"path": path, "ref": _label(all_refs, ref),
                              "report": None, "error": None}

    if not path:
        result.update(status=400, error=_err("missing-path",
                                             "path is required"))
        return result
    if all_refs and ref:
        result.update(status=400, error=_err("ref-conflict",
                                             "use all_refs or ref, not both"))
        return result

    try:
        commits = iter_commits(str(path), all_refs=all_refs, ref=ref)
        report = build_report(
            commits, roster,
            repo_path=str(Path(path).resolve()),
            ref=result["ref"],
            session_gap_minutes=gap,
            max_ai_ratio=max_ratio,
            since=since, until=until,
        )
    except GitError as exc:
        result.update(status=_GIT_STATUS.get(exc.code, 500),
                      error=_err(exc.code, str(exc)))
        return result
    except ValueError as exc:
        # window-empty / window-invalid / session-gap / gate-limit: the
        # request is well-formed but this repo cannot answer it.
        result.update(status=400, error=_err("value-error", str(exc)))
        return result

    # Byte-stable: the report served is exactly the CLI's JSON.
    result.update(status=200, report=json.loads(render_json(report)))
    return result


def handle_audit(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Pure request handler: a params dict -> (HTTP status, body dict).

    Deliberately socket-free so the whole API is unit-testable in-process.
    Errors about the SHARED options (roster conflict, bad window bound, bad
    gap, bad gate, bad roster file) reject the whole request with 400;
    errors about a single repository live inside that entry's result, so a
    bad second path never hides the first report.
    """
    entries = params.get("entries")
    if not isinstance(entries, list) or not (1 <= len(entries) <= 2):
        return 400, {"error": _err("bad-entries",
                                   "entries must be a list of 1 or 2")}

    roster_file = params.get("roster")
    roster_dir = params.get("roster_dir")
    if roster_file and roster_dir:
        return 400, {"error": _err("roster-conflict",
                                   "use roster or roster_dir, not both")}

    # Shared window bounds (request-level).
    since = until = None
    try:
        if params.get("since") not in (None, ""):
            since = parse_window_value(str(params["since"]))
        if params.get("until") not in (None, ""):
            until = parse_window_value(str(params["until"]))
    except ValueError as exc:
        return 400, {"error": _err("window-bound-invalid", str(exc))}

    # Shared session gap + gate (request-level).
    try:
        gap = int(params.get("session_gap_minutes",
                             DEFAULT_SESSION_GAP_MINUTES))
    except (TypeError, ValueError):
        return 400, {"error": _err("session-gap-invalid",
                                   "session_gap_minutes must be an integer")}
    if gap <= 0:
        return 400, {"error": _err("session-gap-invalid",
                                   "must be positive minutes")}
    try:
        max_ratio = _coerce_ratio(params.get("max_ai_ratio"))
    except ValueError as exc:
        return 400, {"error": _err("gate-limit-invalid", str(exc))}

    # Load the roster once (request-level).
    try:
        if roster_dir:
            roster = load_roster_dir(roster_dir)
        else:
            roster = load_roster(roster_file or builtin_roster_path())
    except RosterError as exc:
        return 400, {"error": _err("roster-error", str(exc))}

    results = [_audit_one(e, roster, since, until, gap, max_ratio)
               for e in entries]
    return 200, {"results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "ghostauthor/" + __version__
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes,
              content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: Dict[str, Any]) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            if HTML_PATH.is_file():
                self._send(200, HTML_PATH.read_bytes(),
                           "text/html; charset=utf-8")
            else:
                self._json(500, {"error": _err("web-asset-missing",
                                               "app.html not found")})
        elif self.path == "/api/info":
            self._json(200, {"name": "ghostauthor", "version": __version__,
                             "readonly": True, "stdlib_only": True})
        else:
            self._json(404, {"error": _err("not-found", self.path)})

    def do_POST(self) -> None:
        if self.path != "/api/audit":
            self._json(404, {"error": _err("not-found", self.path)})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(params, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": _err("bad-json", str(exc))})
            return
        status, body = handle_audit(params)
        self._json(status, body)

    def log_message(self, *args: Any) -> None:
        # Keep the ledger quiet: no per-request stderr noise.
        pass


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="ghostauthor-web",
        description="Read-only web UI over the ghostauthor ledger. Stdlib "
                    "only; binds 127.0.0.1 by default (local machine only).")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1 — local only)")
    p.add_argument("--port", type=int, default=8321,
                   help="bind port (default 8321)")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor-web {__version__}")
    args = p.parse_args(argv)

    if not HTML_PATH.is_file():
        print(f"warning: {HTML_PATH} not found — serving the API only",
              file=sys.stderr)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ghostauthor-web {__version__} — read-only ledger UI")
    print(f"  serving on  http://{args.host}:{args.port}")
    print("  stdlib only  ·  no LLM  ·  read-only (git log)  ·  Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
