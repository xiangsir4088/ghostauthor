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
from .coverage import build_coverage, render_coverage_json
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .evidence import render_evidence_json, select_evidence_commits
from .gitparse import GitError, iter_commits
from .options import (OptionError, resolve_gate, resolve_roster,
                      resolve_scope, resolve_session_gap, resolve_window,
                      split_code)
from .report import render_json
from .roster import Roster

HTML_PATH = Path(__file__).resolve().parent / "web" / "app.html"

# GitError.code -> HTTP status. A missing repo is 404 (nothing to audit
# there); an empty repo is 422 (well-formed request, nothing in it); any
# other git/environment failure is a server-side 500.
_GIT_STATUS = {"not-a-repo": 404, "no-commits": 422}

# A request body larger than this is refused before it is read. The web
# transport is a local, read-only dashboard, but an unbounded Content-Length
# is still an unbounded allocation: refuse the absurd, allow the real.
_MAX_BODY_BYTES = 32 * 1024 * 1024


def _err(code: str, detail: str) -> Dict[str, Any]:
    return {"code": code, "detail": detail}


def _audit_one(entry: Dict[str, Any], roster: Roster, since: Optional[int],
               until: Optional[int], gap: int,
               max_ratio: Optional[float]) -> Dict[str, Any]:
    """Audit one repository entry and return its result dict.

    Per-entry failures (missing path, ref conflict, not-a-repo, empty
    window, ...) are reported INSIDE the entry — they never abort the other
    entry, which is what makes side-by-side comparison useful.
    """
    result: Dict[str, Any] = {"path": entry.get("path"), "ref": None,
                              "report": None, "error": None}

    path = entry.get("path")
    if not path or not isinstance(path, str):
        result.update(status=400, error=_err("missing-path",
                                             "path is required"))
        return result
    try:
        # ONE definition of the scope rules — `options.resolve_scope`, the
        # same function the CLI and the MCP server call. It validates too:
        # a mistyped scope must be rejected, not rendered into an answer
        # that looks authoritative.
        scope = resolve_scope(entry)
    except OptionError as exc:
        result.update(status=400, error=_err(exc.code, exc.detail))
        return result
    result["ref"] = scope.label

    try:
        commits = iter_commits(str(path), **scope.git_kwargs)
        report = build_report(
            commits, roster,
            repo_path=str(Path(path).resolve()),
            ref=scope.label,
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
        # request is well-formed but this repo cannot answer it. The code
        # is the CLI's own code, read off the message prefix — the same
        # code the CLI prints for the same failure.
        code, detail = split_code(str(exc))
        result.update(status=400, error=_err(code, detail))
        return result
    except OSError as exc:
        # An unreadable path (permissions, a vanished mount) is an
        # environment failure, not a verdict: 500, never a silent report.
        result.update(status=500, error=_err("io-error", str(exc)))
        return result

    # Byte-stable: the report served is EXACTLY the CLI's bytes. It is
    # returned as a pre-serialized STRING (like /api/evidence and
    # /api/coverage) rather than re-parsed into a dict: parsing and
    # re-serializing would compact the document and drop the canonical
    # key order, so the served bytes would NOT be the CLI's bytes — and
    # "byte-identical to the CLI" is the whole claim. The client parses
    # the inner document (and downloads these exact bytes).
    result.update(status=200, report=render_json(report))
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
    # A malformed entry is a malformed REQUEST (not a repository-level
    # problem), so it rejects the whole request instead of producing an
    # entry that cannot be rendered.
    if not all(isinstance(e, dict) for e in entries):
        return 400, {"error": _err("bad-entries",
                                   "each entry must be an object")}

    roster_file = params.get("roster")
    roster_dir = params.get("roster_dir")

    # Validation order mirrors the CLI exactly: roster, then window, then
    # gap/gate. A request with two faults must name the same fault on every
    # surface, or "same code for the same request" is only true for
    # single-fault requests.
    try:
        roster = resolve_roster(roster_file, roster_dir)
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    # Shared window bounds (request-level).
    try:
        since, until = resolve_window(params.get("since"), params.get("until"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    # Shared session gap + gate (request-level).
    try:
        gap = resolve_session_gap(params.get("session_gap_minutes",
                                             DEFAULT_SESSION_GAP_MINUTES))
        max_ratio = resolve_gate(params.get("max_ai_ratio"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    results = [_audit_one(e, roster, since, until, gap, max_ratio)
               for e in entries]
    return 200, {"results": results}


def handle_evidence(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Pure request handler for POST /api/evidence -> (HTTP status, body).

    A single-repo endpoint (unlike /api/audit's 1–2 entries), so errors live
    at the TOP LEVEL, not inside an entry. The body is the evidence document,
    built by the SAME selection + render the CLI uses, so it is identical to
    the CLI's `evidence --format json` for the same request: one source of
    truth, three skins.

    Status model (mirrors the exit-code contract):
      400  bad request  — missing path, bad shas, scope-conflict, ref-invalid,
           window-bound-invalid, roster-conflict, roster-error, window-empty
      404  not-a-repo   — nothing to audit there
      422  no-commits / sha-unknown — well-formed request, nothing to show
    """
    path = params.get("path")
    if not path:
        return 400, {"error": _err("missing-path", "path is required")}

    shas = params.get("shas")
    if shas is not None:
        if not isinstance(shas, list) or not all(
                isinstance(s, str) for s in shas):
            return 400, {"error": _err("bad-shas",
                                        "shas must be a list of strings")}
    else:
        shas = []

    # Same order as the CLI: roster, scope, window.
    try:
        roster = resolve_roster(params.get("roster"), params.get("roster_dir"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    try:
        scope = resolve_scope(params, shas_given=bool(shas))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    try:
        since, until = resolve_window(params.get("since"), params.get("until"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    try:
        commits = select_evidence_commits(
            str(path), shas, **scope.git_kwargs, since=since, until=until)
    except GitError as exc:
        status = {"not-a-repo": 404, "no-commits": 422,
                  "sha-unknown": 422}.get(exc.code, 500)
        return status, {"error": _err(exc.code, str(exc))}
    except ValueError as exc:
        # ref-invalid / ref-conflict / window-empty: the request is
        # well-formed but cannot be answered.
        code, detail = split_code(str(exc))
        return 400, {"error": _err(code, detail)}
    except OSError as exc:
        return 500, {"error": _err("io-error", str(exc))}

    # Return the evidence as a pre-serialized STRING (like /api/audit nests
    # the report) so the bytes are exactly the CLI's — not a re-serialization
    # that could reorder keys. The client JSON.parses the inner document.
    return 200, {"evidence": render_evidence_json(commits, roster)}


def handle_coverage(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Pure request handler for POST /api/coverage -> (HTTP status, body).

    The browser's answer to "why is this number so low?". Same selection and
    rendering as `ghostauthor coverage --format json`, returned as a
    pre-serialized STRING so the bytes match the CLI exactly — one source of
    truth, four skins.

    Status model mirrors the exit-code contract:
      400  bad request — missing path, scope conflict, bad window, bad roster
      404  not-a-repo
      422  no-commits — well-formed request, nothing to diagnose
    """
    path = params.get("path")
    if not path:
        return 400, {"error": _err("missing-path", "path is required")}

    # Same order as the CLI: roster, scope, window.
    try:
        roster = resolve_roster(params.get("roster"), params.get("roster_dir"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    try:
        scope = resolve_scope(params)
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    since, until = None, None
    try:
        since, until = resolve_window(params.get("since"), params.get("until"))
    except OptionError as exc:
        return 400, {"error": _err(exc.code, exc.detail)}

    try:
        commits = iter_commits(str(path), **scope.git_kwargs)
    except GitError as exc:
        return _GIT_STATUS.get(exc.code, 500), {"error": _err(exc.code, str(exc))}
    except ValueError as exc:
        code, detail = split_code(str(exc))
        return 400, {"error": _err(code, detail)}
    except OSError as exc:
        return 500, {"error": _err("io-error", str(exc))}

    try:
        cov = build_coverage(
            commits, roster, repo_path=str(Path(str(path)).resolve()),
            ref=scope.label, since=since, until=until)
    except ValueError as exc:
        code, detail = split_code(str(exc))
        return 400, {"error": _err(code, detail)}

    return 200, {"coverage": render_coverage_json(cov)}


class Handler(BaseHTTPRequestHandler):
    server_version = "ghostauthor/" + __version__
    protocol_version = "HTTP/1.1"
    # A client that declares more body bytes than it sends must not hold a
    # worker thread forever. With keep-alive, an unanswered request would
    # park the thread indefinitely; the timeout turns that into a 400.
    timeout = 30

    def _send(self, status: int, body: bytes,
              content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            # Say it out loud when the body was not consumed: the client
            # must not reuse a connection whose read buffer still holds
            # unread request bytes.
            self.send_header("Connection", "close")
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
        if self.path not in ("/api/audit", "/api/evidence", "/api/coverage"):
            self._json(404, {"error": _err("not-found", self.path)})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                # A negative length would reach rfile.read(-1), which reads
                # until EOF and parks the worker until the socket timeout.
                self.close_connection = True
                self._json(400, {"error": _err("bad-body",
                                               "Content-Length cannot be negative")})
                return
            if length > _MAX_BODY_BYTES:
                # The body is deliberately NOT read: refusing the absurd
                # must not allocate it. That leaves those bytes in the
                # socket, so the connection cannot be reused — close it, or
                # the remainder is parsed as the next request line.
                self.close_connection = True
                self._json(413, {"error": _err("body-too-large",
                                               f"request body exceeds "
                                               f"{_MAX_BODY_BYTES} bytes")})
                return
            raw = self.rfile.read(length) if length > 0 else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(params, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": _err("bad-json", str(exc))})
            return
        except OSError as exc:
            # A stalled or truncated body: the client declared more bytes
            # than it sent. Answer, then close the connection — with
            # keep-alive, leaving it open would park the worker thread.
            self.close_connection = True
            self._json(400, {"error": _err("bad-body", str(exc))})
            return
        try:
            if self.path == "/api/evidence":
                status, body = handle_evidence(params)
            elif self.path == "/api/coverage":
                status, body = handle_coverage(params)
            else:
                status, body = handle_audit(params)
        except Exception as exc:  # noqa: BLE001 — last line of defence
            # An unexpected failure must still produce a well-formed HTTP
            # response. With protocol_version HTTP/1.1 (keep-alive), letting
            # an exception escape would drop the connection without a
            # response — the client would hang instead of seeing a 500.
            self._json(500, {"error": _err("internal-error", str(exc))})
            return
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
