"""Command line: argument parsing + file IO + exit codes.

Exit codes are the CI contract:
  0  report produced (gate, if any, not breached)
  2  environment or usage error (not a repo, no commits, bad roster, bad args)
  4  gate breached (agent-signed ratio above --max-ai-ratio)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .gitparse import GitError, iter_commits
from .report import render_json, render_markdown, render_sarif, render_text
from .roster import (RosterError, builtin_roster_path, load_roster,
                     load_roster_dir)


def _parse_window_value(value: str) -> int:
    """Deterministic window-bound parsing: epoch seconds, YYYY-MM-DD
    (UTC midnight) or ISO 8601 datetime. Naive datetimes are UTC. No
    wall clock is involved — the bound is pure data."""
    if value.isdigit():
        return int(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"window-bound-invalid: cannot parse {value!r} (use epoch "
            "seconds, YYYY-MM-DD or ISO 8601)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor",
        description="Deterministic AI code provenance ledger: who signed the "
                    "commits in your repo — auditable, reproducible, no LLM.")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("--all", action="store_true",
                   help="scan all refs instead of HEAD")
    p.add_argument("--format", choices=["text", "json", "markdown", "sarif"],
                   default="text", dest="fmt")
    p.add_argument("--out", help="write the report to FILE instead of stdout")
    p.add_argument("--roster",
                   help="path to a custom roster JSON (default: builtin)")
    p.add_argument("--roster-dir",
                   help="directory of roster JSON files to merge (sorted "
                        "filename order; mutually exclusive with --roster)")
    p.add_argument("--since", metavar="BOUND",
                   help="include commits with authored_at >= BOUND (epoch "
                        "seconds, YYYY-MM-DD or ISO 8601; naive values = UTC)")
    p.add_argument("--until", metavar="BOUND",
                   help="include commits with authored_at < BOUND (same "
                        "formats; the bound is exclusive)")
    p.add_argument("--session-gap", type=int,
                   default=DEFAULT_SESSION_GAP_MINUTES, metavar="MINUTES",
                   help="max gap between agent commits still counted as one "
                        "session (default: 360)")
    p.add_argument("--max-ai-ratio", type=float, default=None, metavar="X",
                   help="exit 4 if the agent-signed commit ratio exceeds X "
                        "(0..1); off by default")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.roster is not None and args.roster_dir is not None:
        print("error: roster-conflict: use --roster or --roster-dir, "
              "not both", file=sys.stderr)
        return 2

    try:
        if args.roster_dir is not None:
            roster = load_roster_dir(args.roster_dir)
        else:
            roster = load_roster(args.roster or builtin_roster_path())
    except RosterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        since = _parse_window_value(args.since) if args.since is not None else None
        until = _parse_window_value(args.until) if args.until is not None else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        commits = iter_commits(args.path, all_refs=args.all)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = build_report(
            commits, roster,
            repo_path=str(Path(args.path).resolve()),
            ref="all" if args.all else "HEAD",
            session_gap_minutes=args.session_gap,
            max_ai_ratio=args.max_ai_ratio,
            since=since,
            until=until,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.fmt == "json":
        text = render_json(report)
    elif args.fmt == "markdown":
        text = render_markdown(report)
    elif args.fmt == "sarif":
        text = render_sarif(report)
    else:
        text = render_text(report)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    if report.gate is not None and report.gate["breached"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
