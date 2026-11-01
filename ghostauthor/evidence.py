"""Per-commit evidence (v1.2): why the audit attributed a commit this way.

The audit reports buckets and counts; evidence answers the auditor's
next question — for THIS commit, which roster rule matched which
trailer, and why. It consumes `engine.attribute_commit`, the same
function `build_report` uses, so the audit's bucket and the per-sha
evidence can never disagree about a commit: they are the same code.

Pure: no IO, no wall clock. The CLI writes the optional bundle
(report + evidence + roster snapshot + manifest). The JSON shape is
provisional in v1.2 and is frozen in v1.3 alongside the report schema
(additive within v1.2.x; renaming keys or changing semantics is not
allowed before the freeze).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from . import __version__
from .engine import attribute_commit
from .models import CommitInfo
from .roster import Roster


def build_evidence(commits: List[CommitInfo], roster: Roster) -> List[Dict]:
    """One evidence entry per commit, in the given order (the caller
    picks it: (authored_at, sha) for a scan, input order for explicit
    shas). Same input order => same bytes."""
    entries: List[Dict] = []
    for c in commits:
        bucket, matched_ids, per = attribute_commit(c, roster)
        entries.append({
            "agents": matched_ids,
            "attribution": bucket,
            "authored_at": c.authored_at,
            "author": f"{c.author_name} <{c.author_email}>",
            "sha": c.sha,
            "subject": c.message.split("\n", 1)[0] if c.message else "",
            "trailers": [
                {
                    "agent": rule.id if rule else None,
                    "email": t.email,
                    "match_reason": reason if rule else None,
                    "name": t.name,
                    "raw": t.raw,
                }
                for t, (rule, reason) in zip(c.trailers, per)
            ],
        })
    return entries


def evidence_payload(commits: List[CommitInfo], roster: Roster) -> Dict:
    """The JSON document (shape: provisional in v1.2, frozen in v1.3)."""
    return {
        "agents_in_roster": len(roster.agents),
        "commits": len(commits),
        "evidences": build_evidence(commits, roster),
        "roster_source": roster.source,
    }


def render_evidence_json(commits: List[CommitInfo], roster: Roster) -> str:
    """Byte-stable: fixed indent, sort_keys, trailing newline — the same
    discipline as the frozen report JSON."""
    return json.dumps(evidence_payload(commits, roster), indent=2,
                      sort_keys=True, ensure_ascii=False) + "\n"


def _iso(ts: int) -> str:
    """Deterministic UTC rendering of an authored_at epoch (no wall
    clock: the instant comes from the commit itself)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def render_evidence_text(commits: List[CommitInfo], roster: Roster) -> str:
    lines: List[str] = []
    for e in build_evidence(commits, roster):
        lines.append(f"evidence: {e['sha'][:12]} {e['subject']}")
        lines.append(f"  authored: {_iso(e['authored_at'])} by {e['author']}")
        if e["trailers"]:
            lines.append(f"  trailers ({len(e['trailers'])}):")
            for t in e["trailers"]:
                lines.append(f"    {t['raw']}")
                if t["agent"]:
                    lines.append(f"      -> roster agent {t['agent']!r} "
                                 f"({t['match_reason']})")
                else:
                    lines.append("      -> no roster rule matched")
        else:
            lines.append("  trailers: none")
        if e["agents"]:
            lines.append(f"  attribution: {e['attribution']} — "
                         f"agents: {', '.join(e['agents'])}")
        else:
            lines.append(f"  attribution: {e['attribution']}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def build_bundle(repo_path: str, scope: str, window_desc: str,
                 commits: List[CommitInfo], roster: Roster,
                 roster_files: List[Path], report_json: str,
                 evidence_json: str) -> Tuple[str, Dict[str, bytes]]:
    """Assemble the reproducible evidence bundle, purely:
    (manifest text, {relative path: bytes}).

    No wall clock anywhere: the manifest is a function of (repo path as
    given, scope, window, commits, roster files, payloads), so re-running
    on the same repository state yields the same bytes — that is what
    makes the bundle auditable. `repo_path` is the path the user gave
    (never resolved), because a resolved absolute path would leak the
    auditor's filesystem layout and break cross-machine reproducibility.
    """
    files: Dict[str, bytes] = {}
    for f in sorted(roster_files, key=lambda p: p.name):
        files[f"roster/{f.name}"] = f.read_bytes()
    files["report.json"] = report_json.encode("utf-8")
    files["evidence.json"] = evidence_json.encode("utf-8")
    lines = [
        "ghostauthor-bundle v1",
        f"tool: ghostauthor {__version__}",
        f"repo: {repo_path}",
        f"scope: {scope}",
        f"window: {window_desc}",
        f"commits: {len(commits)}",
        "commit shas (report order):",
    ]
    lines += [f"  {c.sha}" for c in commits]
    lines.append("roster files:")
    for name in sorted(n for n in files if n.startswith("roster/")):
        lines.append(f"  {name}  {hashlib.sha256(files[name]).hexdigest()}")
    lines.append("files:")
    for name in ("evidence.json", "report.json"):
        lines.append(f"  {name}  {hashlib.sha256(files[name]).hexdigest()}")
    return "\n".join(lines) + "\n", files
