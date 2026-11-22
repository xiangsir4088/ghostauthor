"""Rendering: consumes a Report, never recomputes anything.

Four formats: terminal text, JSON (sort_keys => byte-stable), markdown, and
a self-contained HTML brief (no CDN, no script, no wall clock — the same
repository state yields byte-identical HTML, which is what makes it
admissible as evidence rather than a screenshot).
"""
from __future__ import annotations

import html
import json
from typing import List

from .models import Report


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


# The JSON contract generation. Additive changes (new keys) keep it; a
# rename / deletion / retype / semantic change bumps it. Policy and the
# frozen key set live in docs/schema.md; the machine-checkable form is
# schema/report.schema.json (verify step 18).
SCHEMA_VERSION = "1"


def render_json(report: Report) -> str:
    data = {
        "schema_version": SCHEMA_VERSION,
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
        "authors": [
            {
                "by_agent": a.by_agent,
                "by_agent_shas": a.by_agent_shas,
                "commits": a.commits,
                "email": a.email,
                "name": a.name,
            }
            for a in report.authors
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


def _author_table(report: Report) -> List[str]:
    lines = ["authors x agents:"]
    if not report.authors:
        lines.append("  (none)")
        return lines
    # Columns: agents present (roster order, same as the agents table),
    # then the two non-agent attribution buckets.
    cols = [a.agent_id for a in report.agents] + ["cosigned", "unsigned"]
    cw = max(10, max(len(c) for c in cols) + 2)
    labels = [f"{a.name} <{a.email}>" for a in report.authors]
    aw = max(len("AUTHOR"), max(len(l) for l in labels))
    lines.append(f"  {'AUTHOR':<{aw}}" + "".join(f"{c:>{cw}}" for c in cols)
                 + f"{'TOTAL':>8}")
    for a, label in zip(report.authors, labels):
        cells = "".join(f"{a.by_agent.get(c, 0):>{cw}}" for c in cols)
        lines.append(f"  {label:<{aw}}" + cells + f"{a.commits:>8}")
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
    out.extend(_author_table(report))
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
    if report.authors:
        # agent_id, not display_name: by_agent is keyed by id (the text
        # table and the JSON agree).
        cols = [a.agent_id for a in report.agents] + ["cosigned", "unsigned"]
        out.append("| author | " + " | ".join(cols) + " | total |")
        out.append("|---|" + "---:|" * (len(cols) + 1))
        for a in report.authors:
            label = f"{a.name} <{a.email}>"
            cells = " | ".join(str(a.by_agent.get(c, 0)) for c in cols)
            out.append(f"| {label} | {cells} | {a.commits} |")
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


HTML_CSS = """
:root{--ink:#15181d;--muted:#5b636e;--line:#e2e6eb;--bg:#f5f6f8;
--card:#ffffff;--accent:#1f4e79;--agent:#1f4e79;--cosigned:#b1740f;
--unsigned:#9aa1ab;--warn:#a3341f;--ok:#1c6b45}
*{box-sizing:border-box}
body{margin:0;padding:40px 24px 64px;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",
Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:960px;margin:0 auto}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
color:var(--accent);font-weight:700}
h1{font-size:27px;line-height:1.25;margin:8px 0 6px;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:12.5px;margin:0}
.meta code{background:#eceff3;border-radius:4px;padding:1px 5px;font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:22px 24px;margin-top:16px}
h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;
color:var(--muted);margin:0 0 14px;font-weight:700}
.verdict{display:flex;align-items:flex-start;gap:26px;flex-wrap:wrap}
.big{font-size:52px;font-weight:800;line-height:1;letter-spacing:-.03em;
color:var(--accent);font-variant-numeric:tabular-nums}
.big.warn{color:var(--warn)}
.verdict p{margin:0;max-width:520px;color:#2c3138}
.verdict .sub{color:var(--muted);font-size:13px;margin-top:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:12px;margin-top:16px}
.kpi{background:#fafbfc;border:1px solid var(--line);border-radius:8px;
padding:12px 14px}
.kpi .n{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .l{font-size:11.5px;color:var(--muted);text-transform:uppercase;
letter-spacing:.06em;margin-top:2px}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;
background:#eceff3;margin:4px 0 12px}
.bar span{display:block;height:100%}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;
color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;
margin-right:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);font-weight:700}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fafbfc}
.tag{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;
padding:2px 7px;border-radius:999px;text-transform:uppercase}
.tag.warn{background:#fbe9e5;color:var(--warn)}
.tag.info{background:#e9eff6;color:var(--accent)}
.finding{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);
font-size:13px}
.finding:last-child{border-bottom:none}
.finding .ev{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:12px;color:#333}
dl{display:grid;grid-template-columns:170px 1fr;gap:6px 16px;margin:0;
font-size:12.5px}
dt{color:var(--muted)}
dd{margin:0}
.note{font-size:12.5px;color:var(--muted);margin:14px 0 0;
padding-top:12px;border-top:1px dashed var(--line)}
footer{color:var(--muted);font-size:11.5px;margin-top:22px;text-align:center}
@media print{body{background:#fff;padding:0}
section{border-color:#d8d8d8;break-inside:avoid}}
""".strip()


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _bar_segments(report: Report) -> str:
    total = report.total_commits or 1
    spans = []
    for bucket, colour in (("agent", "var(--agent)"),
                           ("cosigned", "var(--cosigned)"),
                           ("unsigned", "var(--unsigned)")):
        count = report.commits_by_bucket[bucket]
        if count <= 0:
            continue
        width = count / total * 100
        spans.append(f'<span style="width:{width:.4f}%;background:{colour}" '
                     f'title="{_esc(bucket)}: {count}"></span>')
    return "".join(spans)


def render_html(report: Report, title: str = "AI Code Provenance Audit") -> str:
    """A self-contained brief for a reader who will not run the CLI.

    No `<script>`, no external asset, no timestamp: the document is a pure
    function of the Report, so it can be archived next to the JSON and
    regenerated byte-for-byte. `title` is the only free text (white-label
    or engagement name); everything else is measured.
    """
    from . import __version__

    total = report.total_commits
    pct = report.ai_ratio * 100
    breached = bool(report.gate and report.gate["breached"])
    total_lines = sum(report.lines_by_bucket.values())
    warns = [f for f in report.findings if f.severity == "warn"]
    infos = [f for f in report.findings if f.severity != "warn"]

    out: List[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width, '
               'initial-scale=1">')
    out.append(f"<title>{_esc(title)}</title>")
    out.append(f"<style>\n{HTML_CSS}\n</style>")
    out.append("</head>")
    out.append("<body><main>")

    # Header -------------------------------------------------------------
    out.append('<header>')
    out.append('<div class="eyebrow">AI code provenance &middot; audit brief</div>')
    out.append(f"<h1>{_esc(title)}</h1>")
    out.append('<p class="meta">repository <code>'
               f"{_esc(report.repo_path)}</code> &nbsp;·&nbsp; scope <code>"
               f"{_esc(report.ref)}</code> &nbsp;·&nbsp; schema "
               f'<code>v{SCHEMA_VERSION}</code></p>')
    out.append("</header>")

    # Verdict ------------------------------------------------------------
    out.append('<section class="verdict">')
    out.append(f'<div class="big{" warn" if breached else ""}">{pct:.1f}%</div>')
    out.append("<div>")
    out.append(f"<p><strong>{report.commits_by_bucket['agent']} of {total} "
               "commits</strong> in scope carry a signature that the roster "
               "recognizes as an AI agent. The figure is read from "
               "<code>Co-Authored-By:</code> trailers in the commit messages "
               "— it is recorded provenance, not a style estimate.</p>")
    if report.gate is not None:
        verdict = "BREACHED" if breached else "within limit"
        out.append(f'<p class="sub">Policy gate: limit '
                   f"{report.gate['limit'] * 100:.1f}%, measured "
                   f"{report.gate['ratio'] * 100:.1f}% — "
                   f"<strong>{_esc(verdict)}</strong>.</p>")
    else:
        out.append('<p class="sub">No policy gate was applied to this run. '
                   "Pass <code>--max-ai-ratio</code> to turn the number into "
                   "a contract.</p>")
    out.append("</div></section>")

    # KPIs ---------------------------------------------------------------
    out.append('<section><h2>At a glance</h2><div class="kpis">')
    kpis = [
        (f"{total}", "commits in scope"),
        (f"{len(report.agents)}", "agents detected"),
        (f"{total_lines:,}", "lines touched"),
        (f"{len(warns)}", "warnings"),
    ]
    for number, label in kpis:
        out.append(f'<div class="kpi"><div class="n">{_esc(number)}</div>'
                   f'<div class="l">{_esc(label)}</div></div>')
    out.append("</div></section>")

    # Distribution -------------------------------------------------------
    out.append("<section><h2>Attribution</h2>")
    out.append(f'<div class="bar">{_bar_segments(report)}</div>')
    out.append('<div class="legend">')
    for bucket, colour, label in (
            ("agent", "var(--agent)", "agent-signed"),
            ("cosigned", "var(--cosigned)", "cosigned, unrecognized"),
            ("unsigned", "var(--unsigned)", "no signature")):
        count = report.commits_by_bucket[bucket]
        out.append(f'<span><i style="background:{colour}"></i>'
                   f"{_esc(label)} — {count} "
                   f"({_pct(count, total)})</span>")
    out.append("</div>")
    out.append('<table style="margin-top:16px"><thead><tr>'
               '<th>bucket</th><th class="num">commits</th>'
               '<th class="num">share</th><th class="num">lines touched</th>'
               "</tr></thead><tbody>")
    for bucket in ("agent", "cosigned", "unsigned"):
        count = report.commits_by_bucket[bucket]
        out.append(f"<tr><td>{_esc(bucket)}</td>"
                   f'<td class="num">{count}</td>'
                   f'<td class="num">{_pct(count, total)}</td>'
                   f'<td class="num">{report.lines_by_bucket[bucket]:,}</td>'
                   "</tr>")
    out.append("</tbody></table></section>")

    # Agents -------------------------------------------------------------
    if report.agents:
        out.append("<section><h2>Agents</h2><table><thead><tr>"
                   "<th>agent</th><th>id</th><th class=\"num\">commits</th>"
                   '<th class="num">lines +</th><th class="num">lines −</th>'
                   '<th class="num">sessions</th><th class="num">longest</th>'
                   "</tr></thead><tbody>")
        for a in report.agents:
            out.append(f"<tr><td>{_esc(a.display_name)}</td>"
                       f"<td><code>{_esc(a.agent_id)}</code></td>"
                       f'<td class="num">{a.commits}</td>'
                       f'<td class="num">{a.lines_added:,}</td>'
                       f'<td class="num">{a.lines_removed:,}</td>'
                       f'<td class="num">{a.session_count}</td>'
                       f'<td class="num">{a.longest_session_commits}</td>'
                       "</tr>")
        out.append("</tbody></table>")
        out.append('<p class="note">Per-agent counts may overlap: one commit '
                   "can carry several agents' signatures. The bucket totals "
                   "above are deduplicated by commit — that is the cap "
                   "invariant, and it is why the two numbers can differ "
                   "without either being wrong.</p>")
        out.append("</section>")

    # Authors ------------------------------------------------------------
    if report.authors:
        cols = [a.agent_id for a in report.agents] + ["cosigned", "unsigned"]
        out.append("<section><h2>Authors × agents</h2><table><thead><tr>"
                   "<th>author</th>")
        for col in cols:
            out.append(f'<th class="num">{_esc(col)}</th>')
        out.append('<th class="num">total</th></tr></thead><tbody>')
        for a in report.authors:
            out.append(f"<tr><td>{_esc(a.name)} "
                       f"<span style=\"color:var(--muted)\">"
                       f"&lt;{_esc(a.email)}&gt;</span></td>")
            for col in cols:
                out.append(f'<td class="num">{a.by_agent.get(col, 0)}</td>')
            out.append(f'<td class="num"><strong>{a.commits}</strong></td></tr>')
        out.append("</tbody></table></section>")

    # Findings -----------------------------------------------------------
    if report.findings:
        shown = warns + infos
        out.append("<section><h2>Findings</h2>")
        for f in shown[:100]:
            severity = "warn" if f.severity == "warn" else "info"
            out.append('<div class="finding">'
                       f'<span class="tag {severity}">{severity}</span>'
                       f'<div><div class="ev">{_esc(f.rule)} · '
                       f"{_esc(f.evidence)}</div>"
                       f"<div>{_esc(f.detail)}</div></div></div>")
        if len(shown) > 100:
            out.append(f'<p class="note">Showing the first 100 of '
                       f"{len(shown)} findings. The JSON report carries every "
                       "one, each with its commit sha.</p>")
        out.append("</section>")

    # Method -------------------------------------------------------------
    out.append("<section><h2>Method &amp; reproducibility</h2><dl>")
    method = [
        ("Source of truth", "git commit messages — the <code>Co-Authored-By:</code> "
                            "trailer block only"),
        ("Attribution", "deterministic roster rules (name pattern / email domain), "
                        "first match wins"),
        ("Not used", "no LLM, no network, no style classifier, no wall clock"),
        ("Scope", f"<code>{_esc(report.ref)}</code>, {total} commits"),
        ("Regenerate", f"<code>ghostauthor &lt;repo&gt; --ref "
                       f"{_esc(report.ref)} --format html</code>"),
        ("Tool", f"ghostauthor {_esc(__version__)}, report schema "
                 f"v{SCHEMA_VERSION}"),
    ]
    for key, value in method:
        out.append(f"<dt>{key}</dt><dd>{value}</dd>")
    out.append("</dl>")
    out.append('<p class="note">This document contains no generation '
               "timestamp by design. It is a pure function of the repository "
               "state: regenerating it on the same commits yields the same "
               "bytes, which is what lets it sit next to the JSON as "
               "evidence rather than as a screenshot.</p>")
    out.append("</section>")

    out.append("<footer>Generated by ghostauthor — deterministic AI code "
               "provenance ledger. Unsigned &ne; human: the ledger records "
               "signatures, it does not infer authorship.</footer>")
    out.append("</main></body></html>")
    return "\n".join(out) + "\n"
