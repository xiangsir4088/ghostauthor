"""MCP server: read-only, stdio, zero dependencies.

The server never touches the network, never writes files, and never
executes anything beyond the read-only `git log` inside the repository
it is pointed at. The tool reports; it does not decide and it does not
act. ("永不自动执行" is an invariant, not a promise.)

Transport: newline-delimited JSON-RPC 2.0 over stdio.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .gitparse import GitError, iter_commits
from .report import render_json
from .roster import RosterError, builtin_roster_path, load_roster

PROTOCOL_VERSION = "2025-03-26"

TOOL: Dict[str, Any] = {
    "name": "repo_authorship_report",
    "description": (
        "Deterministic AI code provenance report for a git repository: "
        "which agents signed which commits (Co-Authored-By trailers), "
        "ratios, sessions, and traceable findings. Read-only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repository path (default: current working directory)",
            },
            "all_refs": {
                "type": "boolean",
                "description": "Scan all refs instead of HEAD",
            },
            "session_gap_minutes": {
                "type": "integer",
                "description": f"Session gap in minutes (default {DEFAULT_SESSION_GAP_MINUTES})",
            },
            "max_ai_ratio": {
                "type": "number",
                "description": "Optional 0..1 gate; the result reports it",
            },
        },
        "required": [],
    },
}


def _error_content(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}],
            "isError": True}


def _handle(method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ghostauthor", "version": __version__},
        }
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        name = params.get("name")
        if name != TOOL["name"]:
            return _error_content(f"unknown tool: {name!r}")
        args = params.get("arguments") or {}
        path = args.get("path") or "."
        try:
            roster = load_roster(builtin_roster_path())
            commits = iter_commits(path, all_refs=bool(args.get("all_refs")))
            report = build_report(
                commits, roster,
                repo_path=str(path),
                ref="all" if args.get("all_refs") else "HEAD",
                session_gap_minutes=int(args.get("session_gap_minutes",
                                               DEFAULT_SESSION_GAP_MINUTES)),
                max_ai_ratio=args.get("max_ai_ratio"),
            )
        except (GitError, RosterError, ValueError, TypeError) as exc:
            return _error_content(str(exc))
        return {"content": [{"type": "text", "text": render_json(report)}],
                "isError": False}
    raise ValueError(f"unknown method: {method!r}")


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_id = req.get("id")
        method = req.get("method")
        if method is None or msg_id is None:
            continue  # notification or malformed: nothing to answer
        try:
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "result": _handle(method, req.get("params"))}
        except ValueError as exc:
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": str(exc)}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve()
