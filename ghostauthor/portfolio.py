"""Portfolio — the same ledger, aggregated across repositories.

The audit answers a question about one repository. The question an
organisation actually asks is about all of them: *across our codebase, how
much of what ships carries an AI signature, and which agents are producing
it?* Answering that by looping the CLI and summing in a spreadsheet loses
the only property that made the single-repo number trustworthy —
reproducibility.

This module is that aggregation, and it keeps the same discipline:

* **Weighted, never averaged.** The aggregate ratio is
  `sum(agent commits) / sum(commits)`, not the mean of per-repository
  ratios. Averaging ratios lets a three-commit repository outweigh a
  thirty-thousand-commit one; the weighted figure is the only one that
  describes the codebase. Both numbers are reported so the difference is
  visible instead of hidden.
* **One broken repository never hides the rest.** A repository that fails
  (not a repo, no commits in the window, unreadable roster) is recorded in
  `errors` with its message; the portfolio still completes. Enterprise
  scans fail partially all the time; a tool that aborts on the first bad
  path is a tool nobody runs.
* **Deterministic.** Repositories are sorted by resolved path; every
  aggregate is derived from commit data only.
"""
from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .engine import (DEFAULT_SESSION_GAP_MINUTES, build_gate, build_report,
                     compute_ratio)
from .gitparse import GitError, iter_commits
from .models import Report
from .report import HTML_CSS, SCHEMA_VERSION
from .roster import Roster

# Directories that never contain a repository worth scanning and can be
# enormous (a monorepo's node_modules is bigger than the code).
_SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    ".tox", ".mypy_cache", ".pytest_cache", "target", ".idea", ".vscode",
})


@dataclass(frozen=True)
class RepoEntry:
    """One repository's contribution to the portfolio.

    `error` is None for a successful scan. A failed entry carries zeroed
    counts and the exact message, so a reader can tell "no commits" from
    "not a repository" without consulting logs.

    `agents` is (agent_id, display_name, commits) per agent in that
    repository — kept so the portfolio's per-agent totals can be expanded
    back to their sources, the same traceability invariant the single-repo
    report enforces.
    """

    name: str
    path: str
    ref: str
    total_commits: int = 0
    agent_commits: int = 0
    ai_ratio: float = 0.0
    top_agents: List[str] = field(default_factory=list)
    agents: List[Tuple[str, str, int]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class AgentTotal:
    """One agent summed across every successfully scanned repository.

    `repos` is the number of repositories the agent appears in — a cheap
    concentration signal ("one agent in 40 repos" reads differently from
    "40 agents in 40 repos").
    """

    agent_id: str
    display_name: str
    commits: int
    repos: int


@dataclass(frozen=True)
class Portfolio:
    root: str
    entries: List[RepoEntry]
    totals: Dict[str, object]
    by_agent: List[AgentTotal]
    errors: List[Dict[str, str]]
    window: Optional[Dict] = None
    # Same shape and the same boundary rule as the single-repo report:
    # `breached` is `ratio > limit`, so a ratio exactly at the limit PASSES.
    gate: Optional[Dict] = None


def discover_repos(root: str, max_depth: int = 3) -> List[Path]:
    """Find repositories under `root`, deterministically.

    A directory containing `.git` is a repository; its subtree is not
    descended into (a vendored checkout is not part of this portfolio).
    `max_depth` counts directory levels below `root`.
    """
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f"portfolio-path-invalid: {root} is not a directory")
    if (base / ".git").exists():
        return [base]

    found: List[Path] = []
    base_depth = len(base.parts)
    for dirpath, dirnames, _filenames in os.walk(base):
        current = Path(dirpath)
        if (current / ".git").exists():
            found.append(current)
            dirnames[:] = []
            continue
        if len(current.parts) - base_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
    return sorted(found)


def read_repo_list(list_path: str) -> List[Path]:
    """Read a repositories file: one path per line, `#` comments allowed.

    Relative paths resolve against the list file's directory, not the
    process cwd — a list committed next to a checkout keeps working from
    anywhere, which is the whole point of keeping a list.
    """
    p = Path(list_path).resolve()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"portfolio-list-unreadable: {exc}") from exc
    out: List[Path] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        candidate = Path(entry)
        out.append((candidate if candidate.is_absolute()
                    else p.parent / candidate).resolve())
    if not out:
        raise ValueError(f"portfolio-list-empty: {list_path} lists no paths")
    return out


def _entry(report: Report, name: str, path: str, ref: str) -> RepoEntry:
    agents = [(a.agent_id, a.display_name, a.commits) for a in report.agents]
    return RepoEntry(
        name=name, path=path, ref=ref,
        total_commits=report.total_commits,
        agent_commits=report.commits_by_bucket["agent"],
        ai_ratio=report.ai_ratio,
        top_agents=[a.agent_id for a in
                    sorted(report.agents, key=lambda a: (-a.commits, a.agent_id))],
        agents=agents,
    )


