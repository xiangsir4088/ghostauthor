"""vibemeter data model.

Pure data: no file IO, no git calls, no rendering, no wall clock.
Everything downstream consumes these objects; nothing downstream
recomputes them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    cosigned_evidence: List[str]
    sessions: List[Session]
    findings: List[Finding]
    ai_ratio: float
    gate: Optional[Dict] = None
    window: Optional[Dict] = None  # {"since": int|None, "until": int|None}; set when a window was applied
