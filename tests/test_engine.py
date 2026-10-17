"""Engine invariants — the tests that prove the tool is honest:

  * reproducible: same repo state => same Report (input order irrelevant)
  * missing == null: unsigned commits are counted, never raised
  * traceable: every bucket count has its commit list
  * cap: multi-agent attribution may overlap, deduplicated totals never do
  * boundary semantics pinned: ratio exactly at the limit is a PASS
"""
import unittest

from ghostauthor.engine import build_report
from ghostauthor.gitparse import parse_trailer_block
from ghostauthor.models import CommitInfo
from ghostauthor.roster import validate_roster

ROSTER = validate_roster({
    "version": 1,
    "agents": [
        {"id": "claude", "display_name": "Claude (Anthropic)",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"]},
        {"id": "codex", "display_name": "Codex (OpenAI)",
         "name_patterns": ["^Codex\\b"], "email_domains": ["openai.com"]},
    ],
})


def mk(sha, ts, trailers=(), added=0, removed=0, message=None,
       author=("h", "h@x.io")):
    msg = message or f"commit {sha}"
    if trailers:
        msg += "\n\n" + "\n".join(trailers)
    return CommitInfo(sha=sha, parent_shas=["0" * 40], author_name=author[0],
                      author_email=author[1], authored_at=ts, message=msg,
                      trailers=parse_trailer_block(msg),
                      lines_added=added, lines_removed=removed)


class EngineInvariantTests(unittest.TestCase):
    def test_reproducible_regardless_of_input_order(self):
        commits = [mk("a", 300, ["Co-Authored-By: Claude <n@anthropic.com>"]),
                   mk("b", 200),
                   mk("c", 100, ["Co-Authored-By: Codex <x@openai.com>"])]
        r1 = build_report(list(commits), ROSTER)
        r2 = build_report(list(reversed(commits)), ROSTER)
        self.assertEqual(r1.ai_ratio, r2.ai_ratio)
        self.assertEqual(r1.commits_by_bucket, r2.commits_by_bucket)
        self.assertEqual(r1.bucket_commit_shas, r2.bucket_commit_shas)
        self.assertEqual([a.agent_id for a in r1.agents],
                         [a.agent_id for a in r2.agents])

    def test_missing_trailer_is_unsigned_not_error(self):
        r = build_report([mk("a", 100)], ROSTER)
        self.assertEqual(r.commits_by_bucket["unsigned"], 1)
        self.assertEqual(r.commits_by_bucket["agent"], 0)

    def test_unknown_coauthor_is_cosigned_with_evidence(self):
        r = build_report([mk("a", 100, ["Co-Authored-By: Bot <bot@nowhere.io>"])],
                         ROSTER)
        self.assertEqual(r.commits_by_bucket["cosigned"], 1)
        hits = [f for f in r.findings if f.rule == "cosigned-unknown"]
        self.assertTrue(hits)
        self.assertTrue(all(f.evidence.startswith("a") for f in hits))

    def test_multi_agent_commit_counted_once_in_total(self):
        c = mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>",
                          "Co-Authored-By: Codex <x@openai.com>"])
        r = build_report([c], ROSTER)
        self.assertEqual(r.total_commits, 1)
        self.assertEqual(r.commits_by_bucket["agent"], 1)
        self.assertEqual({a.agent_id for a in r.agents}, {"claude", "codex"})
        # overlap is visible per-agent; the deduplicated total stays 1
        self.assertEqual(sum(a.commits for a in r.agents), 2)

    def test_duplicate_trailer_same_agent_counted_once(self):
        c = mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>",
                          "Co-Authored-By: Claude (Anthropic) <n@anthropic.com>"])
        r = build_report([c], ROSTER)
        self.assertEqual(len(r.agents), 1)
        self.assertEqual(r.agents[0].commits, 1)

    def test_session_gap_splits_sessions(self):
        commits = [mk(f"c{i}", i * 60,
                      ["Co-Authored-By: Claude <n@anthropic.com>"])
                   for i in range(1, 4)]
        commits.append(mk("d", 10 * 3600,
                          ["Co-Authored-By: Claude <n@anthropic.com>"]))
        r = build_report(commits, ROSTER)
        self.assertEqual(r.agents[0].session_count, 2)
        self.assertEqual(r.agents[0].longest_session_commits, 3)

    def test_gate_breach(self):
        commits = [mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>"]),
                   mk("b", 200)]
        r = build_report(commits, ROSTER, max_ai_ratio=0.4)
        self.assertTrue(r.gate["breached"])

    def test_gate_boundary_exactly_at_limit_is_pass(self):
        commits = [mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>"]),
                   mk("b", 200)]
        r = build_report(commits, ROSTER, max_ai_ratio=0.5)
        self.assertIsNotNone(r.gate)
        self.assertFalse(r.gate["breached"])
        self.assertEqual(r.gate["ratio"], 0.5)

    def test_traceability_every_count_has_shas(self):
        commits = [mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>"]),
                   mk("b", 200),
                   mk("c", 300, ["Co-Authored-By: Bot <b@x.io>"])]
        r = build_report(commits, ROSTER)
        for bucket in ("agent", "cosigned", "unsigned"):
            self.assertEqual(len(r.bucket_commit_shas[bucket]),
                             r.commits_by_bucket[bucket])
        self.assertEqual(r.bucket_commit_shas["agent"], ["a"])

    def test_invalid_options_rejected(self):
        with self.assertRaises(ValueError):
            build_report([mk("a", 1)], ROSTER, session_gap_minutes=0)
        with self.assertRaises(ValueError):
            build_report([mk("a", 1)], ROSTER, max_ai_ratio=2.0)
        with self.assertRaises(ValueError):
            build_report([], ROSTER)

    # ---- time window: boundary semantics pinned, like the gate ----

    def test_window_since_inclusive_until_exclusive(self):
        commits = [mk("a", 100), mk("b", 200), mk("c", 300)]
        r = build_report(commits, ROSTER, since=200, until=300)
        self.assertEqual(r.total_commits, 1)
        self.assertEqual(r.bucket_commit_shas["unsigned"], ["b"])
        self.assertEqual(r.window, {"since": 200, "until": 300})

    def test_window_empty_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_report([mk("a", 100)], ROSTER, since=200)
        self.assertIn("window-empty", str(ctx.exception))

    def test_window_inverted_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_report([mk("a", 100)], ROSTER, since=300, until=200)
        self.assertIn("window-invalid", str(ctx.exception))


