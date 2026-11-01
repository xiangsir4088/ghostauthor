"""Static roster lint: warnings only, never rejects (v1.1).

Complements roster validation, which rejects malformed data with rule
ids. Lint surfaces rosters that are LEGAL but fragile:

- ``lint-catch-all`` — a name pattern that matches every probe identity:
  non-discriminating, and under first-match-wins its placement in file
  order silently decides attribution.
- ``lint-overlap`` — an agent's signing identity resolves to ANOTHER
  agent under roster precedence: the roster-level form of
  ``sign-ambiguous`` (the sign-time refusal, which stays).

Findings are deterministic (roster file order, no wall clock). Lint is
advisory and exits 0 with or without warnings: "validated, not trusted"
extends to lint — the verdict about a fragile roster belongs to the
human reviewer, not to the tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .roster import Roster

# Deterministic probe identities, each leading with a different character
# class. A pattern that matches ALL of them under roster semantics
# (re.match = anchored at the start) is non-discriminating in practice.
PROBE_NAMES = (
    "Zxqv Probe One",    # leading uppercase letter
    "7digit lead",       # leading digit
    "h",                 # single lowercase letter
    "_underscore lead",  # leading underscore
    "探针 probe",          # leading CJK
)


@dataclass(frozen=True)
class LintFinding:
    rule: str        # lint-catch-all | lint-overlap
    agent: str       # the agent id the finding is about
    detail: str
    pattern: str = ""  # the offending pattern (catch-all only)


def lint_catch_all(roster: Roster) -> List[LintFinding]:
    findings: List[LintFinding] = []
    for agent in roster.agents:
        for rx in agent.name_patterns:
            if all(rx.match(probe) for probe in PROBE_NAMES):
                findings.append(LintFinding(
                    rule="lint-catch-all",
                    agent=agent.id,
                    detail=(f"name pattern {rx.pattern!r} matches every "
                            f"probe identity -- it is non-discriminating; "
                            f"under first-match-wins, placement in file "
                            f"order decides attribution"),
                    pattern=rx.pattern))
    return findings


def lint_overlap(roster: Roster) -> List[LintFinding]:
    findings: List[LintFinding] = []
    for agent in roster.agents:
        if agent.signing is None:
            continue
        name, email = agent.signing
        domain = email.rsplit("@", 1)[1].strip().lower()
        resolves = roster.match(name, domain)
        if resolves is not None and resolves.id != agent.id:
            findings.append(LintFinding(
                rule="lint-overlap",
                agent=agent.id,
                detail=(f"signing identity of {agent.id!r} "
                        f"({name} <{email}>) resolves to {resolves.id!r} "
                        f"under roster precedence (file order, first "
                        f"match wins) -- signing is refused at sign time "
                        f"(sign-ambiguous) and attribution is order-fragile")))
    return findings


def lint_roster(roster: Roster) -> List[LintFinding]:
    """All findings, roster file order. Pure and deterministic: the same
    roster always yields the same findings."""
    return lint_catch_all(roster) + lint_overlap(roster)
