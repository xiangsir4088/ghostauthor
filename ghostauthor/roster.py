"""Agent roster loading + validation.

The roster is declarative data (JSON) and is the community entry point
for contributions: adding a new agent is a data change, not a code change.

Validation is strict and deterministic: a malformed roster is rejected
with a precise rule id, never silently repaired. (A silently "fixed"
roster would make the verdict unauditable — see README, invariants.)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class RosterError(ValueError):
    """A roster failed validation. The message always starts with a rule id."""


@dataclass(frozen=True)
class AgentRule:
    id: str
    display_name: str
    name_patterns: Tuple[re.Pattern, ...]
    email_domains: frozenset


@dataclass
class Roster:
    version: int
    agents: List[AgentRule] = field(default_factory=list)
    source: str = "<builtin>"

    def match(self, name: str, email_domain: str) -> Optional[AgentRule]:
        """First matching rule wins. Roster file order IS the documented
        precedence — deterministic, no ranking heuristics."""
        for agent in self.agents:
            if any(rx.match(name) for rx in agent.name_patterns):
                return agent
            if email_domain and email_domain in agent.email_domains:
                return agent
        return None


def _fail(rule: str, detail: str) -> RosterError:
    return RosterError(f"{rule}: {detail}")


def builtin_roster_path() -> Path:
    return Path(__file__).resolve().parent / "roster" / "agents.json"


def load_roster(path: Union[str, Path]) -> Roster:
    path = Path(path)
    if not path.is_file():
        raise _fail("roster-file-missing", f"no such file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _fail("roster-json-invalid", str(exc)) from exc
    return validate_roster(raw, source=str(path))


def load_roster_dir(path: Union[str, Path]) -> Roster:
    """Load every *.json roster in a directory and merge them.

    Files are processed in sorted filename order (deterministic). Each
    file is validated on its own; a duplicate agent id ACROSS files is
    rejected (roster-dir-dup) — conflicts must be resolved by a human,
    never silently by merge order. The merged roster reports the maximum
    of the constituent versions.
    """
    d = Path(path)
    if not d.is_dir():
        raise _fail("roster-dir-missing", f"no such directory: {d}")
    files = sorted(d.glob("*.json"))
    if not files:
        raise _fail("roster-dir-empty", f"no roster files in {d}")
    agents: List[AgentRule] = []
    source_of: dict = {}  # agent id -> filename of first occurrence
    version = 0
    for f in files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise _fail("roster-json-invalid", f"{f.name}: {exc}") from exc
        r = validate_roster(raw, source=f.name)
        version = max(version, r.version)
        for a in r.agents:
            if a.id in source_of:
                raise _fail("roster-dir-dup",
                            f"duplicate id {a.id!r} in {source_of[a.id]} "
                            f"and {f.name}")
            source_of[a.id] = f.name
            agents.append(a)
    return Roster(version=version, agents=agents, source=str(d))


def validate_roster(raw: object, source: str = "<builtin>") -> Roster:
    if not isinstance(raw, dict):
        raise _fail("roster-top-level", "expected a JSON object")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _fail("roster-version", "expected a positive integer")
    agents_raw = raw.get("agents")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise _fail("roster-agents", "expected a non-empty list")

    seen: set = set()
    agents: List[AgentRule] = []
    for i, entry in enumerate(agents_raw):
        where = f"agents[{i}]"
        if not isinstance(entry, dict):
            raise _fail("roster-agent-type", f"{where}: expected an object")

        agent_id = entry.get("id")
        if not isinstance(agent_id, str) or not _ID_RE.match(agent_id):
            raise _fail("roster-agent-id",
                        f"{where}: id must be a string matching {_ID_RE.pattern}")
        if agent_id in seen:
            raise _fail("roster-agent-dup", f"{where}: duplicate id {agent_id!r}")
        seen.add(agent_id)

        display = entry.get("display_name")
        if not isinstance(display, str) or not display.strip():
            raise _fail("roster-agent-display", f"{where}: display_name is required")

        patterns_raw = entry.get("name_patterns")
        patterns: List[re.Pattern] = []
        if patterns_raw is not None:
            if not isinstance(patterns_raw, list):
                raise _fail("roster-agent-patterns",
                            f"{where}: name_patterns must be a list")
            for j, pat in enumerate(patterns_raw):
                if not isinstance(pat, str) or not pat:
                    raise _fail("roster-agent-pattern",
                                f"{where}.name_patterns[{j}]: non-empty string required")
                try:
                    patterns.append(re.compile(pat))
                except re.error as exc:
                    raise _fail("roster-agent-pattern",
                                f"{where}.name_patterns[{j}]: {exc}") from exc

        domains_raw = entry.get("email_domains", [])
        if not isinstance(domains_raw, list):
            raise _fail("roster-agent-domains", f"{where}: email_domains must be a list")
        domains = set()
        for d in domains_raw:
            if not isinstance(d, str) or not d or d != d.strip() or " " in d:
                raise _fail("roster-agent-domain", f"{where}: bad domain {d!r}")
            domains.add(d.lower())

        if not patterns and not domains:
            raise _fail("roster-agent-empty",
                        f"{where}: at least one name pattern or email domain required")

        agents.append(AgentRule(agent_id, display.strip(),
                                tuple(patterns), frozenset(domains)))
    return Roster(version=version, agents=agents, source=source)
