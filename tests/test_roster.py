"""Roster validation: the validator itself must be tested (negative cases).

A roster loader that only accepts good input is a rubber stamp.
"""
import copy
import unittest

from ghostauthor.roster import (builtin_roster_path, load_roster,
                                validate_roster, RosterError)

GOOD = {
    "version": 1,
    "agents": [
        {"id": "claude", "display_name": "Claude",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"]},
        {"id": "custom", "display_name": "Custom Bot",
         "name_patterns": [], "email_domains": ["bot.example.com"]},
    ],
}


class RosterValidationTests(unittest.TestCase):
    def test_builtin_roster_loads(self):
        roster = load_roster(builtin_roster_path())
        self.assertGreaterEqual(len(roster.agents), 5)
        ids = [a.id for a in roster.agents]
        self.assertEqual(len(ids), len(set(ids)))

    def test_match_by_name_and_by_domain(self):
        roster = validate_roster(copy.deepcopy(GOOD))
        self.assertEqual(roster.match("Claude", "").id, "claude")
        self.assertEqual(roster.match("Whatever", "anthropic.com").id, "claude")
        self.assertIsNone(roster.match("Nobody", "nowhere.org"))

    def test_precedence_is_file_order(self):
        raw = {"version": 1, "agents": [
            {"id": "first", "display_name": "F",
             "name_patterns": [], "email_domains": ["shared.example"]},
            {"id": "second", "display_name": "S",
             "name_patterns": [], "email_domains": ["shared.example"]},
        ]}
        self.assertEqual(validate_roster(raw).match("X", "shared.example").id,
                         "first")
        raw["agents"].reverse()
        self.assertEqual(validate_roster(raw).match("X", "shared.example").id,
                         "second")

    # ---- negative: malformed rosters are rejected, never repaired ----

    def _expect(self, raw, rule):
        with self.assertRaises(RosterError) as ctx:
            validate_roster(raw)
        self.assertIn(rule, str(ctx.exception))

    def test_rejects_duplicate_ids(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"].append(copy.deepcopy(raw["agents"][0]))
        self._expect(raw, "roster-agent-dup")

    def test_rejects_bad_id(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"][0]["id"] = "Claude Bot"
        self._expect(raw, "roster-agent-id")

    def test_rejects_missing_display_name(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"][0].pop("display_name")
        self._expect(raw, "roster-agent-display")

    def test_rejects_invalid_regex(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"][0]["name_patterns"] = ["("]
        self._expect(raw, "roster-agent-pattern")

    def test_rejects_agent_with_no_selectors(self):
        raw = {"version": 1, "agents": [
            {"id": "empty", "display_name": "E",
             "name_patterns": [], "email_domains": []}]}
        self._expect(raw, "roster-agent-empty")

    def test_rejects_bad_domain(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"][1]["email_domains"] = ["bad domain.com"]
        self._expect(raw, "roster-agent-domain")

    def test_rejects_bool_version(self):
        raw = copy.deepcopy(GOOD)
        raw["version"] = True
        self._expect(raw, "roster-version")

    def test_rejects_non_object_top_level(self):
        self._expect([1, 2, 3], "roster-top-level")

    def test_rejects_empty_agents(self):
        raw = copy.deepcopy(GOOD)
        raw["agents"] = []
        self._expect(raw, "roster-agents")

    # ---- roster-dir: merge semantics are pinned ----

    def _write(self, d, name, obj):
        import json
        (d / name).write_text(json.dumps(obj), encoding="utf-8")

    def test_roster_dir_merges_in_sorted_order(self):
        import tempfile
        from pathlib import Path
        from ghostauthor.roster import load_roster_dir
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "b-second.json", {"version": 1, "agents": [
                {"id": "beta", "display_name": "Beta",
                 "name_patterns": ["^Beta\\b"], "email_domains": []}]})
            self._write(d, "a-first.json", {"version": 2, "agents": [
                {"id": "alpha", "display_name": "Alpha",
                 "name_patterns": ["^Alpha\\b"], "email_domains": []}]})
            r = load_roster_dir(d)
            self.assertEqual([a.id for a in r.agents], ["alpha", "beta"])
            self.assertEqual(r.version, 2)  # max of the constituents

    def test_roster_dir_rejects_cross_file_duplicate(self):
        import tempfile
        from pathlib import Path
        from ghostauthor.roster import load_roster_dir
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "a.json", {"version": 1, "agents": [
                {"id": "same", "display_name": "A",
                 "name_patterns": ["^A\\b"], "email_domains": []}]})
            self._write(d, "b.json", {"version": 1, "agents": [
                {"id": "same", "display_name": "B",
                 "name_patterns": ["^B\\b"], "email_domains": []}]})
            with self.assertRaises(RosterError) as ctx:
                load_roster_dir(d)
            self.assertIn("roster-dir-dup", str(ctx.exception))

    def test_roster_dir_rejects_empty_or_missing(self):
        import tempfile
        from pathlib import Path
        from ghostauthor.roster import load_roster_dir
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RosterError) as ctx:
                load_roster_dir(td)
            self.assertIn("roster-dir-empty", str(ctx.exception))
        with self.assertRaises(RosterError) as ctx:
            load_roster_dir(Path(td) / "nope")
        self.assertIn("roster-dir-missing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
