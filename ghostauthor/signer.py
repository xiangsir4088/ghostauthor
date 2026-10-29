"""Opt-in trailer signing — the write path, and its boundary.

This is the ONLY module in the package that mutates a repository, and it
mutates exactly one thing: the message of the commit it is told to sign
(`git commit --amend`, HEAD only). Everything else it reads.

The category change is documented in docs/attestation.md: the read side is
advice (safe unsupervised); the write side produces a claim that may be
consumed by a CI gate, so it is a supervised decision. The shape of this
module IS that supervision:

- opt-in: no hook, no default; the command exists only when explicitly run
- consent: dry-run by default; `--commit` is explicit consent
- idempotent: signing an already-signed commit is a no-op, never a second trailer
- no-merge on conflict: a commit already signed by ANOTHER roster agent is
  refused — conflicts are for humans, not for the tool to resolve
- single source of truth: the signing identity comes from the roster's
  `signing` section (validated at load time), never from env or free text

Errors never leave the repository modified: every failure path exits
before the single `git commit --amend` runs.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from .gitparse import GitError, _TRAILER_LINE_RE, _run_git, parse_trailer_block
from .roster import AgentRule, Roster

TRAILER_KEY = "Co-Authored-By"


class SignError(RuntimeError):
    """A sign failure that must exit 2 without touching the repository.

    `code` is one of: sign-agent-unknown | sign-no-signing |
    sign-ambiguous | sign-conflict. (Repository problems surface as
    GitError from gitparse: not-a-repo / no-commits / git-failed.)
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass
class SignResult:
    old_sha: str
    old_message: str
    trailer: str                       # the line that will be / was added
    new_message: str                   # the message the commit will carry
    no_op: bool                        # True: already signed, nothing to do
    applied: bool                      # True: the amend was actually executed
    new_sha: Optional[str] = None      # set when applied


def resolve_agent(roster: Roster, agent_id: str) -> AgentRule:
    """The identity a commit will carry, with the round-trip guarantee.

    Fails (exit-2 class) if the agent is unknown, has no `signing`
    section, or — the first-match-wins guard — the roster's own precedence
    would attribute the signing identity to a DIFFERENT agent. The last
    case means the audit would not see this agent: signing would be a lie,
    so it is refused, not papered over.
    """
    agent = next((a for a in roster.agents if a.id == agent_id), None)
    if agent is None:
        known = ", ".join(a.id for a in roster.agents)
        raise SignError("sign-agent-unknown",
                        f"agent {agent_id!r} not in roster (known: {known})")
    if agent.signing is None:
        raise SignError(
            "sign-no-signing",
            f"agent {agent_id!r} has no `signing` section in the roster — "
            "add it (name + email) to enable signing")
    name, email = agent.signing
    domain = email.rsplit("@", 1)[1].strip().lower()
    resolves = roster.match(name, domain)
    if resolves is None or resolves.id != agent.id:
        other = resolves.id if resolves else "no agent"
        raise SignError(
            "sign-ambiguous",
            f"signing identity of {agent_id!r} resolves to {other!r} under "
            "roster precedence (file order, first match wins) — the audit "
            "would misattribute the signature; fix the roster")
    return agent


def head_commit(repo_path: str) -> Tuple[str, str]:
    """(sha, message) of HEAD. GitError (not-a-repo / no-commits) on failure."""
    sha = _run_git(["-C", str(repo_path), "rev-parse", "HEAD"]).strip()
    message = _run_git(["-C", str(repo_path), "log", "-1",
                        "--format=%B", "HEAD"])
    return sha, message


def _append_trailer(message: str, trailer: str) -> str:
    """Insert `trailer` per git trailer semantics.

    If the final paragraph is already a trailer block, the line joins that
    block; otherwise a new final paragraph is started. The rest of the
    message is preserved byte-for-byte.
    """
    body = message.rstrip("\n")
    if not body:  # unreachable for real commits (a subject is required)
        return trailer + "\n"
    last_blank = body.rfind("\n\n")
    last_paragraph = body[last_blank + 2:] if last_blank != -1 else body
    is_trailer_block = all(
        line[:1] in (" ", "\t") or _TRAILER_LINE_RE.match(line)
        for line in last_paragraph.split("\n"))
    if is_trailer_block:
        return body + "\n" + trailer + "\n"
    return body + "\n\n" + trailer + "\n"


def plan_sign(message: str, roster: Roster, agent: AgentRule) -> Tuple[str, str, bool]:
    """Idempotency + no-merge-on-conflict, then the new message.

    Returns (trailer, new_message, no_op). A commit already carrying this
    agent's signature is a no-op; one carrying ANOTHER roster agent's
    signature is refused (sign-conflict). Non-roster co-authors (cosigned
    territory) do not conflict: the read side keeps them in their own
    bucket.
    """
    name, email = agent.signing
    trailer = f"{TRAILER_KEY}: {name} <{email}>"
    for t in parse_trailer_block(message):
        rule = roster.match(t.name, t.email_domain)
        if rule is None:
            continue
        if rule.id == agent.id:
            return trailer, message, True  # idempotent: already signed
        raise SignError(
            "sign-conflict",
            f"commit already carries a signature of roster agent "
            f"{rule.id!r} ({t.raw!r}) — resolve it manually; this tool "
            "never merges signatures")
    return trailer, _append_trailer(message, trailer), False


def apply_sign(repo_path: str, new_message: str) -> str:
    """Execute the single write: `git commit --amend` with the new message.

    Author identity/date are preserved by git; the committer identity comes
    from the caller's existing git configuration (this tool handles no
    credentials). Returns the new HEAD sha.
    """
    _run_git(["-C", str(repo_path), "commit", "--amend", "-m", new_message])
    return _run_git(["-C", str(repo_path), "rev-parse", "HEAD"]).strip()


def sign_head(repo_path: str, roster: Roster, agent_id: str,
              apply: bool = False) -> SignResult:
    """The full sign operation: resolve → read → plan → (apply).

    With `apply=False` (the default) nothing is written: the returned
    result carries exactly what `--commit` would do. With `apply=True`
    the single `git commit --amend` runs. Every failure path raises before
    any write.
    """
    agent = resolve_agent(roster, agent_id)
    old_sha, old_message = head_commit(repo_path)
    trailer, new_message, no_op = plan_sign(old_message, roster, agent)
    if no_op or not apply:
        return SignResult(old_sha, old_message, trailer,
                          new_message if not no_op else old_message,
                          no_op=no_op, applied=False)
    new_sha = apply_sign(repo_path, new_message)
    return SignResult(old_sha, old_message, trailer, new_message,
                      no_op=False, applied=True, new_sha=new_sha)
