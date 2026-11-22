"""Adoption diagnosis: the cold-start problem, made actionable.

The audit is exact even when it is useless: a repository whose agents never
wrote a trailer yields 0.0% and no clue why. `coverage` separates the two
causes — a signature the roster does not recognize (fix: data) and a
signature that was never written (fix: the write path) — and turns each into
a different next step.

What these tests pin:
  * zero coverage is `none`, never `minimal` (the convention has not started)
  * an unrecognized trailer is reported as a signature, not as silence
  * a known tool marker is a HINT on an unsigned commit, never an attribution
  * adoption band boundaries (0.2 / 0.8) are inclusive at the lower bound
  * author rows reconcile with the bucket totals
  * the JSON is byte-stable, and periods are ISO weeks in UTC
"""
import json
import unittest

from ghostauthor.coverage import (build_coverage, render_coverage_json,
                                  render_coverage_text)
from ghostauthor.models import CommitInfo, Trailer
from ghostauthor.roster import builtin_roster_path, load_roster

ROSTER = load_roster(builtin_roster_path())


def _commit(sha, ts, message, trailers=(), author="Sam Human"):
    return CommitInfo(sha=sha, parent_shas=[], author_name=author,
                      author_email="sam@example.com", authored_at=ts,
                      message=message, trailers=list(trailers),
                      lines_added=1, lines_removed=0)


def _claude_trailer():
    return Trailer(raw="Co-Authored-By: Claude <noreply@anthropic.com>",
                   name="Claude", email="noreply@anthropic.com")


DAY = 86400
T0 = 1767225600  # 2026-01-01T00:00:00Z


class AdoptionBandTests(unittest.TestCase):
    def _cov_with(self, signed, unsigned):
        commits = []
        for i in range(signed):
            commits.append(_commit(f"a{i:03d}", T0 + i,
                                   "feat: x\n\nCo-Authored-By: Claude "
                                   "<noreply@anthropic.com>",
                                   [_claude_trailer()]))
        for i in range(unsigned):
            commits.append(_commit(f"b{i:03d}", T0 + signed + i, "chore: y"))
        return build_coverage(commits, ROSTER)

    def test_zero_coverage_is_none_not_minimal(self):
        cov = self._cov_with(0, 5)
        self.assertEqual(cov.adoption, "none")
        self.assertEqual(cov.coverage_ratio, 0.0)

    def test_partial_boundary_is_inclusive(self):
        # 2/10 = 0.2 -> partial (>= 0.2), 1/10 -> minimal
        self.assertEqual(self._cov_with(2, 8).adoption, "partial")
        self.assertEqual(self._cov_with(1, 9).adoption, "minimal")

    def test_adopted_boundary_is_inclusive(self):
        # 8/10 = 0.8 -> adopted (>= 0.8), 7/10 -> partial
        self.assertEqual(self._cov_with(8, 2).adoption, "adopted")
        self.assertEqual(self._cov_with(7, 3).adoption, "partial")

    def test_full_coverage_suggests_a_gate(self):
        cov = self._cov_with(3, 0)
        self.assertEqual(cov.adoption, "adopted")
        self.assertIn("enforce-gate", [a.code for a in cov.actions])
        self.assertNotIn("install-hook", [a.code for a in cov.actions])


