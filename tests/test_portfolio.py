"""Portfolio aggregation: the one arithmetic mistake that matters.

A portfolio ratio can be computed two ways, and only one of them describes
the codebase:

    weighted    sum(agent commits) / sum(commits)
    unweighted  mean of per-repository ratios

They diverge exactly when repository sizes differ, which is always. The
tests below pin the weighted figure as the headline, keep the unweighted one
visible next to it, and make sure a failed repository never silently joins
the totals.

The aggregation is a pure function over `RepoEntry`, so these tests need no
git — and run in milliseconds.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ghostauthor.portfolio import (Portfolio, RepoEntry, aggregate_agents,
                                   aggregate_totals, discover_repos,
                                   gate_result, read_repo_list,
                                   render_portfolio_html,
                                   render_portfolio_json,
                                   render_portfolio_text)


def _ok(name, total, agent, ratio=None):
    return RepoEntry(
        name=name, path=f"/repos/{name}", ref="HEAD",
        total_commits=total, agent_commits=agent,
        ai_ratio=(agent / total if ratio is None and total else 0.0),
    )


class WeightingTests(unittest.TestCase):
    def test_aggregate_is_weighted_not_a_mean_of_ratios(self):
        # 1 commit at 100% and 99 commits at 0%.
        entries = [_ok("tiny", 1, 1), _ok("huge", 99, 0)]
        totals = aggregate_totals(entries)
        self.assertEqual(totals["total_commits"], 100)
        self.assertEqual(totals["agent_commits"], 1)
        self.assertEqual(totals["ai_ratio"], 0.01)          # weighted
        self.assertEqual(totals["mean_repo_ratio"], 0.5)    # mean of 1.0 and 0.0
        self.assertNotEqual(totals["ai_ratio"], totals["mean_repo_ratio"])

    def test_failed_repository_is_counted_but_never_aggregated(self):
        entries = [_ok("good", 10, 5),
                   RepoEntry(name="bad", path="/repos/bad", ref="HEAD",
                             error="not-a-repo: no .git")]
        totals = aggregate_totals(entries)
        self.assertEqual(totals["repositories"], 2)
        self.assertEqual(totals["scanned"], 1)
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(totals["total_commits"], 10)
        self.assertEqual(totals["ai_ratio"], 0.5)

    def test_zero_commits_yields_zero_not_a_division_error(self):
        totals = aggregate_totals([_ok("empty", 0, 0)])
        self.assertEqual(totals["ai_ratio"], 0.0)
        self.assertEqual(totals["mean_repo_ratio"], 0.0)

    def test_agents_are_summed_across_repositories(self):
        a = RepoEntry(name="a", path="/a", ref="HEAD", total_commits=3,
                      agent_commits=3, ai_ratio=1.0,
                      agents=[("claude", "Claude (Anthropic)", 3)])
        b = RepoEntry(name="b", path="/b", ref="HEAD", total_commits=2,
                      agent_commits=1, ai_ratio=0.5,
                      agents=[("claude", "Claude (Anthropic)", 1),
                              ("codex", "Codex (OpenAI)", 1)])
        totals = aggregate_agents([a, b])
        self.assertEqual([(t.agent_id, t.commits, t.repos) for t in totals],
                         [("claude", 4, 2), ("codex", 1, 1)])
        self.assertEqual(totals[0].display_name, "Claude (Anthropic)")

    def test_failed_repository_agents_are_ignored(self):
        bad = RepoEntry(name="bad", path="/bad", ref="HEAD",
                        error="no-commits: nothing to aggregate")
        self.assertEqual(aggregate_agents([bad]), [])


class DiscoveryTests(unittest.TestCase):
    def test_finds_nested_repositories_and_prunes_inside_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            # resolve() on both sides: Windows CI TEMP is reported as a 8.3
            # short name (C:\Users\RUNNER~1\...) while discover_repos() walks
            # the resolved long name (C:\Users\runneradmin\...). The two are
            # the same directory but different strings, so relative_to() would
            # raise "is not in the subpath". Normalise before comparing.
            root = Path(tmp).resolve()
            for rel in ("a", "b", "a/vendored", "node_modules/pkg", "deep/c/d"):
                (root / rel / ".git").mkdir(parents=True)
            found = [p.relative_to(root).as_posix()
                     for p in discover_repos(str(root), max_depth=4)]
            # a/vendored is inside a: a repository is never descended into.
            # node_modules is skipped by name. deep/c/d is depth 3.
            self.assertEqual(found, ["a", "b", "deep/c/d"])

    def test_depth_limit_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one" / "two" / ".git").mkdir(parents=True)
            self.assertEqual(discover_repos(str(root), max_depth=1), [])
            self.assertEqual(len(discover_repos(str(root), max_depth=2)), 1)

    def test_root_itself_can_be_a_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertEqual(discover_repos(tmp), [Path(tmp).resolve()])

    def test_missing_root_is_a_usage_error(self):
        with self.assertRaises(ValueError) as ctx:
            discover_repos("/definitely/not/here")
        self.assertIn("portfolio-path-invalid", str(ctx.exception))


class RepoListTests(unittest.TestCase):
    def test_comments_blank_lines_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repos.txt").write_text(
                "# team A\n"
                "alpha\n"
                "\n"
                f"  {root / 'gamma'}  \n"
                "# trailing comment\n",
                encoding="utf-8")
            paths = read_repo_list(str(root / "repos.txt"))
            # Every entry comes back resolved and absolute — relative entries
            # against the list file's directory, absolute ones unchanged.
            self.assertEqual(paths, [(root / "alpha").resolve(),
                                     (root / "gamma").resolve()])

    def test_empty_list_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "repos.txt"
            p.write_text("# nothing here\n\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                read_repo_list(str(p))
            self.assertIn("portfolio-list-empty", str(ctx.exception))


class RenderTests(unittest.TestCase):
    def _portfolio(self):
        from ghostauthor.portfolio import Portfolio
        entries = [_ok("alpha", 10, 4), _ok("beta", 30, 12)]
        return Portfolio(root="/repos", entries=entries,
                         totals=aggregate_totals(entries),
                         by_agent=aggregate_agents(entries), errors=[])

    def test_json_is_deterministic_and_carries_schema_version(self):
        pf = self._portfolio()
        first = render_portfolio_json(pf)
        second = render_portfolio_json(pf)
        self.assertEqual(first, second)
        payload = json.loads(first)
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["totals"]["ai_ratio"], 0.4)

    def test_html_reports_both_ratios_so_they_cannot_be_confused(self):
        html = render_portfolio_html(self._portfolio())
        self.assertIn("weighted", html)
        self.assertIn("Unweighted mean", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_text_names_the_weighted_figure_explicitly(self):
        text = render_portfolio_text(self._portfolio())
        self.assertIn("aggregate:", text)
        self.assertIn("mean per-repo ratio", text)


if __name__ == "__main__":
    unittest.main()


class OrganisationGateTests(unittest.TestCase):
    """The org-level gate must use the weighted ratio and the report's
    boundary rule: `ratio > limit` breaches, equality passes."""

    def test_equality_passes_and_above_breaches(self):
        self.assertFalse(gate_result(0.4, 0.4)["breached"])
        self.assertTrue(gate_result(0.400001, 0.4)["breached"])
        self.assertFalse(gate_result(0.0, 0.0)["breached"])

    def test_no_limit_means_no_gate(self):
        self.assertIsNone(gate_result(0.9, None))

    def test_limit_outside_the_unit_interval_is_rejected(self):
        for bad in (-0.01, 1.01):
            with self.assertRaises(ValueError) as ctx:
                gate_result(0.5, bad)
            self.assertIn("gate-limit-invalid", str(ctx.exception))

    def _portfolio(self, gate=None):
        entries = [_ok("alpha", 10, 4), _ok("beta", 30, 12)]
        return Portfolio(root="/repos", entries=entries,
                         totals=aggregate_totals(entries),
                         by_agent=aggregate_agents(entries), errors=[],
                         gate=gate)

    def test_gate_appears_in_all_three_outputs(self):
        pf = self._portfolio(gate_result(0.4, 0.39))
        payload = json.loads(render_portfolio_json(pf))
        self.assertEqual(payload["gate"],
                         {"limit": 0.39, "ratio": 0.4, "breached": True})
        self.assertIn("BREACHED", render_portfolio_text(pf))
        self.assertIn("BREACHED", render_portfolio_html(pf))

    def test_absent_gate_is_null_not_missing(self):
        payload = json.loads(render_portfolio_json(self._portfolio()))
        self.assertIn("gate", payload)
        self.assertIsNone(payload["gate"])
