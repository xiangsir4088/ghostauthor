"""Command line: argument parsing + file IO + exit codes.

Exit codes are the CI contract:
  0  report produced (gate, if any, not breached)
  2  environment or usage error (not a repo, no commits, bad roster, bad args)
  4  gate breached (agent-signed ratio above --max-ai-ratio)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .gitparse import GitError, iter_commits
from .report import render_json, render_markdown, render_text
from .roster import RosterError, builtin_roster_path, load_roster


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor",
        description="Deterministic AI code provenance ledger: who signed the "
                    "commits in your repo — auditable, reproducible, no LLM.")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("--all", action="store_true",
                   help="scan all refs instead of HEAD")
    p.add_argument("--format", choices=["text", "json", "markdown"],
                   default="text", dest="fmt")
    p.add_argument("--out", help="write the report to FILE instead of stdout")
    p.add_argument("--roster",
                   help="path to a custom roster JSON (default: builtin)")
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

    try:
        roster = load_roster(args.roster or builtin_roster_path())
    except RosterError as exc:
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
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.fmt == "json":
        text = render_json(report)
    elif args.fmt == "markdown":
        text = render_markdown(report)
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
