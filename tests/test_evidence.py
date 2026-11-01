"""Per-commit evidence (v1.2): the audit's verdict, expanded per commit.

The load-bearing property is AGREEMENT: for every commit, the evidence's
attribution equals the audit's bucket. Both consume
`engine.attribute_commit`, and these tests pin that they cannot diverge —
verify.py step 21 re-checks it machine-side.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.make_fixtures import build_mixed
from ghostauthor.roster import builtin_roster_path, validate_roster

ROOT = Path(__file__).resolve().parent.parent


def _env():
    return dict(os.environ,
                GIT_AUTHOR_NAME="Audit",
                GIT_AUTHOR_EMAIL="audit@example.com",
                GIT_COMMITTER_NAME="Audit",
                GIT_COMMITTER_EMAIL="audit@example.com")


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", env=_env(),
        cwd=str(ROOT))


class EvidenceCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(
            prefix="ghostauthor-evidence-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = build_mixed(Path(self._tmp.name) / "mixed")

    def _evidence_json(self, *shas):
        p = _cli("evidence", self.repo, *(shas or ()), "--format", "json")
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_attribution_agrees_with_audit_for_every_commit(self):
        audit = _cli(self.repo, "--format", "json")
        self.assertEqual(audit.returncode, 0, audit.stderr)
        data = json.loads(audit.stdout)
        bucket_of = {sha: bucket
                     for bucket, shas in data["bucket_commit_shas"].items()
                     for sha in shas}
        e = self._evidence_json()
        self.assertEqual(e["commits"], data["total_commits"])
        self.assertEqual({x["sha"] for x in e["evidences"]},
                         set(bucket_of),
                         "evidence must cover exactly the audited commits")
        for entry in e["evidences"]:
            self.assertEqual(
                entry["attribution"], bucket_of[entry["sha"]],
                f"{entry['sha'][:12]}: evidence says "
                f"{entry['attribution']}, audit says "
                f"{bucket_of[entry['sha']]}")

    def test_agent_trailers_carry_rule_and_reason(self):
        e = self._evidence_json()
        agent_entries = [x for x in e["evidences"]
                         if x["attribution"] == "agent"]
        self.assertTrue(agent_entries, "the mixed fixture has agent commits")
        self.assertTrue({a for x in agent_entries for a in x["agents"]}
                        <= {"claude", "codex"})
        for x in agent_entries:
            for t in x["trailers"]:
                if t["agent"] is not None:
                    self.assertTrue(t["match_reason"],
                                    "a match must name its selector")

    def test_unknown_sha_exits_2(self):
        p = _cli("evidence", self.repo,
                 "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        self.assertEqual(p.returncode, 2)
        self.assertIn("sha-unknown", p.stderr)

    def test_not_a_repo_exits_2(self):
        empty = Path(self._tmp.name) / "notarepo"
        empty.mkdir()
        p = _cli("evidence", empty)
        self.assertEqual(p.returncode, 2)
        self.assertIn("not-a-repo", p.stderr)

    def test_json_is_byte_stable(self):
        a = _cli("evidence", self.repo, "--format", "json")
        b = _cli("evidence", self.repo, "--format", "json")
        self.assertEqual(a.stdout, b.stdout)

    def test_explicit_shas_keep_input_order(self):
        e = self._evidence_json()
        shas = [x["sha"] for x in e["evidences"]]
        self.assertGreater(len(shas), 1)
        e2 = self._evidence_json(shas[1], shas[0])
        self.assertEqual([x["sha"] for x in e2["evidences"]],
                         [shas[1], shas[0]])

    def test_bundle_reproducible_and_consistent(self):
        b1 = Path(self._tmp.name) / "bundle1"
        b2 = Path(self._tmp.name) / "bundle2"
        p = _cli("evidence", self.repo, "--bundle", b1)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = _cli("evidence", self.repo, "--bundle", b2)
        self.assertEqual(p.returncode, 0, p.stderr)
        for name in ("report.json", "evidence.json", "manifest.txt",
                     "roster/agents.json"):
            self.assertTrue((b1 / name).is_file(), f"missing {name}")
            self.assertTrue((b2 / name).is_file(), f"missing {name}")
            self.assertEqual(
                (b1 / name).read_bytes(), (b2 / name).read_bytes(),
                f"{name}: same state + same path must give the same bytes")
        # report.json is the audit's report (canonical LF; subprocess text
        # reading is newline-normalized, so encode and compare directly)
        audit = _cli(self.repo, "--format", "json")
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertEqual(
            (b1 / "report.json").read_bytes(),
            audit.stdout.encode("utf-8"),
            "bundle report.json must equal the audit's report bytes")
        # the manifest's sha256s describe the files on disk
        manifest = (b1 / "manifest.txt").read_text(encoding="utf-8")
        for name in ("report.json", "evidence.json", "roster/agents.json"):
            digest = hashlib.sha256((b1 / name).read_bytes()).hexdigest()
            self.assertIn(f"  {name}  {digest}", manifest)
        # the roster snapshot is the roster that was actually used
        self.assertEqual(
            (b1 / "roster" / "agents.json").read_bytes(),
            builtin_roster_path().read_bytes())
        # re-running refuses to overwrite evidence
        p = _cli("evidence", self.repo, "--bundle", b1)
        self.assertEqual(p.returncode, 2)
        self.assertIn("bundle-exists", p.stderr)


class MatchDetailTests(unittest.TestCase):
    """roster.match_detail: the reason-carrying projection of roster.match."""

    def setUp(self):
        self.roster = validate_roster({
            "version": 1,
            "agents": [
                {"id": "claude", "display_name": "Claude",
                 "name_patterns": [r"^Claude\b"],
                 "email_domains": ["anthropic.com"]},
                {"id": "codex", "display_name": "Codex",
                 "name_patterns": [r"^OpenAI Codex\b"],
                 "email_domains": ["openai.com"]},
            ],
        })

    def test_match_and_match_detail_agree(self):
        for name, dom in [("Claude", "anthropic.com"), ("Claude", ""),
                          ("OpenAI Codex", ""), ("VibeBot", "vibe.example"),
                          ("Claude", "other.com"), ("Human", "openai.com")]:
            by_match = self.roster.match(name, dom)
            detail = self.roster.match_detail(name, dom)
            self.assertEqual(
                (by_match.id if by_match else None),
                (detail[0].id if detail else None),
                f"divergence for ({name!r}, {dom!r})")

    def test_reason_names_the_exact_selector(self):
        rule, reason = self.roster.match_detail("Claude", "")
        self.assertEqual(rule.id, "claude")
        self.assertIn("name pattern", reason)
        rule, reason = self.roster.match_detail("Human", "anthropic.com")
        self.assertEqual(rule.id, "claude")
        self.assertEqual(reason, "email domain 'anthropic.com'")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.roster.match_detail(
            "VibeBot", "vibe.example"))


if __name__ == "__main__":
    unittest.main()
