"""Rendering: byte-stable JSON, honest formatting.

The golden-output property is the reproducibility promise at the byte
level: two Reports built from the same data render identically.
"""
import json
import unittest

from ghostauthor.engine import build_report
from ghostauthor.gitparse import parse_trailer_block
from ghostauthor.models import CommitInfo
from ghostauthor.report import (render_json, render_markdown, render_sarif,
                                render_text)
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


if __name__ == "__main__":
    unittest.main()
