"""ghostauthor data model.

Pure data: no file IO, no git calls, no rendering, no wall clock.
Everything downstream consumes these objects; nothing downstream
recomputes them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Commit classification buckets (exhaustive; missing counts are 0, never absent)
BUCKET_AGENT = "agent"        # at least one trailer matched the roster
BUCKET_COSIGNED = "cosigned"  # has Co-Authored-By trailers, none matched the roster
BUCKET_UNSIGNED = "unsigned"  # no Co-Authored-By trailer in the trailer block

ALL_BUCKETS = (BUCKET_AGENT, BUCKET_COSIGNED, BUCKET_UNSIGNED)

# A line that LOOKS like a Co-Authored-By trailer, wherever it appears.
# Shared by the read side (which reports it as prose when it is outside the
# trailer block) and the write side (which must see it even when git would
# not count it as a trailer) — the two must not disagree about whether a
# commit carries a signature.
COAUTHOR_LINE_RE = re.compile(r"^\s*co-authored-by\s*:", re.IGNORECASE)


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


# The accepted ISO 8601 subset, pinned EXPLICITLY.
#
# `datetime.fromisoformat` is deliberately not used: CPython 3.11 widened it
# to accept most ISO 8601 forms (compact dates like 20260101, nanosecond
# precision), while 3.9 accepts almost none of them. The project supports
# 3.9+, so the same bound would be accepted on one supported interpreter and
# rejected on another — the accepted set would depend on the interpreter
# instead of on the code. That is exactly the drift this project refuses:
# the same request must give the same answer everywhere.
_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:[T ](?P<time>\d{2}:\d{2}(?::\d{2})?(?:\.\d{1,6})?))?"
    r"(?P<tz>Z|z|[+-]\d{2}:?\d{2})?$"
)


def _parse_iso(value: str) -> datetime:
    """Parse the accepted ISO 8601 subset into a datetime.

    Shapes: `YYYY-MM-DD`, optionally followed by `T` or a space and
    `HH:MM[:SS[.ffffff]]`, optionally followed by `Z` or a `±HH:MM`
    offset. A naive value is UTC (the documented rule).
    """
    m = _ISO_RE.match(value)
    if m is None:
        raise ValueError(f"not an accepted ISO 8601 instant: {value!r}")
    clock = m.group("time") or "00:00:00"
    if len(clock) == 5:          # HH:MM
        clock += ":00"
    if "." in clock:             # HH:MM:SS.ffffff — pad/truncate to micros
        head, frac = clock.split(".")
        clock = f"{head}.{frac.ljust(6, '0')[:6]}"
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in clock else "%Y-%m-%d %H:%M:%S"
    dt = datetime.strptime(f"{m.group('date')} {clock}", fmt)
    tz = m.group("tz")
    if tz in (None, "", "Z", "z"):
        return dt.replace(tzinfo=timezone.utc)
    sign = 1 if tz[0] == "+" else -1
    body = tz[1:].replace(":", "")
    offset = timedelta(hours=int(body[:2]), minutes=int(body[2:4])) * sign
    return dt.replace(tzinfo=timezone(offset))


def parse_window_value(value: str) -> int:
    """Deterministic window-bound parsing: epoch seconds, YYYY-MM-DD
    (UTC midnight) or ISO 8601 datetime. Naive datetimes are UTC. No
    wall clock is involved — the bound is pure data.

    Lives here (pure, no IO) so both the CLI and the web transport share
    exactly one parser — the boundary semantics are defined in one place.
    """
    # `str.isdigit()` is true for superscripts ("²"), which `int()` then
    # refuses: without the ASCII check the documented
    # `window-bound-invalid` code never happens and a raw Python message
    # escapes to the caller.
    if value.isascii() and value.isdigit():
        return int(value)
    try:
        dt = _parse_iso(value)
    except ValueError:
        raise ValueError(
            f"window-bound-invalid: cannot parse {value!r} (use epoch "
            "seconds, YYYY-MM-DD or ISO 8601)") from None
    return int(dt.timestamp())
