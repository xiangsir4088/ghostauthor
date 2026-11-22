"""MCP server: read-only, stdio, zero dependencies.

The server never touches the network, never writes files, and never
executes anything beyond the read-only `git log` inside the repository
it is pointed at. The tool reports; it does not decide and it does not
act. ("永不自动执行" is an invariant, not a promise.)

Two tools, both pure reads over the SAME pipeline as the CLI:

* `repo_authorship_report` — the full audit. Its JSON is byte-identical to
  the CLI's `--format json` for the same repo state and options (the report
  is the single source of truth; the MCP is one skin of it).
* `commit_evidence` — per-commit evidence, byte-identical to the CLI's
  `evidence --format json`. It has NO bundle parameter: a bundle writes
  files, and the server never writes files.

Transport: newline-delimited JSON-RPC 2.0 over stdio.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .evidence import render_evidence_json, select_evidence_commits
from .gitparse import GitError, iter_commits
from .models import parse_window_value
from .report import render_json
from .roster import (RosterError, builtin_roster_path, load_roster,
                     load_roster_dir)

PROTOCOL_VERSION = "2025-03-26"


def _since_until(args: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Parse the optional window bounds (since inclusive, until exclusive).
    Returns (since, until); raises ValueError (window-bound-invalid) on a bad
    bound. Mirrors the CLI and the web handler exactly."""
    since = until = None
    if args.get("since") not in (None, ""):
        since = parse_window_value(str(args["since"]))
    if args.get("until") not in (None, ""):
        until = parse_window_value(str(args["until"]))
    return since, until


def _scope_validation_error(all_refs: bool, ref: Optional[str],
                            base: Optional[str]) -> Optional[str]:
    """Request-level scope errors, returned as a message or None. Mirrors
    gitparse.iter_commits so a bad scope is rejected here with the same codes
    (ref-conflict / ref-invalid) the CLI produces."""
    if all_refs and ref:
        return "ref-conflict: use all_refs or ref, not both"
    if all_refs and base:
        return "ref-conflict: use all_refs or base, not both"
    for name, value in (("ref", ref), ("base", base)):
        if value is not None and value.startswith("-"):
            return f"ref-invalid: {name} cannot start with '-'"
    return None


def _load_roster_or_error(roster: Any, roster_dir: Any
                          ) -> Tuple[Optional[object], Optional[str]]:
    """Load the roster or return a request-level error message. Mirrors the
    CLI: roster + roster_dir together is roster-conflict; a bad file is
    roster-error. (RosterError subclasses ValueError, so it is caught first.)"""
    if roster and roster_dir:
        return None, "roster-conflict: use roster or roster_dir, not both"
    try:
        if roster_dir:
            return load_roster_dir(roster_dir), None
        return load_roster(roster or builtin_roster_path()), None
    except RosterError as exc:
        return None, f"roster-error: {exc}"


