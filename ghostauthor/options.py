"""Shared request-option semantics — ONE definition of scope, time window,
roster, session gap and gate, used by the CLI, the MCP server and the web
transport.

Why this module exists
----------------------
The three surfaces parse the same request-shaped options. Before this module
each of them parsed them independently, and the copies had already drifted:

* the web audit endpoint **lost the CLI's error codes** — a `window-empty`
  failure arrived as `value-error`, because that one handler hard-coded the
  code instead of reading the prefix off the message;
* the scope flag was spelled `all_refs` in the audit endpoint and `all` in
  the evidence and coverage endpoints, so a client that guessed wrong got a
  **silently different scope** (asked for all refs, quietly got HEAD);
* the audit endpoint **ignored `base`** entirely — asking for a two-branch
  diff returned the whole history, which for an audit tool is the worst
  possible failure mode: a wrong answer that looks right;
* the same "which commits are in scope" label (`base..target`, `all`,
  `HEAD`) was spelled out four separate times, so it could drift too.

This is the same lesson the project already applied twice — the window-bound
parser was moved into `models.py` so the CLI and the web share exactly one
definition, and the evidence selection was moved into
`evidence.select_evidence_commits` so the CLI, the MCP tool and the web
endpoint select the same commits. Boundary semantics belong in ONE place and
the surfaces must *call* it, not re-implement it.

Nothing here touches git or renders anything: this module only turns raw
request values into validated, ready-to-use options. The verdict still comes
from `engine.build_report`, which remains the single source of truth.

Error contract
--------------
Every failure raises `OptionError`, whose message is always
``"<code>: <detail>"`` — the exact shape the CLI prints today, so the
transports can keep translating it to their own error channel (HTTP 400,
MCP `isError`) without re-deriving the code from the text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .models import parse_window_value
from .roster import (Roster, RosterError, builtin_roster_path, load_roster,
                     load_roster_dir)


class OptionError(ValueError):
    """A request-level option error.

    `code` is the stable machine-readable id (the CLI prints it, the MCP
    server returns it, the web transport maps it to HTTP 400). `detail` is
    the human-readable part. `str(exc)` is ``"<code>: <detail>"`` — byte-for-
    byte the same shape the CLI emitted before this module existed.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def split_code(message: str) -> Tuple[str, str]:
    """Split a ``"<code>: <detail>"`` message into its two halves.

    Every error this project raises carries its machine-readable code as the
    message prefix, so re-wrapping must not double the prefix. Falls back to
    ``("value-error", message)`` for a message that carries no code — the
    same fallback the web transport used to apply on its own.
    """
    head, sep, tail = message.partition(":")
    head = head.strip()
    if sep and head and " " not in head and "\n" not in head:
        return head, tail.strip()
    return "value-error", message


@dataclass(frozen=True)
class Scope:
    """Which commits are in scope: all refs, one ref, or a two-branch range.

    `label` is the single spelling of "what was scanned" that every surface
    now reports: a range is ``base..target``, all refs is ``all``, and
    everything else is the ref name (or ``HEAD``).
    """

    all_refs: bool = False
    ref: Optional[str] = None
    base: Optional[str] = None

    @property
    def label(self) -> str:
        if self.base is not None:
            return f"{self.base}..{self.ref or 'HEAD'}"
        return "all" if self.all_refs else (self.ref or "HEAD")

    @property
    def git_kwargs(self) -> Dict[str, Any]:
        """The keyword arguments `gitparse.iter_commits` expects."""
        return {"all_refs": self.all_refs, "ref": self.ref, "base": self.base}

    def validate(self) -> "Scope":
        """Raise OptionError on a conflicting or malformed scope.

        - `all_refs` together with `ref` or `base` is a conflict: the
          request names two different scopes.
        - a ref or base starting with ``-`` is rejected outright: git ref
          names cannot start with a dash, and a refspec that looks like an
          option could be misread as one. This matters most on the
          network-facing web transport.
        """
        if self.all_refs and self.ref:
            raise OptionError("ref-conflict",
                              "use all_refs or ref, not both")
        if self.all_refs and self.base:
            raise OptionError("ref-conflict",
                              "use all_refs or base, not both")
        for name, value in (("ref", self.ref), ("base", self.base)):
            if value is not None and value.startswith("-"):
                raise OptionError("ref-invalid",
                                  f"{name} cannot start with '-'")
        return self


