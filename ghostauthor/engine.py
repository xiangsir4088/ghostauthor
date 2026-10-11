"""The deterministic core.

Pipeline: normalize -> match -> reconstruct sessions -> aggregate.

This module is a pure function: (commits, roster, options) -> Report.
No IO, no rendering, no wall clock. Same input always yields the same
Report — that is the reproducibility invariant, and it is tested.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .models import (ALL_BUCKETS, BUCKET_AGENT, BUCKET_COSIGNED,
                     BUCKET_UNSIGNED, AgentStats, CommitInfo, Finding,
                     Report, Session)
from .roster import Roster

DEFAULT_SESSION_GAP_MINUTES = 360

_BODY_COAUTH_RE = re.compile(r"^\s*co-authored-by\s*:", re.IGNORECASE)


def build_report(commits: List[CommitInfo],
                 roster: Roster,
                 repo_path: str = ".",
                 ref: str = "HEAD",
                 session_gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
                 max_ai_ratio: Optional[float] = None,
                 since: Optional[int] = None,
                 until: Optional[int] = None) -> Report:
    if session_gap_minutes <= 0:
        raise ValueError("session-gap-invalid: must be positive minutes")
    if max_ai_ratio is not None and not (0.0 <= max_ai_ratio <= 1.0):
        raise ValueError("gate-limit-invalid: must be within [0, 1]")
    if since is not None and until is not None and since >= until:
        raise ValueError("window-invalid: since must be before until")
    if not commits:
        raise ValueError("no-commits: nothing to aggregate")

    # Time window: since INCLUSIVE, until EXCLUSIVE. Boundary semantics are
    # pinned by tests — the same discipline as the gate boundary.
    windowed = [c for c in commits
                if (since is None or c.authored_at >= since)
                and (until is None or c.authored_at < until)]
    if not windowed:
        raise ValueError("window-empty: no commits within the window")

    # Deterministic order: commit data only (authored_at + sha), never wall clock.
    ordered = sorted(windowed, key=lambda c: (c.authored_at, c.sha))
    total = len(ordered)

    bucket_commits: Dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    bucket_lines: Dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    bucket_shas: Dict[str, List[str]] = {b: [] for b in ALL_BUCKETS}
    agent_commits: Dict[str, List[CommitInfo]] = {}
    findings: List[Finding] = []
    cosigned_evidence: List[str] = []

    for c in ordered:
        matched_ids: List[str] = []
        for t in c.trailers:
            rule = roster.match(t.name, t.email_domain)
            if rule is not None and rule.id not in matched_ids:
                matched_ids.append(rule.id)
            if rule is not None and not t.email:
                findings.append(Finding(
                    rule="trailer-no-email", severity="info",
                    evidence=f"{c.sha[:12]}: {t.raw}",
                    detail="matched by name only; trailer carries no <email>"))

        if not c.trailers:
            bucket = BUCKET_UNSIGNED
        elif matched_ids:
            bucket = BUCKET_AGENT
        else:
            bucket = BUCKET_COSIGNED
            for t in c.trailers:
                cosigned_evidence.append(f"{c.sha[:12]}: {t.raw}")
                findings.append(Finding(
                    rule="cosigned-unknown", severity="warn",
                    evidence=f"{c.sha[:12]}: {t.raw}",
                    detail="Co-Authored-By present but no roster rule matched"))

        body_hits = [ln.strip() for ln in c.message.splitlines()
                     if _BODY_COAUTH_RE.match(ln)]
        if len(body_hits) > len(c.trailers):
            findings.append(Finding(
                rule="trailer-in-body", severity="warn",
                evidence=f"{c.sha[:12]}: {body_hits[len(c.trailers)]}",
                detail="Co-Authored-By line outside the trailer block is prose, "
                       "not metadata — ignored by design"))
        if len(matched_ids) > 1:
            findings.append(Finding(
                rule="multi-agent-commit", severity="info",
                evidence=c.sha[:12],
                detail="commit carries signatures of several agents: "
                       + ", ".join(matched_ids)))

        bucket_commits[bucket] += 1
        bucket_lines[bucket] += c.lines_added + c.lines_removed
        bucket_shas[bucket].append(c.sha)
        for agent_id in matched_ids:
            agent_commits.setdefault(agent_id, []).append(c)

    # Sessions + per-agent stats, in roster order (deterministic output order).
    gap = session_gap_minutes * 60
    sessions: List[Session] = []
    stats: List[AgentStats] = []
    for rule in roster.agents:
        cs = agent_commits.get(rule.id)
        if not cs:
            continue
        cs = sorted(cs, key=lambda c: (c.authored_at, c.sha))
        runs: List[List[CommitInfo]] = []
        cur: List[CommitInfo] = []
        for c in cs:
            if cur and c.authored_at - cur[-1].authored_at > gap:
                runs.append(cur)
                cur = []
            cur.append(c)
        if cur:
            runs.append(cur)
        for run in runs:
            sessions.append(Session(rule.id, run[0].authored_at,
                                    run[-1].authored_at,
                                    [x.sha for x in run]))
        added = sum(c.lines_added for c in cs)
        removed = sum(c.lines_removed for c in cs)
        stats.append(AgentStats(
            agent_id=rule.id,
            display_name=rule.display_name,
            commits=len(cs),
            lines_added=added,
            lines_removed=removed,
            session_count=len(runs),
            longest_session_commits=max(len(r) for r in runs),
            first_ts=cs[0].authored_at,
            last_ts=cs[-1].authored_at,
        ))

    agent_count = bucket_commits[BUCKET_AGENT]
    ratio = agent_count / total
    gate: Optional[Dict] = None
    if max_ai_ratio is not None:
        gate = {"limit": max_ai_ratio, "ratio": round(ratio, 6),
                "breached": ratio > max_ai_ratio}

    window = {"since": since, "until": until} \
        if (since is not None or until is not None) else None
    return Report(
        repo_path=repo_path,
        ref=ref,
        total_commits=total,
        commits_by_bucket=bucket_commits,
        lines_by_bucket=bucket_lines,
        bucket_commit_shas=bucket_shas,
        agents=stats,
        cosigned_evidence=cosigned_evidence,
        sessions=sessions,
        findings=findings,
        ai_ratio=ratio,
        gate=gate,
        window=window,
    )