def _audit_report(args: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pure audit handler: args dict -> (report_json | None, error | None).

    Mirrors the CLI `main()` option-for-option so the MCP report is
    byte-identical to the CLI's `--format json` for the same repo state, and
    request-level errors carry the same codes. Never raises: every failure is
    returned as (None, "<code>: <msg>").
    """
    path = args.get("path") or "."
    all_refs = bool(args.get("all_refs"))
    ref = args.get("ref") or None
    base = args.get("base") or None

    roster, error = _load_roster_or_error(args.get("roster"),
                                          args.get("roster_dir"))
    if error:
        return None, error

    if (err := _scope_validation_error(all_refs, ref, base)) is not None:
        return None, err

    try:
        since, until = _since_until(args)
    except ValueError as exc:
        return None, str(exc)

    try:
        gap = int(args.get("session_gap_minutes", DEFAULT_SESSION_GAP_MINUTES))
    except (TypeError, ValueError):
        return None, "session-gap-invalid: session_gap_minutes must be an integer"
    if gap <= 0:
        return None, "session-gap-invalid: must be positive minutes"

    max_ratio = args.get("max_ai_ratio")
    if max_ratio is not None:
        try:
            max_ratio = float(max_ratio)
        except (TypeError, ValueError):
            return None, ("gate-limit-invalid: max-ai-ratio must be a number "
                          "in [0, 1]")
        if not (0.0 <= max_ratio <= 1.0):
            return None, "gate-limit-invalid: must be within [0, 1]"
    else:
        max_ratio = None

    try:
        commits = iter_commits(str(path), all_refs=all_refs, ref=ref, base=base)
    except (GitError, ValueError) as exc:
        # GitError: not-a-repo / no-commits / git-failed. ValueError: a scope
        # problem the pre-check did not catch. Same codes as the CLI.
        return None, str(exc)

    # Byte parity with the CLI: resolved repo path, identical ref label.
    ref_label = (f"{base}..{ref or 'HEAD'}" if base is not None
                 else ("all" if all_refs else (ref or "HEAD")))
    try:
        report = build_report(
            commits, roster,
            repo_path=str(Path(path).resolve()),
            ref=ref_label,
            session_gap_minutes=gap,
            max_ai_ratio=max_ratio,
            since=since, until=until)
    except ValueError as exc:
        # window-empty / window-invalid: the request is well-formed but this
        # repo cannot answer it.
        return None, str(exc)
    return render_json(report), None


def _evidence_report(args: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pure evidence handler: args dict -> (evidence_json | None, error | None).

    Mirrors the CLI `evidence` command through the SAME selection function
    (`select_evidence_commits`) and renderer (`render_evidence_json`), so the
    output is byte-identical to the CLI's `evidence --format json`. No bundle:
    the bundle writes files and the server never writes files. Never raises.
    """
    path = args.get("path") or "."
    shas = args.get("shas")
    if shas is not None:
        if not isinstance(shas, list):
            return None, "bad-shas: shas must be a list of strings"
        shas = [str(s) for s in shas]
    else:
        shas = []
    all_refs = bool(args.get("all_refs"))
    ref = args.get("ref") or None
    base = args.get("base") or None

    roster, error = _load_roster_or_error(args.get("roster"),
                                          args.get("roster_dir"))
    if error:
        return None, error

    # Scope only matters when shas are omitted (the CLI ignores scope once the
    # auditor names the commits).
    if not shas and (err := _scope_validation_error(all_refs, ref, base)) is not None:
        return None, err

    try:
        since, until = _since_until(args)
    except ValueError as exc:
        return None, str(exc)

    try:
        commits = select_evidence_commits(
            path, shas, all_refs=all_refs, ref=ref, base=base,
            since=since, until=until)
    except (GitError, ValueError) as exc:
        # GitError: sha-unknown / not-a-repo / no-commits. ValueError:
        # ref-conflict / ref-invalid / window-empty. Same codes as the CLI.
        return None, str(exc)

    return render_evidence_json(commits, roster), None


TOOL: Dict[str, Any] = {
    "name": "repo_authorship_report",
    "description": (
        "Deterministic AI code provenance report for a git repository: "
        "which agents signed which commits (Co-Authored-By trailers), "
        "ratios, sessions, and traceable findings. Read-only. The JSON is "
        "byte-identical to the CLI's `--format json` for the same repo and "
        "options."
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
                "description": "Scan all refs instead of HEAD (mutually "
                               "exclusive with ref and base)",
            },
            "ref": {
                "type": "string",
                "description": "Scan one ref (branch/tag/commit) instead of "
                               "HEAD (mutually exclusive with all_refs)",
            },
            "base": {
                "type": "string",
                "description": "Two-branch diff: only commits in base..target "
                               "(mutually exclusive with all_refs)",
            },
            "since": {
                "type": ["string", "integer"],
                "description": "Include commits with authored_at >= since "
                               "(epoch seconds, YYYY-MM-DD or ISO 8601; "
                               "inclusive)",
            },
            "until": {
                "type": ["string", "integer"],
                "description": "Include commits with authored_at < until "
                               "(same formats; exclusive)",
            },
            "session_gap_minutes": {
                "type": "integer",
                "description": f"Session gap in minutes (default {DEFAULT_SESSION_GAP_MINUTES})",
            },
            "max_ai_ratio": {
                "type": "number",
                "description": "Optional 0..1 gate; the result reports it",
            },
            "roster": {
                "type": "string",
                "description": "Path to a custom roster JSON (default: builtin; "
                               "mutually exclusive with roster_dir)",
            },
            "roster_dir": {
                "type": "string",
                "description": "Directory of roster JSON files to merge "
                               "(mutually exclusive with roster)",
            },
        },
        "required": [],
    },
}

COMMIT_EVIDENCE_TOOL: Dict[str, Any] = {
    "name": "commit_evidence",
    "description": (
        "Per-commit evidence for a git repository: for each commit, which "
        "roster rule matched each Co-Authored-By trailer and why the audit "
        "attributed the commit the way it did. Same attribution code as the "
        "report, so the two can never disagree. Read-only; the bundle is a "
        "file artifact and lives on the CLI only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repository path (default: current working directory)",
            },
            "shas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Commits to produce evidence for, in order "
                               "(default: every commit in scope)",
            },
            "all_refs": {
                "type": "boolean",
                "description": "Scope: all refs instead of HEAD (ignored when "
                               "shas are given)",
            },
            "ref": {
                "type": "string",
                "description": "Scope: one ref instead of HEAD (ignored when "
                               "shas are given)",
            },
            "base": {
                "type": "string",
                "description": "Scope: git range base..target (ignored when "
                               "shas are given; mutually exclusive with "
                               "all_refs)",
            },
            "since": {
                "type": ["string", "integer"],
                "description": "Window bound, same semantics as the report "
                               "(inclusive)",
            },
            "until": {
                "type": ["string", "integer"],
                "description": "Window bound, same semantics as the report "
                               "(exclusive)",
            },
            "roster": {
                "type": "string",
                "description": "Path to a custom roster JSON (default: builtin; "
                               "mutually exclusive with roster_dir)",
            },
            "roster_dir": {
                "type": "string",
                "description": "Directory of roster JSON files to merge "
                               "(mutually exclusive with roster)",
            },
        },
        "required": [],
    },
}

_TOOLS = [TOOL, COMMIT_EVIDENCE_TOOL]


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
        return {"tools": _TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == TOOL["name"]:
            payload, error = _audit_report(args)
        elif name == COMMIT_EVIDENCE_TOOL["name"]:
            payload, error = _evidence_report(args)
        else:
            return _error_content(f"unknown tool: {name!r}")
        if error is not None:
            return _error_content(error)
        return {"content": [{"type": "text", "text": payload}],
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