def scope_from_mapping(params: Mapping[str, Any]) -> Scope:
    """Build a Scope from a request mapping.

    ``all`` and ``all_refs`` are accepted as **synonyms**. The audit
    endpoint historically used `all_refs` and the evidence and coverage
    endpoints used `all`; a client that guessed wrong silently got a
    different scope (asked for all refs, quietly got HEAD). Accepting both
    removes that trap and keeps every existing caller working.

    When BOTH spellings are present, an explicit `all_refs` wins — so a
    stale `all: true` left over in a request cannot override a deliberate
    `all_refs: false`.
    """
    raw = (params.get("all_refs") if "all_refs" in params
           else params.get("all"))
    return Scope(
        all_refs=bool(raw),
        ref=(params.get("ref") or None),
        base=(params.get("base") or None),
    )


def resolve_scope(params: Mapping[str, Any], *,
                  shas_given: bool = False) -> Scope:
    """The requested scope, validated — with ONE rule about when a
    conflicting scope still matters.

    When the auditor named the commits explicitly (`shas_given`), the
    scope is irrelevant: the commits are already chosen. The CLI, the MCP
    `commit_evidence` tool and the web `/api/evidence` endpoint all ignore
    it then, so they must all ignore it the same way. The rule lives here
    so a fourth surface cannot get it wrong.
    """
    scope = scope_from_mapping(params)
    if not shas_given:
        scope.validate()
    return scope


def resolve_window(since_raw: Any = None,
                   until_raw: Any = None) -> Tuple[Optional[int], Optional[int]]:
    """Resolve the time-window bounds: (since, until).

    `since` is INCLUSIVE, `until` is EXCLUSIVE — the semantics pinned by
    tests. Both bounds accept epoch seconds, ``YYYY-MM-DD`` (UTC midnight)
    or ISO 8601; a naive datetime is UTC. No wall clock is involved: the
    bound is pure data.
    """
    since = until = None
    try:
        if since_raw not in (None, ""):
            since = parse_window_value(str(since_raw))
        if until_raw not in (None, ""):
            until = parse_window_value(str(until_raw))
    except ValueError as exc:
        code, detail = split_code(str(exc))
        raise OptionError(code, detail) from None
    return since, until


def resolve_roster(roster_path: Optional[str] = None,
                   roster_dir: Optional[str] = None) -> Roster:
    """Load the roster a request asked for: a file, a directory of files, or
    the builtin.

    `roster` and `roster_dir` together is a conflict. A roster that fails
    validation is **rejected, never repaired** — and the rejection keeps
    the validator's own rule id, so the operator sees which rule failed.
    """
    if roster_path and roster_dir:
        raise OptionError("roster-conflict",
                          "use roster or roster_dir, not both")
    try:
        if roster_dir:
            return load_roster_dir(roster_dir)
        return load_roster(roster_path or builtin_roster_path())
    except RosterError as exc:
        raise OptionError("roster-error", str(exc)) from None


def resolve_session_gap(gap: Any) -> int:
    """The session gap in minutes: a positive integer.

    A non-integral value is rejected rather than truncated: `int(1.9)` is
    `1`, so silently truncating would accept a gap the CLI's own argparse
    (`type=int`) refuses — one surface accepting what another rejects.
    """
    if isinstance(gap, float) and not gap.is_integer():
        raise OptionError("session-gap-invalid",
                          "session_gap_minutes must be an integer")
    try:
        value = int(gap)
    except (TypeError, ValueError):
        raise OptionError("session-gap-invalid",
                          "session_gap_minutes must be an integer") from None
    if value <= 0:
        raise OptionError("session-gap-invalid", "must be positive minutes")
    return value


def resolve_gate(max_ratio: Any) -> Optional[float]:
    """The optional 0..1 gate limit.

    None or an empty string means the gate is off. A ratio exactly at the
    limit is a PASS — the comparison itself lives in `engine.build_report`
    and is pinned by tests.
    """
    if max_ratio is None or max_ratio == "":
        return None
    try:
        ratio = float(max_ratio)
    except (TypeError, ValueError):
        raise OptionError("gate-limit-invalid",
                          "max-ai-ratio must be a number in [0, 1]") from None
    if not (0.0 <= ratio <= 1.0):
        raise OptionError("gate-limit-invalid", "must be within [0, 1]")
    return ratio
