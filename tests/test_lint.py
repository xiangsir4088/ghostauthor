"""Roster lint (v1.1): warnings only, never rejects, deterministic.

Lint is the advisory layer over roster validation: a LEGAL but fragile
roster (catch-all pattern, cross-agent overlap) must produce warnings,
and the command must still exit 0 — the verdict about a fragile roster
belongs to the human reviewer. The builtin roster must lint clean.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ghostauthor.lint import lint_catch_all, lint_overlap, lint_roster
from ghostauthor.roster import validate_roster

ROOT = Path(__file__).resolve().parent.parent

CLEAN_RAW = {
    "version": 1,
    "agents": [
        {"id": "claude", "display_name": "Claude (Anthropic)",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"],
         "signing": {"name": "Claude", "email": "noreply@anthropic.com"}},
        {"id": "codex", "display_name": "Codex (OpenAI)",
         "name_patterns": ["^OpenAI Codex\\b"], "email_domains": ["openai.com"],
         "signing": {"name": "OpenAI Codex", "email": "noreply@openai.com"}},
        {"id": "plain", "display_name": "Plain",
         "name_patterns": ["^Plain\\b"], "email_domains": []},
    ],
}

# LEGAL but fragile: `wild` first in file order with a catch-all pattern
# swallows every name, so claude's signing identity resolves to `wild`
# (sign-ambiguous territory). Validation accepts it; lint must warn.
WILD_RAW = {
    "version": 1,
    "agents": [
        {"id": "wild", "display_name": "Wild",
         "name_patterns": [".*"], "email_domains": [],
         "signing": {"name": "Wild", "email": "wild@wild.example"}},
        {"id": "claude", "display_name": "Claude (Anthropic)",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"],
         "signing": {"name": "Claude", "email": "noreply@anthropic.com"}},
    ],
}


def _env() -> dict:
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "Sam Human", "GIT_AUTHOR_EMAIL": "sam@example.com",
        "GIT_COMMITTER_NAME": "Sam Human",
        "GIT_COMMITTER_EMAIL": "sam@example.com",
    })
    return e


def _cli(*args, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(ROOT))


class LintUnitTests(unittest.TestCase):
    def test_clean_roster_has_no_findings(self):
        roster = validate_roster(json.loads(json.dumps(CLEAN_RAW)))
        self.assertEqual(lint_roster(roster), [])

    def test_catch_all_detected_with_pattern(self):
        roster = validate_roster(json.loads(json.dumps(WILD_RAW)))
        findings = lint_catch_all(roster)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "lint-catch-all")
        self.assertEqual(findings[0].agent, "wild")
        self.assertEqual(findings[0].pattern, ".*")

    def test_specific_patterns_are_not_flagged(self):
        roster = validate_roster(json.loads(json.dumps(CLEAN_RAW)))
        self.assertEqual(lint_catch_all(roster), [])

    def test_overlap_detected_for_shadowed_agent(self):
        roster = validate_roster(json.loads(json.dumps(WILD_RAW)))
        findings = lint_overlap(roster)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "lint-overlap")
        self.assertEqual(findings[0].agent, "claude")
        self.assertIn("wild", findings[0].detail)

    def test_overlap_absent_when_precedence_is_clean(self):
        roster = validate_roster(json.loads(json.dumps(CLEAN_RAW)))
        self.assertEqual(lint_overlap(roster), [])

    def test_agents_without_signing_are_not_overlap_candidates(self):
        # `plain` has no signing: it cannot be shadowed into a mis-sign
        roster = validate_roster(json.loads(json.dumps(CLEAN_RAW)))
        self.assertEqual(
            [f.agent for f in lint_overlap(roster)], [])

    def test_deterministic_repeated_calls(self):
        roster = validate_roster(json.loads(json.dumps(WILD_RAW)))
        self.assertEqual(lint_roster(roster), lint_roster(roster))

    def test_builtin_roster_lints_clean(self):
        from ghostauthor.roster import builtin_roster_path, load_roster
        roster = load_roster(builtin_roster_path())
        self.assertEqual(lint_roster(roster), [])


class LintCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ghostauthor-lint-test-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.env = _env()
        self.clean = root / "clean.json"
        self.clean.write_text(json.dumps(CLEAN_RAW), encoding="utf-8")
        self.wild = root / "wild.json"
        self.wild.write_text(json.dumps(WILD_RAW), encoding="utf-8")

    def test_clean_roster_exits_0_no_warnings(self):
        proc = _cli("lint-roster", "--roster", self.clean, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no warnings", proc.stdout)

    def test_warnings_exit_0_and_list_rules(self):
        proc = _cli("lint-roster", "--roster", self.wild, env=self.env)
        self.assertEqual(proc.returncode, 0,
                         "warnings are advisory — exit stays 0")
        self.assertIn("lint-catch-all", proc.stdout)
        self.assertIn("lint-overlap", proc.stdout)
        self.assertIn("wild", proc.stdout)
        self.assertIn("claude", proc.stdout)

    def test_json_format_is_parseable_and_deterministic(self):
        proc = _cli("lint-roster", "--roster", self.wild, "--format", "json",
                    env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["warnings"]), 2)
        rules = sorted(w["rule"] for w in data["warnings"])
        self.assertEqual(rules, ["lint-catch-all", "lint-overlap"])
        # byte-stable: the same roster gives the same bytes
        again = _cli("lint-roster", "--roster", self.wild, "--format", "json",
                     env=self.env)
        self.assertEqual(proc.stdout, again.stdout)

    def test_bad_roster_exits_2(self):
        bad = self.clean.parent / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        proc = _cli("lint-roster", "--roster", bad, env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("roster-json-invalid", proc.stderr)

    def test_default_is_builtin_roster(self):
        proc = _cli("lint-roster", env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # loading the builtin path reports its real location, and the
        # builtin roster itself must lint clean
        self.assertIn("agents.json", proc.stdout)
        self.assertIn("no warnings", proc.stdout)


if __name__ == "__main__":
    unittest.main()
