"""The deterministic core.

Pipeline: normalize -> match -> reconstruct sessions -> aggregate.

This module is a pure function: (commits, roster, options) -> Report.
No IO, no rendering, no wall clock. Same input always yields the same
Report — that is the reproducibility invariant, and it is tested.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import (ALL_BUCKETS, BUCKET_AGENT, BUCKET_COSIGNED,
                     BUCKET_UNSIGNED, COAUTHOR_LINE_RE, RULE_COSIGNED_UNKNOWN,
                     RULE_MULTI_AGENT, RULE_TRAILER_IN_BODY,
                     RULE_TRAILER_NO_EMAIL, AgentStats, AuthorStats, CommitInfo,
                     Finding, Report, Session)
from .options import apply_window
from .roster import AgentRule, Roster

DEFAULT_SESSION_GAP_MINUTES = 360


def compute_ratio(numerator: int, denominator: int) -> float:
    """The agent-signed ratio — ONE definition.

    The JSON carries the EXACT float; rounding is the renderers' job. That
    matters because a document that carries the same quantity twice
    (`ai_ratio` and `gate.ratio`) must carry it twice with the SAME value:
    rounding one of them produced a report where `gate.ratio` was
    0.333333 while `ai_ratio` was 0.3333333333333333 — two numbers for one
    quantity in one artifact.
    """
    return numerator / denominator if denominator else 0.0


def build_gate(ratio: float, limit: Optional[float]) -> Optional[Dict]:
    """The gate verdict — ONE definition, shared by the single-repo report
    and the organisation-level portfolio.

    The boundary rule is `breached = ratio > limit`, so a ratio exactly at
    the limit PASSES. The comparison lives here and nowhere else: the
    portfolio used to run it against a ROUNDED ratio while the report ran
    it against the exact one, so the same boundary could give two
    different verdicts (e.g. ratio 1/3 against a limit of 0.333333:
    breached in the report, not breached in the portfolio).
    """
    if limit is None:
        return None
    if not (0.0 <= limit <= 1.0):
        raise ValueError("gate-limit-invalid: must be within [0, 1]")
    return {"limit": limit, "ratio": ratio, "breached": ratio > limit}


def attribute_commit(c: CommitInfo, roster: Roster
                     ) -> Tuple[str, List[str],
                                List[Tuple[Optional[AgentRule], str]]]:
    """The single source of truth for per-commit attribution.

    Returns (bucket, matched agent ids in first-seen order, per-trailer
    (rule, reason-or-"")). `build_report` and the evidence command both
    consume this: the audit's bucket and the per-sha evidence can never
    disagree about a commit, because they are the same code.
    """
    per: List[Tuple[Optional[AgentRule], str]] = []
    matched_ids: List[str] = []
    for t in c.trailers:
        hit = roster.match_detail(t.name, t.email_domain)
        if hit is None:
            per.append((None, ""))
            continue
        rule, reason = hit
        per.append((rule, reason))
        if rule.id not in matched_ids:
            matched_ids.append(rule.id)
    if not c.trailers:
        bucket = BUCKET_UNSIGNED
    elif matched_ids:
        bucket = BUCKET_AGENT
    else:
        bucket = BUCKET_COSIGNED
    return bucket, matched_ids, per


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
    if not commits:
        raise ValueError("no-commits: nothing to aggregate")

    # Time window: since INCLUSIVE, until EXCLUSIVE. The filter AND its two
    # error codes live in `options.apply_window` — one definition, shared
    # with the coverage and evidence surfaces.
    windowed = apply_window(commits, since, until)

    # Deterministic order: commit data only (authored_at + sha), never wall clock.
    ordered = sorted(windowed, key=lambda c: (c.authored_at, c.sha))
    total = len(ordered)

    bucket_commits: Dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    bucket_lines: Dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    bucket_shas: Dict[str, List[str]] = {b: [] for b in ALL_BUCKETS}
    agent_commits: Dict[str, List[CommitInfo]] = {}
    # author x agent cross-tab: (name, email) -> row accumulator. `cats`
    # is the commit's attribution column set (matched agent ids, or the
    # single cosigned/unsigned bucket key), so a multi-agent commit lands
    # in every column — the same cap invariant as AgentStats.
    author_agg: Dict[Tuple[str, str], Dict] = {}
    findings: List[Finding] = []
    cosigned_evidence: List[str] = []

    for c in ordered:
        bucket, matched_ids, per = attribute_commit(c, roster)
        for t, (rule, _reason) in zip(c.trailers, per):
            if rule is not None and not t.email:
                findings.append(Finding(
                    rule=RULE_TRAILER_NO_EMAIL, severity="info",
                    evidence=f"{c.sha[:12]}: {t.raw}",
                    detail="matched by name only; trailer carries no <email>"))
            elif rule is None:
                # An unmatched co-author is worth surfacing on EVERY commit,
                # not only on the ones whose whole bucket is `cosigned`: a
                # commit signed by a known agent AND an unknown party would
                # otherwise hide the unknown party completely — and "is
                # anyone else on this commit?" is exactly the question a
                # reviewer asks. The finding is additive; `cosigned_evidence`
                # keeps its documented meaning (the evidence for the
                # `cosigned` bucket).
                findings.append(Finding(
                    rule=RULE_COSIGNED_UNKNOWN, severity="warn",
                    evidence=f"{c.sha[:12]}: {t.raw}",
                    detail="Co-Authored-By present but no roster rule matched"))

        if bucket == BUCKET_COSIGNED:
            for t in c.trailers:
                cosigned_evidence.append(f"{c.sha[:12]}: {t.raw}")

        # A Co-Authored-By-shaped line that is NOT part of the trailer block
        # is prose: git does not treat it as metadata, and neither do we.
        # The trailer lines are excluded EXPLICITLY — git puts trailers
        # LAST, so a prose mention comes BEFORE them, and counting positions
        # would quote the legitimate trailer instead of the offending prose.
        trailer_lines = set()
        for t in c.trailers:
            for ln in t.raw.split("\n"):
                trailer_lines.add(ln.strip())
        body_hits = [ln.strip() for ln in c.message.splitlines()
                     if COAUTHOR_LINE_RE.match(ln)
                     and ln.strip() not in trailer_lines]
        if body_hits:
            findings.append(Finding(
                rule=RULE_TRAILER_IN_BODY, severity="warn",
                evidence=f"{c.sha[:12]}: {body_hits[0]}",
                detail="Co-Authored-By line outside the trailer block is prose, "
                       "not metadata — ignored by design"))
        if len(matched_ids) > 1:
            findings.append(Finding(
                rule=RULE_MULTI_AGENT, severity="info",
                evidence=c.sha[:12],
                detail="commit carries signatures of several agents: "
                       + ", ".join(matched_ids)))

        bucket_commits[bucket] += 1
        bucket_lines[bucket] += c.lines_added + c.lines_removed
        bucket_shas[bucket].append(c.sha)
        for agent_id in matched_ids:
            agent_commits.setdefault(agent_id, []).append(c)

        # Cross-tab row: one cell per attribution column, deduplicated total.
        row = author_agg.setdefault(
            (c.author_name, c.author_email),
            {"commits": 0, "by_agent": {}, "shas": {}})
        row["commits"] += 1
        cats = matched_ids if matched_ids else (
            [BUCKET_COSIGNED] if c.trailers else [BUCKET_UNSIGNED])
        for cat in cats:
            row["by_agent"][cat] = row["by_agent"].get(cat, 0) + 1
            row["shas"].setdefault(cat, []).append(c.sha)

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

    # Rows in (name, email) order; cells already in (authored_at, sha)
    # order because the loop runs over `ordered`.
    authors = [
        AuthorStats(name=name, email=email, commits=row["commits"],
                    by_agent=row["by_agent"], by_agent_shas=row["shas"])
        for (name, email), row in sorted(author_agg.items())
    ]

    agent_count = bucket_commits[BUCKET_AGENT]
    ratio = compute_ratio(agent_count, total)
    gate = build_gate(ratio, max_ai_ratio)

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
        authors=authors,
        cosigned_evidence=cosigned_evidence,
        sessions=sessions,
        findings=findings,
        ai_ratio=ratio,
        gate=gate,
        window=window,
    )
