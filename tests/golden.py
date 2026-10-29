"""The frozen golden report: one fixed dataset, one frozen byte sequence.

`tests/goldens/report.json` pins the BYTES of render_json — indentation,
key order, the trailing newline — so a refactor that changes the output
shape (not just the data) goes red. verify.py step 18 re-renders this
dataset and byte-compares.

The dataset is in-memory (no git repo, no wall clock): it exercises every
shape in the schema — all three buckets, findings, a non-breached gate,
a window, per-agent stats, the author x agent cross-tab, a session — so
the golden is also the schema's most complete positive instance.
"""
from __future__ import annotations

from pathlib import Path

from ghostauthor.engine import build_report
from ghostauthor.gitparse import parse_trailer_block
from ghostauthor.models import CommitInfo, Report
from ghostauthor.roster import validate_roster

GOLDEN_FILE = Path(__file__).resolve().parent / "goldens" / "report.json"

# Same fixed instant as the example fixtures (2026-01-01T00:00:00Z).
BASE_TS = 1767225600

ROSTER = validate_roster({
    "version": 1,
    "agents": [
        {"id": "claude", "display_name": "Claude (Anthropic)",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"]},
        {"id": "codex", "display_name": "Codex (OpenAI)",
         "name_patterns": ["^OpenAI Codex\\b"], "email_domains": ["openai.com"]},
    ],
})


def _commit(sha: str, ts: int, message: str,
            added: int, removed: int) -> CommitInfo:
    return CommitInfo(
        sha=sha, parent_shas=[],
        author_name="Sam Human", author_email="sam@example.com",
        authored_at=ts, message=message, trailers=parse_trailer_block(message),
        lines_added=added, lines_removed=removed,
    )


def make_golden_report() -> Report:
    commits = [
        _commit("a" * 40, BASE_TS,
                "agent commit\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                10, 2),
        _commit("b" * 40, BASE_TS + 60,
                "cosigned commit\n\nCo-Authored-By: VibeBot <vibe@example.com>",
                3, 0),
        _commit("c" * 40, BASE_TS + 120, "unsigned commit", 5, 1),
    ]
    return build_report(
        commits, ROSTER,
        repo_path="golden-repo", ref="golden",
        max_ai_ratio=0.5, since=BASE_TS, until=BASE_TS + 1000,
    )


def golden_bytes() -> str:
    from ghostauthor.report import render_json  # local: keep import graph flat
    return render_json(make_golden_report())