class CrossTabTests(unittest.TestCase):
    """per-author x per-agent cross-tab: rows, columns, cap, traceability."""

    def test_rows_and_cells(self):
        commits = [
            mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>"],
               author=("Ana", "ana@x.io")),
            mk("b", 200, [], author=("Ana", "ana@x.io")),
            mk("c", 300, ["Co-Authored-By: Codex <x@openai.com>"],
               author=("Bob", "bob@x.io")),
            mk("d", 400, ["Co-Authored-By: Bot <bot@nowhere.io>"],
               author=("Bob", "bob@x.io")),
        ]
        r = build_report(commits, ROSTER)
        rows = {(a.name, a.email): a for a in r.authors}
        self.assertEqual(rows[("Ana", "ana@x.io")].commits, 2)
        self.assertEqual(rows[("Ana", "ana@x.io")].by_agent,
                         {"claude": 1, "unsigned": 1})
        self.assertEqual(rows[("Bob", "bob@x.io")].by_agent,
                         {"codex": 1, "cosigned": 1})
        # cell counts never exceed the row total
        for a in r.authors:
            self.assertTrue(all(n <= a.commits for n in a.by_agent.values()))

    def test_multi_agent_lands_in_every_column(self):
        # cap invariant: one commit, two columns, deduplicated row total 1.
        c = mk("a", 100,
               ["Co-Authored-By: Claude <n@anthropic.com>",
                "Co-Authored-By: Codex <x@openai.com>"])
        r = build_report([c], ROSTER)
        row = r.authors[0]
        self.assertEqual(row.commits, 1)
        self.assertEqual(row.by_agent, {"claude": 1, "codex": 1})
        self.assertEqual(sum(row.by_agent.values()), 2)

    def test_rows_sorted_by_name_email(self):
        commits = [mk("a", 100, author=("Zed", "z@x.io")),
                   mk("b", 200, author=("Amy", "a@x.io")),
                   mk("c", 300, author=("Zed", "z@x.io"))]
        r = build_report(commits, ROSTER)
        self.assertEqual([(a.name, a.email) for a in r.authors],
                         [("Amy", "a@x.io"), ("Zed", "z@x.io")])

    def test_cell_shas_traceable_and_ordered(self):
        commits = [
            mk("b", 200, ["Co-Authored-By: Claude <n@anthropic.com>"]),
            mk("a", 100, ["Co-Authored-By: Claude <n@anthropic.com>"]),
        ]
        r = build_report(commits, ROSTER)
        shas = r.authors[0].by_agent_shas["claude"]
        self.assertEqual(len(shas), r.authors[0].by_agent["claude"])
        # (authored_at, sha) order, not input order
        self.assertEqual([s[:1] for s in shas], ["a", "b"])
        # every cell list matches its count
        for a in r.authors:
            for col, n in a.by_agent.items():
                self.assertEqual(len(a.by_agent_shas[col]), n)


if __name__ == "__main__":
    unittest.main()
