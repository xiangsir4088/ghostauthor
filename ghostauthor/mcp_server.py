"""MCP server: read-only, stdio, zero dependencies.

The server never touches the network, never writes files, and never
executes anything beyond the read-only `git log` inside the repository
it is pointed at. The tool reports; it does not decide and it does not
act. ("永不自动执行" is an invariant, not a promise.)

Two tools were the v1.4 surface; v1.6.1 adds a third read-only tool:

* `repo_authorship_report` — the full audit. Its JSON is byte-identical to
  the CLI's `--format json` for the same repo state and options (the report
  is the single source of truth; the MCP is one skin of it).
* `commit_evidence` — per-commit evidence, byte-identical to the CLI's
  `evidence --format json`. It has NO bundle parameter: a bundle writes
  files, and the server never writes files.
* `adoption_diagnosis` — why the ledger looks the way it does, byte-identical
  to the CLI's `coverage --format json`. Without it an agent could see a 0%
  ratio and had no way to learn that the cause is a missing signature rather
  than a missing convention — the one diagnosis the tool exists to make.

Request semantics (scope, window, roster, session gap, gate) are NOT
parsed here: they come from `options`, the module the CLI and the web
transport call too. One definition of a boundary means the three surfaces
cannot drift apart — which is how they already had (the web audit endpoint
used to lose the CLI's error codes, and spelled the scope flag differently
from its own sibling endpoints).

Transport: newline-delimited JSON-RPC 2.0 over stdio.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import __version__
from .coverage import build_coverage, render_coverage_json
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .evidence import render_evidence_json, select_evidence_commits
from .gitparse import GitError, iter_commits
from .options import (OptionError, Scope, resolve_gate, resolve_roster,
                      resolve_scope, resolve_session_gap, resolve_window)
from .report import render_json

PROTOCOL_VERSION = "2025-03-26"


def _scope(args: Dict[str, Any], *, shas_given: bool = False) -> Scope:
    """The requested scope, resolved through the ONE shared definition.

    `all_refs` is this server's own parameter name (the tool schema is
    frozen and cannot change), but `all` is accepted as a synonym by
    `options.scope_from_mapping` — so a client that learned the web
    transport's spelling still gets the scope it asked for instead of
    silently getting HEAD.

    `shas_given` carries the one rule about WHEN a scope matters: once the
    auditor names the commits explicitly, the scope is irrelevant. That rule
    lives in `options.resolve_scope`, and the CLI and the web transport call
    the same function — so the three surfaces cannot disagree about it.
    """
    return resolve_scope(args, shas_given=shas_given)


def _audit_report(args: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pure audit handler: args dict -> (report_json | None, error | None).

    Mirrors the CLI `main()` option-for-option so the MCP report is
    byte-identical to the CLI's `--format json` for the same repo state, and
    request-level errors carry the same codes. Never raises: every failure is
    returned as (None, "<code>: <msg>").
    """
    path = args.get("path") or "."

    try:
        roster = resolve_roster(args.get("roster"), args.get("roster_dir"))
    except OptionError as exc:
        return None, str(exc)

    # ONE definition of the scope rules — `options.resolve_scope` validates
    # as well as resolving, so this server does not keep its own copy.
    try:
        scope = _scope(args)
    except OptionError as exc:
        return None, str(exc)

    try:
        since, until = resolve_window(args.get("since"), args.get("until"))
    except OptionError as exc:
        return None, str(exc)

    try:
        gap = resolve_session_gap(args.get("session_gap_minutes",
                                          DEFAULT_SESSION_GAP_MINUTES))
    except OptionError as exc:
        return None, str(exc)

    try:
        max_ratio = resolve_gate(args.get("max_ai_ratio"))
    except OptionError as exc:
        return None, str(exc)

    try:
        commits = iter_commits(str(path), **scope.git_kwargs)
    except (GitError, ValueError) as exc:
        # GitError: not-a-repo / no-commits / git-failed. ValueError: a scope
        # problem the pre-check did not catch. Same codes as the CLI.
        return None, str(exc)

    # Byte parity with the CLI: resolved repo path, identical ref label.
    try:
        report = build_report(
            commits, roster,
            repo_path=str(Path(path).resolve()),
            ref=scope.label,
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
        # Types are CHECKED, not coerced (see `options`): the web transport
        # rejects a non-string sha with `bad-shas`, so this server must not
        # quietly turn 123 into "123" and go looking for a commit named
        # "123" — two surfaces, two answers, for the same request.
        if not isinstance(shas, list) or not all(
                isinstance(s, str) for s in shas):
            return None, "bad-shas: shas must be a list of strings"
        shas = list(shas)
    else:
        shas = []

    try:
        roster = resolve_roster(args.get("roster"), args.get("roster_dir"))
    except OptionError as exc:
        return None, str(exc)

    # ONE definition of the scope rules. `shas_given` carries the one rule
    # about WHEN a scope matters: once the auditor names the commits
    # explicitly, the scope is irrelevant — the CLI and the web transport
    # apply the same rule from the same function.
    try:
        scope = _scope(args, shas_given=bool(shas))
    except OptionError as exc:
        return None, str(exc)

    try:
        since, until = resolve_window(args.get("since"), args.get("until"))
    except OptionError as exc:
        return None, str(exc)

    try:
        commits = select_evidence_commits(
            path, shas, **scope.git_kwargs, since=since, until=until)
    except (GitError, ValueError) as exc:
        # GitError: sha-unknown / not-a-repo / no-commits. ValueError:
        # ref-conflict / ref-invalid / window-empty. Same codes as the CLI.
        return None, str(exc)

    return render_evidence_json(commits, roster), None


def _coverage_report(args: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pure adoption-diagnosis handler: args -> (coverage_json | None, error).

    Mirrors the CLI `coverage` command through the SAME option resolution,
    commit selection, `build_coverage` and `render_coverage_json`, so the
    output is byte-identical to `coverage --format json`. Never raises: every
    failure is returned as (None, "<code>: <msg>").

    This is the diagnosis, not an attribution: markers are hints, they never
    change a bucket, and a commit with no marker produces no claim.
    """
    path = args.get("path") or "."

    try:
        roster = resolve_roster(args.get("roster"), args.get("roster_dir"))
    except OptionError as exc:
        return None, str(exc)

    # ONE definition of the scope rules — `options.resolve_scope` validates
    # as well as resolving, so this server does not keep its own copy.
    try:
        scope = _scope(args)
    except OptionError as exc:
        return None, str(exc)

    try:
        since, until = resolve_window(args.get("since"), args.get("until"))
    except OptionError as exc:
        return None, str(exc)

    try:
        commits = iter_commits(str(path), **scope.git_kwargs)
    except (GitError, ValueError) as exc:
        return None, str(exc)

    try:
        coverage = build_coverage(
            commits, roster,
            repo_path=str(Path(path).resolve()),
            ref=scope.label,
            since=since, until=until)
    except ValueError as exc:
        return None, str(exc)
    return render_coverage_json(coverage), None


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

ADOPTION_TOOL: Dict[str, Any] = {
    "name": "adoption_diagnosis",
    "description": (
        "Explain why a repository's AI-provenance ratio is low: which "
        "commits carry a signature the roster does not recognize (a roster "
        "fix) versus which carry no signature at all (a write-path fix). "
        "Known tool markers are reported as hints, never as attributions. "
        "Read-only. The JSON is byte-identical to the CLI's "
        "`coverage --format json` for the same repo and options."
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
                "description": "Scope: all refs instead of HEAD (mutually "
                               "exclusive with ref and base)",
            },
            "ref": {
                "type": "string",
                "description": "Scope: one ref instead of HEAD",
            },
            "base": {
                "type": "string",
                "description": "Scope: git range base..target (mutually "
                               "exclusive with all_refs)",
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

_TOOLS = [TOOL, COMMIT_EVIDENCE_TOOL, ADOPTION_TOOL]


def _error_content(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}],
            "isError": True}


class _InvalidParams(ValueError):
    """The request's SHAPE is wrong (not the method). JSON-RPC -32602."""


def _handle(method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    params = params or {}
    if not isinstance(params, dict):
        raise _InvalidParams("params must be an object")
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
        if not isinstance(args, dict):
            raise _InvalidParams("arguments must be an object")
        if name == TOOL["name"]:
            payload, error = _audit_report(args)
        elif name == COMMIT_EVIDENCE_TOOL["name"]:
            payload, error = _evidence_report(args)
        elif name == ADOPTION_TOOL["name"]:
            payload, error = _coverage_report(args)
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
        # A peer may send anything: a JSON array, a number, a string. One
        # malformed line must not end the session — the server would be
        # gone for every request that follows, not just the bad one.
        if not isinstance(req, dict):
            continue
        msg_id = req.get("id")
        method = req.get("method")
        if method is None or msg_id is None:
            continue  # notification or malformed: nothing to answer
        try:
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "result": _handle(method, req.get("params"))}
        except _InvalidParams as exc:
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32602, "message": str(exc)}}
        except ValueError as exc:
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": str(exc)}}
        except (TypeError, AttributeError, OSError) as exc:
            # Answered as a JSON-RPC error rather than a crash: a dead
            # server cannot serve the next request. OSError is in the list
            # because an unreadable file (a roster on a locked drive) must
            # not take the session down either.
            out = {"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve()
