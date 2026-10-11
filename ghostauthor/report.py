"""Rendering: consumes a Report, never recomputes anything.

Three formats: terminal text, JSON (sort_keys => byte-stable), markdown.
"""
from __future__ import annotations

import json
from typing import List

from .models import Report


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


def render_json(report: Report) -> str:
    data = {
        "ref": report.ref,
        "repo": report.repo_path,
        "total_commits": report.total_commits,
        "commits_by_bucket": report.commits_by_bucket,
        "lines_by_bucket": report.lines_by_bucket,
        "bucket_commit_shas": report.bucket_commit_shas,
        "ai_ratio": report.ai_ratio,
        "agents": [
            {
                "commits": a.commits,
                "display_name": a.display_name,
                "first_ts": a.first_ts,
                "id": a.agent_id,
                "last_ts": a.last_ts,
                "lines_added": a.lines_added,
                "lines_removed": a.lines_removed,
                "longest_session_commits": a.longest_session_commits,
                "sessions": a.session_count,
            }
            for a in report.agents
        ],
        "sessions": [
            {"agent": s.agent_id, "commit_shas": s.commit_shas,
             "end_ts": s.end_ts, "start_ts": s.start_ts}
            for s in report.sessions
        ],
        "cosigned_evidence": report.cosigned_evidence,
        "findings": [
            {"detail": f.detail, "evidence": f.evidence,
             "rule": f.rule, "severity": f.severity}
            for f in report.findings
        ],
        "gate": report.gate,
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _agent_table(report: Report) -> List[str]:
    lines = ["agents:"]
    if not report.agents:
        lines.append("  (none matched the roster)")
        return lines
    lines.append(f"  {'ID':<10} {'NAME':<24} {'COMMITS':>8} {'+ADDED':>8} "
                 f"{'-REMOVED':>9} {'SESSIONS':>9} {'LONGEST':>8}")
    for a in report.agents:
        lines.append(f"  {a.agent_id:<10} {a.display_name:<24} {a.commits:>8} "
                     f"{a.lines_added:>8} {a.lines_removed:>9} "
                     f"{a.session_count:>9} {a.longest_session_commits:>8}")
    return lines


def render_text(report: Report) -> str:
    out: List[str] = []
    out.append("ghostauthor — deterministic AI code provenance ledger")
    out.append(f"repo: {report.repo_path}   ref: {report.ref}")
    out.append("")
    total = report.total_commits
    out.append(f"commits: {total} total")
    for bucket, label in (("agent", "agent-signed"),
                          ("cosigned", "cosigned-unknown"),
                          ("unsigned", "unsigned")):
        n = report.commits_by_bucket[bucket]
        out.append(f"  {label:<16} {n:>5}  ({_pct(n, total)})")
    out.append("")
    total_lines = sum(report.lines_by_bucket.values())
    out.append(f"lines touched: {total_lines} total")
    out.append(f"  " + "  ".join(f"{b} {report.lines_by_bucket[b]}"
                                 for b in ("agent", "cosigned", "unsigned")))
    out.append("")
    out.extend(_agent_table(report))
    if report.gate is not None:
        g = report.gate
        verdict = "BREACHED" if g["breached"] else "pass"
        out.append("")
        out.append(f"gate: ai ratio {g['ratio'] * 100:.1f}% "
                   f"vs limit {g['limit'] * 100:.1f}% — {verdict}")
    if report.findings:
        out.append("")
        out.append(f"findings ({len(report.findings)}):")
        for f in report.findings:
            out.append(f"  [{f.severity:<4}] {f.rule}  {f.evidence}")
            out.append(f"         {f.detail}")
    return "\n".join(out) + "\n"


def render_markdown(report: Report) -> str:
    total = report.total_commits
    out: List[str] = []
    out.append("# ghostauthor report")
    out.append("")
    out.append(f"- repo: `{report.repo_path}`")
    out.append(f"- ref: `{report.ref}`")
    out.append(f"- total commits: {total}")
    out.append(f"- AI-signed ratio: **{_pct(report.commits_by_bucket['agent'], total)}**")
    out.append("")
    out.append("| bucket | commits | lines touched |")
    out.append("|---|---:|---:|")
    for bucket in ("agent", "cosigned", "unsigned"):
        out.append(f"| {bucket} | {report.commits_by_bucket[bucket]} "
                   f"| {report.lines_by_bucket[bucket]} |")
    out.append("")
    if report.agents:
        out.append("| agent | commits | +added | -removed | sessions | longest |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for a in report.agents:
            out.append(f"| {a.display_name} | {a.commits} | {a.lines_added} "
                       f"| {a.lines_removed} | {a.session_count} "
                       f"| {a.longest_session_commits} |")
        out.append("")
    if report.gate is not None:
        g = report.gate
        verdict = "BREACHED" if g["breached"] else "pass"
        out.append(f"gate: ai ratio {g['ratio'] * 100:.1f}% "
                   f"vs limit {g['limit'] * 100:.1f}% — {verdict}")
        out.append("")
    if report.findings:
        out.append(f"## findings ({len(report.findings)})")
        out.append("")
        for f in report.findings:
            out.append(f"- **[{f.severity}] {f.rule}** `{f.evidence}` — {f.detail}")
        out.append("")
    out.append("Every count above is traceable: the JSON report carries the full "
               "commit lists per bucket, per agent and per session.")
    out.append("")
    return "\n".join(out)