def aggregate_totals(entries: Sequence[RepoEntry]) -> Dict[str, object]:
    """Weighted totals over the successfully scanned entries.

    Split out as a pure function on purpose: the difference between a
    weighted aggregate and a mean of ratios is the single most likely
    arithmetic mistake in a portfolio feature, so it is pinned by tests
    without needing git.

    Weighted:  sum(agent commits) / sum(commits)      <- describes the codebase
    Unweighted: mean of per-repository ratios         <- describes the repos
    Both are returned; neither is allowed to hide the other.
    """
    ok = [e for e in entries if e.error is None]
    total_commits = sum(e.total_commits for e in ok)
    total_agent = sum(e.agent_commits for e in ok)
    return {
        "repositories": len(entries),
        "scanned": len(ok),
        "failed": len(entries) - len(ok),
        "total_commits": total_commits,
        "agent_commits": total_agent,
        "ai_ratio": compute_ratio(total_agent, total_commits),
        "mean_repo_ratio": (sum(e.ai_ratio for e in ok) / len(ok)
                            if ok else 0.0),
    }


def aggregate_agents(entries: Sequence[RepoEntry]) -> List[AgentTotal]:
    """Sum each agent across repositories, from the per-repo breakdown."""
    commits: Dict[str, int] = {}
    repos: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for entry in entries:
        if entry.error is not None:
            continue
        for agent_id, name, count in entry.agents:
            commits[agent_id] = commits.get(agent_id, 0) + count
            repos[agent_id] = repos.get(agent_id, 0) + 1
            display[agent_id] = name
    return [
        AgentTotal(agent_id=aid, display_name=display.get(aid, aid),
                   commits=count, repos=repos[aid])
        for aid, count in sorted(commits.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def gate_result(ratio: float, limit: Optional[float]) -> Optional[Dict]:
    """The organisation-level gate verdict, or None when no limit was given.

    Delegates to `engine.build_gate` — the SAME function the single-repo
    report uses — so the boundary rule (`breached = ratio > limit`, exactly
    at the limit PASSES) and the reported ratio live in one place. It used
    to be a copy, and the copy compared a ROUNDED ratio while the report
    compared the exact one: the same boundary could then give two
    different verdicts.

    Kept pure and separate from `build_portfolio` so the boundary can be
    pinned without git.
    """
    return build_gate(ratio, limit)


def build_portfolio(paths: Sequence[Path], roster: Roster,
                    ref: str = "HEAD",
                    session_gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
                    since: Optional[int] = None,
                    until: Optional[int] = None,
                    root: str = ".",
                    max_ai_ratio: Optional[float] = None) -> Portfolio:
    """Scan each path and aggregate. Never raises for a single bad repo."""
    entries: List[RepoEntry] = []
    errors: List[Dict[str, str]] = []

    # The gate limit is validated HERE, before any repository is scanned.
    # Validated inside the per-repository scan instead, an invalid limit
    # was caught per repo and recorded as "this repository failed" — so
    # every repository failed and the operator saw `portfolio-empty:
    # every repository failed to scan` instead of `gate-limit-invalid`.
    if max_ai_ratio is not None and not (0.0 <= max_ai_ratio <= 1.0):
        raise ValueError("gate-limit-invalid: must be within [0, 1]")

    for repo in paths:
        repo_path = str(Path(repo).resolve())
        name = Path(repo).name or repo_path
        try:
            commits = iter_commits(str(repo), ref=ref)
            report = build_report(
                commits, roster, repo_path=repo_path, ref=ref,
                session_gap_minutes=session_gap_minutes,
                since=since, until=until)
        except (GitError, ValueError, OSError) as exc:
            entries.append(RepoEntry(name=name, path=repo_path, ref=ref,
                                     error=str(exc)))
            errors.append({"path": repo_path, "error": str(exc)})
            continue
        entries.append(_entry(report, name, repo_path, ref))

    totals = aggregate_totals(entries)
    gate = gate_result(float(totals["ai_ratio"]), max_ai_ratio)

    return Portfolio(
        root=str(Path(root).resolve()), entries=entries,
        totals=totals, by_agent=aggregate_agents(entries),
        errors=errors, gate=gate,
        window=({"since": since, "until": until}
                if (since is not None or until is not None) else None))


def render_portfolio_json(pf: Portfolio) -> str:
    payload = {
        "schema_version": "1",
        "root": pf.root,
        "window": pf.window,
        "gate": pf.gate,
        "totals": pf.totals,
        "repositories": [
            {"name": e.name, "path": e.path, "ref": e.ref,
             "total_commits": e.total_commits,
             "agent_commits": e.agent_commits, "ai_ratio": e.ai_ratio,
             "top_agents": e.top_agents,
             "agents": [{"id": aid, "display_name": disp, "commits": count}
                        for aid, disp, count in e.agents],
             "error": e.error}
            for e in pf.entries
        ],
        "by_agent": [
            {"agent_id": a.agent_id, "display_name": a.display_name,
             "commits": a.commits, "repos": a.repos}
            for a in pf.by_agent
        ],
        "errors": pf.errors,
    }
    return json.dumps(payload, sort_keys=True, indent=2,
                      ensure_ascii=False) + "\n"


def render_portfolio_text(pf: Portfolio) -> str:
    t = pf.totals
    out: List[str] = []
    out.append(f"ghostauthor portfolio — {pf.root}")
    out.append(f"  repositories: {t['scanned']} scanned, {t['failed']} failed")
    out.append(f"  aggregate: {t['agent_commits']} of {t['total_commits']} "
               f"commits agent-signed — {float(t['ai_ratio']):.1%}")
    out.append(f"  mean per-repo ratio: {float(t['mean_repo_ratio']):.1%} "
               f"(unweighted; the aggregate above is the one that describes "
               f"the codebase)")
    if pf.gate is not None:
        verdict = "BREACHED" if pf.gate["breached"] else "PASS"
        out.append(f"  gate: limit {float(pf.gate['limit']):.1%} — {verdict} "
                   f"(ratio {float(pf.gate['ratio']):.1%}; a ratio exactly at "
                   f"the limit passes)")
    out.append("")
    name_w = max([len("REPOSITORY")] + [len(e.name) for e in pf.entries])
    out.append(f"  {'REPOSITORY':<{name_w}} {'COMMITS':>8} {'SIGNED':>7} "
               f"{'RATIO':>7}  TOP AGENTS")
    for e in pf.entries:
        if e.error is not None:
            out.append(f"  {e.name:<{name_w}} {'—':>8} {'—':>7} {'—':>7}  "
                       f"ERROR: {e.error}")
            continue
        out.append(f"  {e.name:<{name_w}} {e.total_commits:>8} "
                   f"{e.agent_commits:>7} {e.ai_ratio:>6.1%}  "
                   + ", ".join(e.top_agents[:3]))
    if pf.by_agent:
        out.append("")
        out.append("  agents across the portfolio")
        for a in pf.by_agent:
            out.append(f"    {a.commits:>6} commits in {a.repos:>3} repo(s)  "
                       f"{a.display_name} [{a.agent_id}]")
    if pf.errors:
        out.append("")
        out.append(f"  {len(pf.errors)} repository/repositories could not be "
                   f"scanned (recorded, not skipped silently)")
        for err in pf.errors[:10]:
            out.append(f"    {err['path']}: {err['error']}")
    out.append("")
    return "\n".join(out) + "\n"


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_portfolio_html(pf: Portfolio,
                          title: str = "AI Code Provenance Portfolio") -> str:
    """A self-contained, timestamp-free portfolio brief (same discipline as
    the single-repository HTML report)."""
    from . import __version__

    t = pf.totals
    ratio = float(t["ai_ratio"])
    # The scope actually scanned — not a hardcoded "HEAD": a portfolio
    # produced with `--ref feature/x` states the wrong scope in a document
    # whose whole purpose is to be re-runnable.
    scope_ref = ", ".join(sorted({e.ref for e in pf.entries
                                  if e.error is None})) or "HEAD"
    out: List[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width, '
               'initial-scale=1">')
    out.append(f"<title>{_esc(title)}</title>")
    out.append(f"<style>\n{HTML_CSS}\n</style>")
    out.append("</head><body><main>")

    out.append("<header>")
    out.append('<div class="eyebrow">AI code provenance &middot; '
               "portfolio brief</div>")
    out.append(f"<h1>{_esc(title)}</h1>")
    out.append('<p class="meta">root <code>' + _esc(pf.root) +
               "</code> &nbsp;·&nbsp; " + _esc(t["scanned"]) +
               " repositories &nbsp;·&nbsp; schema <code>v" +
               SCHEMA_VERSION + "</code></p>")
    out.append("</header>")

    out.append('<section class="verdict">')
    out.append(f'<div class="big">{ratio * 100:.1f}%</div>')
    out.append("<div><p><strong>"
               f"{t['agent_commits']} of {t['total_commits']} commits</strong> "
               "across " + _esc(t["scanned"]) + " repositories carry a "
               "roster-recognized AI signature. This is a weighted figure: "
               "every commit counts once, regardless of which repository it "
               "lives in.</p>")
    out.append(f'<p class="sub">Unweighted mean per-repository ratio: '
               f"{float(t['mean_repo_ratio']) * 100:.1f}% — reported so the "
               "two can never be confused.</p>")
    if pf.errors:
        out.append(f'<p class="sub">{len(pf.errors)} repository(ies) could '
                   "not be scanned; they are listed below and excluded from "
                   "the totals.</p>")
    if pf.gate is not None:
        verdict = "BREACHED" if pf.gate["breached"] else "PASS"
        out.append(f'<p class="sub">Gate: limit '
                   f"{float(pf.gate['limit']) * 100:.1f}% — <strong>"
                   f"{verdict}</strong> at {float(pf.gate['ratio']) * 100:.1f}% "
                   "(a ratio exactly at the limit passes).</p>")
    out.append("</div></section>")

    out.append('<section><h2>At a glance</h2><div class="kpis">')
    for number, label in ((t["scanned"], "repositories scanned"),
                          (f"{t['total_commits']:,}", "commits"),
                          (len(pf.by_agent), "distinct agents"),
                          (len(pf.errors), "scan failures")):
        out.append(f'<div class="kpi"><div class="n">{_esc(number)}</div>'
                   f'<div class="l">{_esc(label)}</div></div>')
    out.append("</div></section>")

    out.append("<section><h2>Repositories</h2><table><thead><tr>"
               "<th>repository</th><th>ref</th>"
               '<th class="num">commits</th><th class="num">signed</th>'
               '<th class="num">ratio</th><th>top agents</th>'
               "</tr></thead><tbody>")
    for e in pf.entries:
        if e.error is not None:
            out.append(f"<tr><td>{_esc(e.name)}</td><td>{_esc(e.ref)}</td>"
                       '<td class="num">—</td><td class="num">—</td>'
                       '<td class="num">—</td>'
                       f'<td><span class="tag warn">error</span> '
                       f"{_esc(e.error)}</td></tr>")
            continue
        out.append(f"<tr><td>{_esc(e.name)}</td><td>{_esc(e.ref)}</td>"
                   f'<td class="num">{e.total_commits}</td>'
                   f'<td class="num">{e.agent_commits}</td>'
                   f'<td class="num">{e.ai_ratio * 100:.1f}%</td>'
                   f"<td>{_esc(', '.join(e.top_agents[:4]) or '—')}</td>"
                   "</tr>")
    out.append("</tbody></table></section>")

    if pf.by_agent:
        out.append("<section><h2>Agents across the portfolio</h2>"
                   '<table><thead><tr><th>agent</th><th>id</th>'
                   '<th class="num">commits</th><th class="num">repos</th>'
                   "</tr></thead><tbody>")
        for a in pf.by_agent:
            out.append(f"<tr><td>{_esc(a.display_name)}</td>"
                       f"<td><code>{_esc(a.agent_id)}</code></td>"
                       f'<td class="num">{a.commits}</td>'
                       f'<td class="num">{a.repos}</td></tr>')
        out.append("</tbody></table>")
        out.append('<p class="note">Per-agent counts overlap across commits '
                   "(one commit can carry several signatures); repository "
                   "totals above are deduplicated by commit.</p>")
        out.append("</section>")

    if pf.errors:
        out.append("<section><h2>Scan failures</h2>")
        for err in pf.errors:
            out.append(f'<div class="finding"><span class="tag warn">error</span>'
                       f'<div><div class="ev">{_esc(err["path"])}</div>'
                       f"<div>{_esc(err['error'])}</div></div></div>")
        out.append("</section>")

    out.append("<section><h2>Method &amp; reproducibility</h2><dl>")
    for key, value in (
            ("Source of truth", "git commit messages — the "
                                "<code>Co-Authored-By:</code> trailer block only"),
            ("Aggregate", "weighted: sum of agent-signed commits / sum of "
                          "commits (never a mean of ratios)"),
            ("Not used", "no LLM, no network, no style classifier, no wall clock"),
            ("Scope", f"{_esc(t['scanned'])} repositories at "
                      f"<code>{_esc(scope_ref)}</code>"),
            ("Regenerate", "<code>ghostauthor portfolio &lt;root&gt; "
                           "--format html</code>"),
            ("Tool", f"ghostauthor {_esc(__version__)}, portfolio schema "
                     f"v1")):
        out.append(f"<dt>{key}</dt><dd>{value}</dd>")
    out.append("</dl>")
    out.append('<p class="note">No generation timestamp: regenerating on the '
               "same repository state yields the same bytes.</p>")
    out.append("</section>")

    out.append("<footer>Generated by ghostauthor — deterministic AI code "
               "provenance ledger. Unsigned &ne; human.</footer>")
    out.append("</main></body></html>")
    return "\n".join(out) + "\n"
