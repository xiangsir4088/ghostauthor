"""Rendering: byte-stable JSON, honest formatting.

The golden-output property is the reproducibility promise at the byte
level: two Reports built from the same data render identically.
"""
import json
import unittest

from ghostauthor.engine import build_report
from ghostauthor.gitparse import parse_trailer_block
from ghostauthor.models import CommitInfo
from ghostauthor.report import render_json, render_markdown, render_text
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


if __name__ == "__main__":
    unittest.main()
