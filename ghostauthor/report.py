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
        "window": report.window,
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_RULE_INFO = {
    "cosigned-unknown": (
        "Signature not in roster",
        "A commit carries a Co-Authored-By trailer that no roster rule "
        "matched. Add the agent to the roster to make its provenance "
        "auditable."),
    "trailer-in-body": (
        "Signature-shaped line outside the trailer block",
        "A Co-Authored-By-shaped line was found in the message body, "
        "where git does not treat it as metadata. It is ignored by "
        "design."),
    "trailer-no-email": (
        "Agent signature without email",
        "The signature matched the roster by name only; the trailer "
        "carries no <email>."),
    "multi-agent-commit": (
        "Multiple agents signed one commit",
        "The commit carries signatures of several agents; per-agent "
        "attribution overlaps (the cap invariant)."),
    "ai-ratio-gate": (
        "AI-signed ratio above limit",
        "The agent-signed commit ratio exceeds --max-ai-ratio."),
}


def render_sarif(report: Report) -> str:
    """SARIF 2.1.0: findings as results, so compliance pipelines can
    ingest the ledger without a custom adapter. Byte-stable like the
    JSON report (sort_keys)."""
    from . import __version__
    rules: List[dict] = []
    results: List[dict] = []

    def _rule(rule_id: str) -> None:
        name, short = _RULE_INFO.get(rule_id, (rule_id, ""))
        if not any(r["id"] == rule_id for r in rules):
            rules.append({"id": rule_id,
                          "name": f"ghostauthor.{rule_id}",
                          "shortDescription": {"text": short}})

    for f in report.findings:
        _rule(f.rule)
        results.append({
            "ruleId": f.rule,
            "level": "warning" if f.severity == "warn" else "note",
            "message": {"text": f"{f.evidence} — {f.detail}"},
            "locations": [{
                "logicalLocations": [
                    {"name": f.evidence.split(":", 1)[0].strip(),
                     "kind": "commit"},
                ],
            }],
        })
    if report.gate is not None and report.gate["breached"]:
        _rule("ai-ratio-gate")
        results.append({
            "ruleId": "ai-ratio-gate",
            "level": "error",
            "message": {"text": (
                f"ai ratio {report.gate['ratio']} exceeds limit "
                f"{report.gate['limit']}")},
            "locations": [],
        })

    doc = {
        "$schema": ("https://raw.githubusercontent.com/oasis-tcs/"
                    "sarif-spec/master/Schemata/"
                    "sarif-schema-2.1.0.json"),
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "ghostauthor",
                "version": __version__,
                "informationUri": "https://atomgit.com/xiangsir/ghostauthor",
                "rules": rules,
            }},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


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
