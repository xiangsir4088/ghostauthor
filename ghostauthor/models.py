"""vibemeter data model.

Pure data: no file IO, no git calls, no rendering, no wall clock.
Everything downstream consumes these objects; nothing downstream
recomputes them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Commit classification buckets (exhaustive; missing counts are 0, never absent)
BUCKET_AGENT = "agent"        # at least one trailer matched the roster
BUCKET_COSIGNED = "cosigned"  # has Co-Authored-By trailers, none matched the roster
BUCKET_UNSIGNED = "unsigned"  # no Co-Authored-By trailer in the trailer block

ALL_BUCKETS = (BUCKET_AGENT, BUCKET_COSIGNED, BUCKET_UNSIGNED)


@dataclass(frozen=True)
class Trailer:
    """One parsed Co-Authored-By trailer line.

    `email` is "" when the trailer carries no <...> part.
    Invariant: missing value == empty string, never an exception.
    """
    raw: str      # original line, kept as audit evidence
    name: str
    email: str

    @property
    def email_domain(self) -> str:
        if "@" in self.email:
            return self.email.rsplit("@", 1)[1].strip().lower()
        return ""


@dataclass
class CommitInfo:
    """One commit, extracted from git exactly as stored."""
    sha: str
    parent_shas: List[str]
    author_name: str
    author_email: str
    authored_at: int  # unix seconds, read from the commit itself (deterministic)
    message: str
    trailers: List[Trailer] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class AgentStats:
    """Per-agent attribution. Attribution may OVERLAP across agents
    (one commit can carry several matched trailers); the deduplicated
    totals live in Report.commits_by_bucket. This is the cap invariant."""
    agent_id: str
    display_name: str
    commits: int
    lines_added: int
    lines_removed: int
    session_count: int
    longest_session_commits: int
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None


@dataclass
class Session:
    """A run of one agent's commits with inter-commit gaps <= the
    configured threshold, in authored_at order."""
    agent_id: str
    start_ts: int
    end_ts: int
    commit_shas: List[str]


@dataclass
class AuthorStats:
    """One row of the per-author x per-agent cross-tabulation.

    `by_agent` maps an attribution column — a roster agent id, or the
    bucket keys "cosigned" / "unsigned" — to a commit count; non-zero
    entries only. A multi-agent commit counts in EVERY matched column
    (the same cap invariant as AgentStats); `commits` is the deduplicated
    row total. `by_agent_shas` keeps the traceability invariant: every
    cell count expands to a commit list, in (authored_at, sha) order.
    """
    name: str
    email: str
    commits: int
    by_agent: Dict[str, int]
    by_agent_shas: Dict[str, List[str]]


@dataclass
class Finding:
    """One traceable judgment: rule id + evidence (sha + original line).

    Findings never change the verdict; they explain what the engine saw.
    """
    rule: str
    severity: str  # "info" | "warn"
    evidence: str
    detail: str


@dataclass
class Report:
    """The complete, deterministic result of one scan.

    `ai_ratio` = agent-bucket commits / total commits (deduplicated).
    `gate` is None unless --max-ai-ratio was given.
    """
    repo_path: str
    ref: str
    total_commits: int
    commits_by_bucket: Dict[str, int]
    lines_by_bucket: Dict[str, int]
    bucket_commit_shas: Dict[str, List[str]]  # traceability: every count -> commit list
    agents: List[AgentStats]
    authors: List[AuthorStats] = field(default_factory=list)
    cosigned_evidence: List[str] = field(default_factory=list)
    sessions: List[Session] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    ai_ratio: float = 0.0
    gate: Optional[Dict] = None
    window: Optional[Dict] = None  # {"since": int|None, "until": int|None}; set when a window was applied


def parse_window_value(value: str) -> int:
    """Deterministic window-bound parsing: epoch seconds, YYYY-MM-DD
    (UTC midnight) or ISO 8601 datetime. Naive datetimes are UTC. No
    wall clock is involved — the bound is pure data.

    Lives here (pure, no IO) so both the CLI and the web transport share
    exactly one parser — the boundary semantics are defined in one place.
    """
    if value.isdigit():
        return int(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"window-bound-invalid: cannot parse {value!r} (use epoch "
            "seconds, YYYY-MM-DD or ISO 8601)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
