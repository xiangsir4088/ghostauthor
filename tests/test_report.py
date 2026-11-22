"""Rendering: byte-stable JSON, honest formatting.

The golden-output property is the reproducibility promise at the byte
level: two Reports built from the same data render identically.
"""
import json
import unittest

from ghostauthor.engine import build_report
from ghostauthor.gitparse import parse_trailer_block
from ghostauthor.models import CommitInfo
from ghostauthor.report import (render_html, render_json, render_markdown,
                                render_sarif, render_text)
from ghostauthor.roster import validate_roster

ROSTER = validate_roster({
    "version": 1,
    "agents": [
        {"id": "claude", "display_name": "Claude (Anthropic)",
         "name_patterns": ["^Claude\\b"], "email_domains": ["anthropic.com"]},
    ],
})


def make_report():
    msg = "m\n\nCo-Authored-By: Claude <n@anthropic.com>"
    c = CommitInfo(sha="a" * 40, parent_shas=[], author_name="h",
                   author_email="h@x.io", authored_at=100, message=msg,
                   trailers=parse_trailer_block(msg),
                   lines_added=10, lines_removed=2)
    return build_report([c], ROSTER, max_ai_ratio=0.5)


class ReportRenderingTests(unittest.TestCase):
    def test_json_is_byte_stable(self):
        self.assertEqual(render_json(make_report()), render_json(make_report()))

    def test_json_keys_sorted_and_traceable(self):
        data = json.loads(render_json(make_report()))
        self.assertEqual(list(data.keys()), sorted(data.keys()))
        self.assertEqual(data["commits_by_bucket"]["agent"], 1)
        self.assertEqual(data["bucket_commit_shas"]["agent"], ["a" * 40])
        self.assertEqual(data["agents"][0]["id"], "claude")

    def test_text_and_markdown_render(self):
        t = render_text(make_report())
        m = render_markdown(make_report())
        self.assertIn("100.0%", t)
        self.assertIn("agent", m)
        self.assertIn("| agent |", m)

    def test_json_authors_traceable(self):
        data = json.loads(render_json(make_report()))
        self.assertEqual(len(data["authors"]), 1)
        row = data["authors"][0]
        self.assertEqual(row["name"], "h")
        self.assertEqual(row["commits"], 1)
        self.assertEqual(row["by_agent"], {"claude": 1})
        # traceability: the cell count expands to a commit list
        self.assertEqual(row["by_agent_shas"]["claude"], ["a" * 40])

    def test_text_and_markdown_render_cross_tab(self):
        t = render_text(make_report())
        m = render_markdown(make_report())
        self.assertIn("authors x agents:", t)
        self.assertIn("| author |", m)
        self.assertIn("| h <h@x.io> |", m)

    # ---- SARIF: schema shape + finding/gate mapping pinned ----

    @staticmethod
    def _minimal_report():
        # One unsigned commit, no gate: the empty SARIF case.
        msg = "c" * 40 + "\n\nplain message, no trailers"
        c = CommitInfo(sha="c" * 40, parent_shas=[], author_name="h",
                       author_email="h@x.io", authored_at=100, message=msg,
                       trailers=parse_trailer_block(msg),
                       lines_added=1, lines_removed=0)
        return build_report([c], ROSTER)

    def test_sarif_byte_stable_and_valid(self):
        import json as _json
        s1 = render_sarif(self._minimal_report())
        self.assertEqual(s1, render_sarif(self._minimal_report()))
        doc = _json.loads(s1)
        self.assertEqual(doc["version"], "2.1.0")
        run = doc["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "ghostauthor")
        # no findings, no gate: both lists stay empty
        self.assertEqual(run["results"], [])
        self.assertEqual(run["tool"]["driver"]["rules"], [])

    def test_sarif_maps_findings_and_gate(self):
        import json as _json
        from ghostauthor.gitparse import parse_trailer_block

        def c(sha, ts, trailer):
            msg = f"{sha}\n\n{trailer}"
            return CommitInfo(sha=sha, parent_shas=[], author_name="h",
                              author_email="h@x.io", authored_at=ts,
                              message=msg, trailers=parse_trailer_block(msg),
                              lines_added=1, lines_removed=0)

        r = build_report(
            [c("a" * 40, 100, "Co-Authored-By: Claude <n@anthropic.com>"),
             c("b" * 40, 200, "Co-Authored-By: VibeBot <vibe@x.io>")],
            ROSTER, max_ai_ratio=0.4)
        doc = _json.loads(render_sarif(r))
        run = doc["runs"][0]
        rules = {x["id"] for x in run["tool"]["driver"]["rules"]}
        self.assertIn("cosigned-unknown", rules)
        self.assertIn("ai-ratio-gate", rules)
        by_rule = {x["ruleId"]: x for x in run["results"]}
        self.assertEqual(by_rule["cosigned-unknown"]["level"], "warning")
        self.assertEqual(by_rule["ai-ratio-gate"]["level"], "error")
        loc = by_rule["cosigned-unknown"]["locations"][0]["logicalLocations"][0]
        self.assertEqual(loc["kind"], "commit")
        self.assertTrue(loc["name"].startswith("b"))

    # ---- HTML brief: admissible evidence, not a screenshot ----

    def test_html_is_byte_stable(self):
        """No timestamp, no ordering nondeterminism: same state, same bytes."""
        self.assertEqual(render_html(make_report()), render_html(make_report()))

    def test_html_is_self_contained(self):
        doc = render_html(make_report())
        self.assertNotIn("<script", doc)
        self.assertNotIn("http://", doc)
        self.assertNotIn("https://", doc)
        self.assertNotIn("<link", doc)
        self.assertIn("<!DOCTYPE html>", doc)

    def test_html_states_the_method_and_the_prohibition(self):
        doc = render_html(make_report())
        self.assertIn("no LLM, no network, no style classifier", doc)
        self.assertIn("no generation", doc)
        self.assertIn("Unsigned &ne; human", doc)

    def test_html_escapes_the_title(self):
        doc = render_html(make_report(),
                          title='<script>alert("x")</script>')
        self.assertNotIn("<script>", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_html_escapes_repository_path(self):
        report = make_report()
        report.repo_path = '/tmp/<img src=x onerror="alert(1)">'
        doc = render_html(report)
        self.assertNotIn("<img", doc)
        self.assertIn("&lt;img", doc)

    def test_html_carries_the_measured_numbers(self):
        doc = render_html(make_report())
        self.assertIn("100.0%", doc)
        self.assertIn("Claude (Anthropic)", doc)
        self.assertIn("h@x.io", doc)


if __name__ == "__main__":
    unittest.main()
