"""Command line: argument parsing + file IO + exit codes.

Exit codes are the CI contract:
  0  report produced (gate, if any, not breached)
  2  environment or usage error (not a repo, no commits, bad roster, bad args)
  4  gate breached (agent-signed ratio above --max-ai-ratio)

`ghostauthor sign ...` is the write path (v1.0): an opt-in subcommand that
dry-runs by default and only amends HEAD on explicit `--commit`. It shares
the exit-code discipline (0 ok / 2 error) and never uses exit 4 — it has
no gate. Everything it does is documented in docs/attestation.md.

v1.1 subcommands, same discipline:
  install-hook / uninstall-hook — the repo-level commit-msg hook (opt-in,
    marker-protected; never touches a foreign hook)
  lint-roster — static roster warnings (catch-all patterns, cross-agent
    overlap); exit 0 with or without warnings
  _commit-msg-hook — INTERNAL: what the installed hook execs; do not
    call directly (it is the hook's argv, pinned by install-hook)

v1.2 subcommands, read side (advice, safe unsupervised):
  evidence — per-commit evidence: which roster rule matched each trailer
    and why the audit attributed the commit the way it did (same
    attribution code as the audit); optional --bundle writes a
    reproducible evidence package (report + evidence + roster + manifest)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .engine import DEFAULT_SESSION_GAP_MINUTES, build_report
from .evidence import (build_bundle, render_evidence_json,
                       render_evidence_text)
from .gitparse import GitError, get_commit, iter_commits
from .lint import lint_roster
from .models import parse_window_value
from .report import render_json, render_markdown, render_sarif, render_text
from .roster import (Roster, RosterError, builtin_roster_path, load_roster,
                     load_roster_dir)
from .signer import (SignError, install_hook, sign_head, sign_message_file,
                     uninstall_hook)


# The window-bound parser is pure data logic; it now lives in models.py so
# the CLI and the web transport share one definition. This alias keeps the
# pinned tests/test_cli.py import working unchanged.
_parse_window_value = parse_window_value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor",
        description="Deterministic AI code provenance ledger: who signed the "
                    "commits in your repo — auditable, reproducible, no LLM.")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    ref_group = p.add_mutually_exclusive_group()
    ref_group.add_argument("--all", action="store_true",
                           help="scan all refs instead of HEAD")
    ref_group.add_argument("--ref", metavar="REF",
                           help="scan one ref (branch/tag/commit) instead "
                                "of HEAD")
    p.add_argument("--base", metavar="REF",
                   help="two-branch diff: scan only the commits reachable "
                        "from the target (HEAD or --ref) but not from REF "
                        "(git range REF..target); mutually exclusive with "
                        "--all")
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


def _build_sign_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor sign",
        description="Sign HEAD with a roster agent's Co-Authored-By trailer. "
                    "Opt-in: dry-run by default; --commit actually amends HEAD. "
                    "See docs/attestation.md for the detection/attestation "
                    "boundary this command crosses.")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("--agent", required=True, metavar="ID",
                   help="roster agent id; must exist AND have a `signing` "
                        "section")
    p.add_argument("--roster",
                   help="path to a custom roster JSON (default: builtin)")
    p.add_argument("--roster-dir",
                   help="directory of roster JSON files to merge (sorted "
                        "filename order; mutually exclusive with --roster)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="show what would change and stop (the default)")
    mode.add_argument("--commit", action="store_true",
                      help="actually amend HEAD with the trailer")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def sign_main(argv: Optional[List[str]] = None) -> int:
    args = _build_sign_parser().parse_args(argv)
    roster, error = _load_roster(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    assert roster is not None
    try:
        result = sign_head(args.path, roster, args.agent,
                           apply=args.commit)
    except (SignError, GitError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if result.no_op:
        print(f"already signed by {args.agent!r} ({result.trailer}); "
              "nothing to do")
        return 0
    if result.applied:
        print(f"signed HEAD: {result.old_sha[:12]} -> "
              f"{result.new_sha[:12] if result.new_sha else '?'}")
        print(f"  + {result.trailer}")
        return 0
    print(f"[dry-run] would amend HEAD {result.old_sha[:12]} with:")
    print(f"  + {result.trailer}")
    print("new message:")
    for line in result.new_message.splitlines():
        print(f"  {line}")
    print("(no changes made — pass --commit to apply)")
    return 0


def _build_commit_msg_hook_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor _commit-msg-hook",
        description="INTERNAL: the commit-msg hook's argv (generated by "
                    "install-hook). Signs a commit message FILE in place. "
                    "Do not call directly.")
    p.add_argument("msgfile", help="path to the commit message file "
                                   "(the hook's $1)")
    p.add_argument("--agent", required=True, metavar="ID",
                   help="roster agent id (pinned at install time)")
    p.add_argument("--roster",
                   help="path to the roster JSON pinned at install time")
    p.add_argument("--roster-dir",
                   help="roster directory pinned at install time")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def commit_msg_hook_main(argv: Optional[List[str]] = None) -> int:
    # A non-zero exit aborts the commit (commit-msg contract): conflicts
    # and broken rosters must block, and the user sees the reason.
    args = _build_commit_msg_hook_parser().parse_args(argv)
    roster, error = _load_roster(args)
    if error:
        print(f"ghostauthor: error: {error}", file=sys.stderr)
        return 2
    assert roster is not None
    try:
        trailer, _new, no_op = sign_message_file(args.msgfile, roster,
                                                 args.agent)
    except (SignError, OSError) as exc:
        print(f"ghostauthor: error: {exc}", file=sys.stderr)
        return 2
    if no_op:
        print(f"ghostauthor: commit already signed by {args.agent!r} — "
              "no change")
    else:
        print(f"ghostauthor: signed commit with {trailer}")
    return 0


def _build_install_hook_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor install-hook",
        description="Install the repo-level commit-msg hook: every commit "
                    "is signed at commit time (no amend, no sha rewrite). "
                    "Opt-in — nothing is installed unless this runs. "
                    "Refuses to overwrite a foreign hook.")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("--agent", required=True, metavar="ID",
                   help="roster agent id; must exist AND have a `signing` "
                        "section")
    p.add_argument("--roster",
                   help="path to a custom roster JSON (default: builtin); "
                        "pinned into the generated hook")
    p.add_argument("--roster-dir",
                   help="directory of roster JSON files to merge (sorted "
                        "filename order; mutually exclusive with --roster); "
                        "pinned into the generated hook")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def install_hook_main(argv: Optional[List[str]] = None) -> int:
    args = _build_install_hook_parser().parse_args(argv)
    roster, error = _load_roster(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    assert roster is not None
    try:
        hook_path = install_hook(args.path, args.agent, roster,
                                 roster_path=args.roster,
                                 roster_dir=args.roster_dir)
    except (SignError, GitError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"installed commit-msg hook: {hook_path}")
    print(f"  agent: {args.agent!r} — commits are signed at commit time")
    print(f"  remove: ghostauthor uninstall-hook {args.path}")
    print(f"  bypass one commit: git commit --no-verify")
    return 0


def _build_uninstall_hook_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor uninstall-hook",
        description="Remove the ghostauthor commit-msg hook. Refuses to "
                    "remove a hook it did not install (no marker).")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def uninstall_hook_main(argv: Optional[List[str]] = None) -> int:
    args = _build_uninstall_hook_parser().parse_args(argv)
    try:
        removed = uninstall_hook(args.path)
    except (SignError, GitError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("removed commit-msg hook" if removed
          else "no ghostauthor hook installed — nothing to remove")
    return 0


def _build_lint_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor lint-roster",
        description="Static roster lint: warnings only (catch-all name "
                    "patterns, cross-agent overlap), never rejects. Exit "
                    "0 with or without warnings — the verdict about a "
                    "fragile roster belongs to the human reviewer.")
    p.add_argument("--roster",
                   help="path to a roster JSON (default: builtin)")
    p.add_argument("--roster-dir",
                   help="directory of roster JSON files to merge (sorted "
                        "filename order; mutually exclusive with --roster)")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   dest="fmt")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def lint_roster_main(argv: Optional[List[str]] = None) -> int:
    args = _build_lint_parser().parse_args(argv)
    roster, error = _load_roster(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    assert roster is not None
    findings = lint_roster(roster)
    if args.fmt == "json":
        payload = {
            "agents": len(roster.agents),
            "source": roster.source,
            "warnings": [
                {"agent": f.agent, "detail": f.detail, "pattern": f.pattern,
                 "rule": f.rule} for f in findings
            ],
        }
        sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2,
                                    ensure_ascii=False) + "\n")
        return 0
    print(f"ghostauthor lint — roster {roster.source} — "
          f"{len(roster.agents)} agent(s)")
    if not findings:
        print("  no warnings")
        return 0
    for f in findings:
        print(f"  [warn] {f.rule:<15} {f.agent}: {f.detail}")
    print(f"{len(findings)} warning(s) — advisory only; the roster still "
          f"loads and validates")
    return 0


def _build_evidence_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghostauthor evidence",
        description="Per-commit evidence: which roster rule matched each "
                    "trailer and why the audit attributed the commit the "
                    "way it did. Same attribution code as the audit — "
                    "they can never disagree. The JSON shape is "
                    "provisional in v1.2 (frozen in v1.3).")
    p.add_argument("path", nargs="?", default=".",
                   help="repository path (default: current directory)")
    p.add_argument("shas", nargs="*", metavar="SHA",
                   help="commits to produce evidence for (default: every "
                        "commit in scope; order preserved)")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true",
                       help="scope: all refs instead of HEAD (ignored "
                            "when SHAs are given)")
    scope.add_argument("--ref", metavar="REF",
                       help="scope: one ref instead of HEAD (ignored "
                            "when SHAs are given)")
    p.add_argument("--base", metavar="REF",
                   help="scope: git range REF..target (mutually exclusive "
                        "with --all; ignored when SHAs are given)")
    p.add_argument("--since", metavar="BOUND",
                   help="window bound, same semantics as the audit "
                        "(inclusive)")
    p.add_argument("--until", metavar="BOUND",
                   help="window bound, same semantics as the audit "
                        "(exclusive)")
    p.add_argument("--roster",
                   help="path to a custom roster JSON (default: builtin)")
    p.add_argument("--roster-dir",
                   help="directory of roster JSON files to merge (sorted "
                        "filename order; mutually exclusive with --roster)")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   dest="fmt", help="output format (default: text)")
    p.add_argument("--bundle", metavar="DIR",
                   help="also write a reproducible evidence bundle into "
                        "DIR (report.json + evidence.json + roster/ + "
                        "manifest.txt); DIR must not exist yet")
    p.add_argument("--version", action="version",
                   version=f"ghostauthor {__version__}")
    return p


def evidence_main(argv: Optional[List[str]] = None) -> int:
    args = _build_evidence_parser().parse_args(argv)
    roster, error = _load_roster(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    assert roster is not None

    try:
        since = (_parse_window_value(args.since)
                 if args.since is not None else None)
        until = (_parse_window_value(args.until)
                 if args.until is not None else None)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.shas:
            commits = [get_commit(args.path, s) for s in args.shas]
        else:
            commits = iter_commits(args.path, all_refs=args.all,
                                   ref=args.ref, base=args.base)
    except (GitError, ValueError) as exc:
        # GitError covers sha-unknown (an explicit SHA the repo does not
        # hold) exactly like the audit covers an unknown ref: usage error,
        # exit 2, nothing written.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Window: since INCLUSIVE, until EXCLUSIVE — the same boundary
    # semantics as the audit, so evidence and audit cannot disagree about
    # which commits a window selects.
    if since is not None or until is not None:
        windowed = [c for c in commits
                    if (since is None or c.authored_at >= since)
                    and (until is None or c.authored_at < until)]
        if not windowed:
            print("error: window-empty: no commits within the window",
                  file=sys.stderr)
            return 2
        commits = windowed
    if not args.shas:
        # Scan order: deterministic (authored_at, sha), like the report.
        # Explicit SHAs keep input order: the auditor asks, the tool answers.
        commits = sorted(commits, key=lambda c: (c.authored_at, c.sha))

    if args.bundle is not None:
        bundle_dir = Path(args.bundle)
        if bundle_dir.exists():
            print(f"error: bundle-exists: {bundle_dir} already exists — "
                  "refusing to overwrite evidence", file=sys.stderr)
            return 2
        scope = (f"{args.base}..{args.ref or 'HEAD'}"
                 if (args.base is not None and not args.shas)
                 else ("sha-list" if args.shas
                       else ("all" if args.all else (args.ref or "HEAD"))))
        try:
            # report.json = what the audit says about EXACTLY this
            # selection, through the frozen read-side pipeline.
            report = build_report(
                commits, roster,
                repo_path=str(Path(args.path).resolve()),
                ref=scope, since=since, until=until)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.roster_dir is not None:
            roster_files = sorted(Path(args.roster_dir).glob("*.json"))
        elif args.roster is not None:
            roster_files = [Path(args.roster)]
        else:
            roster_files = [builtin_roster_path()]
        manifest, files = build_bundle(
            repo_path=str(args.path),
            scope=scope,
            window_desc=("none" if (since is None and until is None)
                         else f"since={since if since is not None else 'none'},"
                              f" until={until if until is not None else 'none'}"),
            commits=commits, roster=roster, roster_files=roster_files,
            report_json=render_json(report),
            evidence_json=render_evidence_json(commits, roster))
        try:
            bundle_dir.mkdir(parents=True)
            for rel, data in files.items():
                dest = bundle_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            (bundle_dir / "manifest.txt").write_text(manifest,
                                                     encoding="utf-8")
        except OSError as exc:
            print(f"error: bundle-write-failed: {exc}", file=sys.stderr)
            return 2
        print(f"evidence bundle written to {bundle_dir}: "
              + ", ".join(sorted(list(files) + ["manifest.txt"])))

    if args.fmt == "json":
        sys.stdout.write(render_evidence_json(commits, roster))
    else:
        sys.stdout.write(render_evidence_text(commits, roster))
    return 0


def _load_roster(args: argparse.Namespace) -> Tuple[Optional[Roster],
                                                    Optional[str]]:
    """Shared roster loading: (roster, error). Never raises."""
    if args.roster is not None and args.roster_dir is not None:
        return None, "roster-conflict: use --roster or --roster-dir, not both"
    try:
        if args.roster_dir is not None:
            return load_roster_dir(args.roster_dir), None
        return load_roster(args.roster or builtin_roster_path()), None
    except RosterError as exc:
        return None, str(exc)


def main(argv: Optional[List[str]] = None) -> int:
    # Reports carry raw commit messages (ensure_ascii=False JSON) and
    # errors may carry repo output. When stdout/stderr are redirected,
    # Python encodes with the platform locale (GBK on Chinese Windows)
    # and non-ASCII text mangles or raises; force UTF-8 on both.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if argv is None:
        argv = sys.argv[1:]
    # Subcommand dispatch happens before argparse: the audit contract
    # (positional PATH, all flags) is untouched. Note: a repository
    # literally named after a subcommand is shadowed by it — the reserved
    # names are: sign, evidence, _commit-msg-hook, install-hook,
    # uninstall-hook, lint-roster. Documented; none is a plausible repo
    # name.
    if argv and argv[0] == "sign":
        return sign_main(argv[1:])
    if argv and argv[0] == "evidence":
        return evidence_main(argv[1:])
    if argv and argv[0] == "_commit-msg-hook":
        return commit_msg_hook_main(argv[1:])
    if argv and argv[0] == "install-hook":
        return install_hook_main(argv[1:])
    if argv and argv[0] == "uninstall-hook":
        return uninstall_hook_main(argv[1:])
    if argv and argv[0] == "lint-roster":
        return lint_roster_main(argv[1:])
    args = build_parser().parse_args(argv)

    roster, error = _load_roster(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    assert roster is not None

    if args.all and args.base is not None:
        print("error: ref-conflict: --base needs a single target (HEAD or "
              "--ref), not --all", file=sys.stderr)
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
        commits = iter_commits(args.path, all_refs=args.all, ref=args.ref,
                               base=args.base)
    except (GitError, ValueError) as exc:
        # GitError = repository problem; ValueError = a bad ref (ref-conflict
        # / ref-invalid, reachable via the equals form --ref=-foo). Both are
        # usage/environment errors => exit 2.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = build_report(
            commits, roster,
            repo_path=str(Path(args.path).resolve()),
            ref=(f"{args.base}..{args.ref or 'HEAD'}" if args.base is not None
                 else ("all" if args.all else (args.ref or "HEAD"))),
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