class DiagnosisTests(unittest.TestCase):
    def test_unrecognized_signature_is_not_silence(self):
        """A real trailer that no roster rule claims is the cheapest fix."""
        vibebot = Trailer(raw="Co-Authored-By: VibeBot <vibe@example.com>",
                          name="VibeBot", email="vibe@example.com")
        cov = build_coverage(
            [_commit("a" * 40, T0, "feat: x\n\n" + vibebot.raw, [vibebot])],
            ROSTER)
        self.assertEqual(cov.buckets["cosigned"], 1)
        self.assertEqual(cov.signals, [])
        self.assertEqual(len(cov.unmatched_trailers), 1)
        self.assertEqual(cov.unmatched_trailers[0].name, "VibeBot")
        self.assertEqual(cov.unmatched_trailers[0].email_domain, "example.com")
        self.assertIn("add-roster-rule", [a.code for a in cov.actions])

    def test_known_marker_is_a_hint_never_an_attribution(self):
        cov = build_coverage(
            [_commit("b" * 40, T0,
                     "feat: add parser\n\n🤖 Generated with [Claude Code]"),
             _commit("c" * 40, T0 + 1,
                     "fix: off by one\n\nGenerated with [Claude Code]")],
            ROSTER)
        self.assertEqual(cov.adoption, "none")
        self.assertEqual(cov.buckets["unsigned"], 2)
        self.assertEqual(len(cov.signals), 1)
        signal = cov.signals[0]
        self.assertEqual(signal.agent_id, "claude-code")
        self.assertEqual(signal.commits, 2)      # one marker per commit
        self.assertEqual(len(signal.sample_shas), 2)
        # The hint is a hint: no bucket was changed by it.
        self.assertEqual(cov.signed_commits, 0)
        self.assertEqual([a.code for a in cov.actions][0], "install-hook")

    def test_no_marker_no_claim(self):
        """A plain unsigned commit yields silence — never a guess."""
        cov = build_coverage([_commit("d" * 40, T0, "chore: bump deps")],
                             ROSTER)
        self.assertEqual(cov.signals, [])
        self.assertEqual(cov.unmatched_trailers, [])
        self.assertEqual([a.code for a in cov.actions], ["no-signatures"])

    def test_partial_adoption_refuses_a_gate_recommendation(self):
        commits = [_commit(f"e{i:03d}", T0 + i, "chore: x") for i in range(4)]
        commits.append(_commit("f" * 40, T0 + 9,
                               "feat: y", [_claude_trailer()]))
        cov = build_coverage(commits, ROSTER)
        codes = [a.code for a in cov.actions]
        self.assertIn("close-the-gap", codes)
        self.assertNotIn("enforce-gate", codes)


class InvariantTests(unittest.TestCase):
    def _mixed(self):
        return [
            _commit("a" * 40, T0, "feat: a", [_claude_trailer()]),
            _commit("b" * 40, T0 + DAY, "chore: b"),
            _commit("c" * 40, T0 + 2 * DAY, "feat: c\n\nGenerated with Cursor"),
        ]

    def test_author_rows_reconcile_with_buckets(self):
        cov = build_coverage(self._mixed(), ROSTER)
        self.assertEqual(sum(a.commits for a in cov.by_author),
                         cov.total_commits)
        for a in cov.by_author:
            self.assertEqual(a.signed + a.cosigned + a.unsigned, a.commits)
        self.assertEqual(sum(a.signed for a in cov.by_author),
                         cov.signed_commits)

    def test_period_is_iso_week_in_utc(self):
        cov = build_coverage(self._mixed(), ROSTER)
        self.assertEqual([p.period for p in cov.by_period], ["2026-W01"])
        self.assertEqual(cov.by_period[0].commits, 3)

    def test_window_semantics_match_the_audit(self):
        commits = self._mixed()  # T0, T0+DAY, T0+2*DAY
        cov = build_coverage(commits, ROSTER, since=T0 + DAY,
                             until=T0 + 2 * DAY)
        self.assertEqual(cov.total_commits, 1)  # since inclusive, until exclusive
        self.assertEqual(cov.by_period[0].signed, 0)

    def test_empty_window_is_an_error_not_a_zero_report(self):
        with self.assertRaises(ValueError) as ctx:
            build_coverage(self._mixed(), ROSTER, since=T0 + 10 * DAY)
        self.assertIn("window-empty", str(ctx.exception))

    def test_json_is_byte_stable(self):
        cov1 = build_coverage(self._mixed(), ROSTER, repo_path="/r")
        cov2 = build_coverage(list(reversed(self._mixed())), ROSTER,
                              repo_path="/r")
        self.assertEqual(render_coverage_json(cov1), render_coverage_json(cov2))
        payload = json.loads(render_coverage_json(cov1))
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["buckets"],
                         {"agent": 1, "cosigned": 0, "unsigned": 2})

    def test_text_renders_the_verdict_first(self):
        text = render_coverage_text(build_coverage(self._mixed(), ROSTER))
        self.assertIn("1 of 3 commits", text)
        self.assertIn("adoption: PARTIAL", text)
        self.assertIn("next steps", text)


if __name__ == "__main__":
    unittest.main()
