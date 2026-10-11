"""Git data extraction.

Deterministic: results depend only on the repository state (commit data
read from git), never on the wall clock, the network, or file mtimes.

Trailer semantics follow git: only the TRAILER BLOCK counts — the final
paragraph of a commit message consisting solely of `key: value` lines.
A "Co-Authored-By:" line written in the body of a message is prose, not
metadata: it is ignored here and surfaced as a finding by the engine.

Missing parts are empty values, never exceptions: a trailer without
<email> yields email=""; a commit without a trailer block yields no
trailers.
"""
from __future__ import annotations

import re
import subprocess
from typing import List, Tuple

from .models import CommitInfo, Trailer

# Framing characters: C1 control characters, invisible in prose and
# effectively never emitted by humans or LLMs into commit messages.
# NUL (\x00) is deliberately NOT used: Windows rejects argv containing
# NUL bytes (CreateProcess: "embedded null character"), which would make
# the tool unrunnable on a major platform. \x1d/\x1e/\x1f are all safe.
RECORD_START = "\x1d"
FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"

# Per commit git emits:  <START> sha FS parents FS an FS ae FS at FS <SEP>
#                        message <SEP> numstat-block
# and glues the next commit's <START> straight onto the numstat block.
# So: split on RECORD_START first (one segment per commit), then split each
# segment on RECORD_SEP: head is the first part, numstat the last, and the
# message is everything between (a message containing RECORD_SEP re-joins
# losslessly). A message containing RECORD_START is pathological: rejected.
_LOG_FORMAT = (RECORD_START + "%H" + FIELD_SEP + "%P" + FIELD_SEP +
               "%an" + FIELD_SEP + "%ae" + FIELD_SEP + "%at" + FIELD_SEP +
               RECORD_SEP + "%B" + RECORD_SEP)

_TRAILER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)(\s*):\s*(.*)$")
_NAME_EMAIL_RE = re.compile(r"^(?P<name>.+?)(?:\s*<(?P<email>[^<>]*)>)?$")
_NUMSTAT_RE = re.compile(r"^(?P<added>\d+|-)\t(?P<removed>\d+|-)\t(?P<path>.+)$")

COAUTHOR_KEY = "co-authored-by"


class GitError(RuntimeError):
    """Raised for unusable repositories. `code` is one of:
    git-missing | not-a-repo | no-commits | git-failed | malformed-output."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _run_git(args: List[str]) -> str:
    try:
        proc = subprocess.run(["git", *args], capture_output=True,
                              text=True, check=False)
    except FileNotFoundError:
        raise GitError("git-missing", "git executable not found on PATH")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        if "not a git repository" in err:
            raise GitError("not-a-repo", err)
        if "does not have any commits" in err:
            raise GitError("no-commits", err)
        raise GitError("git-failed", err)
    return proc.stdout


def parse_trailer_block(message: str) -> List[Trailer]:
    """Return the Co-Authored-By trailers of `message`, per git semantics.

    The trailer block is the final paragraph where every non-continuation
    line looks like `key: value`. If that test fails, the message has no
    trailer block and no trailers are returned — never an error.
    """
    lines = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)
    if not paragraphs:
        return []

    block = paragraphs[-1]
    for line in block:
        if line[:1] in (" ", "\t"):
            continue  # continuation line: valid in a trailer block
        if _TRAILER_LINE_RE.match(line) is None:
            return []  # final paragraph is prose, not a trailer block

    trailers: List[Trailer] = []
    i = 0
    while i < len(block):
        line = block[i]
        m = _TRAILER_LINE_RE.match(line)
        key = m.group(1).casefold() if m else None
        raw_lines = [line]
        i += 1
        while i < len(block) and block[i][:1] in (" ", "\t"):
            raw_lines.append(block[i])
            i += 1
        if key != COAUTHOR_KEY:
            continue
        # name/email come from the main line only; continuation text stays
        # in `raw` for audit. (Letting prose change the parsed address would
        # make matching depend on wording — exactly what this tool refuses.)
        name, email = _split_name_email(m.group(3))
        trailers.append(Trailer(raw="\n".join(raw_lines), name=name, email=email))
    return trailers


def _split_name_email(value: str) -> Tuple[str, str]:
    value = value.strip()
    if not value:
        return "", ""
    m = _NAME_EMAIL_RE.match(value)
    name = (m.group("name") if m else value).strip()
    email = (m.group("email") or "").strip() if m else ""
    return name, email


def _parse_numstat(block: str) -> Tuple[int, int]:
    added = removed = 0
    for line in block.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        m = _NUMSTAT_RE.match(line)
        if not m:
            continue  # rename headers etc. are not line counts: skip, never error
        if m.group("added") != "-":
            added += int(m.group("added"))
        if m.group("removed") != "-":
            removed += int(m.group("removed"))
    return added, removed


def _parse_log(out: str) -> List[CommitInfo]:
    """Parse git output framed with RECORD_START / RECORD_SEP / FIELD_SEP."""
    segments = out.split(RECORD_START)
    if segments and segments[0].strip():
        raise GitError("malformed-output", "data before first record")
    commits: List[CommitInfo] = []
    for seg in segments[1:]:
        parts = seg.split(RECORD_SEP)
        if len(parts) < 3:
            raise GitError("malformed-output", "expected 3 sections per record")
        head, numstat = parts[0], parts[-1]
        message = RECORD_SEP.join(parts[1:-1]).strip("\n")
        fields = head.rstrip(FIELD_SEP).split(FIELD_SEP)
        if len(fields) != 5:
            raise GitError("malformed-output", "expected 5 header fields")
        sha, parents, an, ae, at = fields
        try:
            ts = int(at)
        except ValueError:
            raise GitError("malformed-output", f"bad timestamp {at!r}") from None
        added, removed = _parse_numstat(numstat)
        commits.append(CommitInfo(
            sha=sha.strip(),
            parent_shas=[p for p in parents.split(" ") if p],
            author_name=an,
            author_email=ae,
            authored_at=ts,
            message=message,
            trailers=parse_trailer_block(message),
            lines_added=added,
            lines_removed=removed,
        ))
    return commits


def iter_commits(repo_path: str, all_refs: bool = False) -> List[CommitInfo]:
    """Extract every commit of the repository, oldest data last.

    Returns commits in git log order (newest first); the engine sorts
    deterministically by (authored_at, sha).
    """
    args = ["-C", str(repo_path), "log", "--numstat",
            "--format=" + _LOG_FORMAT, "--no-show-signature"]
    args.append("--all" if all_refs else "HEAD")
    out = _run_git(args)
    commits = _parse_log(out)
    if not commits:
        raise GitError("no-commits")
    return commits
