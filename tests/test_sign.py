"""Write path (v1.0): opt-in trailer signing and its boundaries.

Every new semantic carries a negative test (project rule). CLI-level
behaviors — dry-run default-off, `--commit` apply, audit round-trip,
idempotency, conflict — are pinned here as subprocess tests and re-checked
machine-side in verify.py step 19.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ghostauthor.roster import validate_roster
from ghostauthor.signer import (SignError, _append_trailer, plan_sign,
                                resolve_agent)

ROOT = Path(__file__).resolve().parent.parent

ROSTER_RAW = {
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
ROSTER = validate_roster(copy.deepcopy(ROSTER_RAW))


class TrailerInsertionTests(unittest.TestCase):
    def test_subject_only_gets_new_block(self):
        self.assertEqual(_append_trailer("hello\n", "Co-Authored-By: X <x>"),
                         "hello\n\nCo-Authored-By: X <x>\n")

    def test_no_trailing_newline_normalized(self):
        self.assertEqual(_append_trailer("hello", "Co-Authored-By: X <x>"),
                         "hello\n\nCo-Authored-By: X <x>\n")

    def test_prose_tail_gets_new_block(self):
        msg = "hello\n\nsome prose paragraph"
        self.assertEqual(_append_trailer(msg, "Co-Authored-By: X <x>"),
                         "hello\n\nsome prose paragraph\n\n"
                         "Co-Authored-By: X <x>\n")

    def test_trailer_block_is_joined_not_duplicated(self):
        msg = "hello\n\nRefs: 123"
        self.assertEqual(_append_trailer(msg, "Co-Authored-By: X <x>"),
                         "hello\n\nRefs: 123\nCo-Authored-By: X <x>\n")


class PlanSignTests(unittest.TestCase):
    def test_fresh_message_gets_trailer(self):
        trailer, new, no_op = plan_sign("hello\n", ROSTER, ROSTER.agents[0])
        self.assertFalse(no_op)
        self.assertEqual(trailer,
                         "Co-Authored-By: Claude <noreply@anthropic.com>")
        self.assertIn(trailer, new)

    def test_idempotent_when_same_agent_already_signed(self):
        msg = "hello\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
        _, new, no_op = plan_sign(msg, ROSTER, ROSTER.agents[0])
        self.assertTrue(no_op)
        self.assertEqual(new, msg)

    def test_conflict_when_other_roster_agent_signed(self):
        msg = "hello\n\nCo-Authored-By: OpenAI Codex <noreply@openai.com>"
        with self.assertRaises(SignError) as ctx:
            plan_sign(msg, ROSTER, ROSTER.agents[0])
        self.assertIn("sign-conflict", str(ctx.exception))

    def test_non_roster_cosigner_does_not_conflict(self):
        # cosigned territory: the read side keeps it in its own bucket,
        # so adding a roster signature alongside is not a conflict
        msg = "hello\n\nCo-Authored-By: VibeBot <vibe@example.com>"
        _, new, no_op = plan_sign(msg, ROSTER, ROSTER.agents[0])
        self.assertFalse(no_op)
        self.assertIn("VibeBot", new)


class ResolveAgentTests(unittest.TestCase):
    def test_unknown_agent_refused(self):
        with self.assertRaises(SignError) as ctx:
            resolve_agent(ROSTER, "nope")
        self.assertIn("sign-agent-unknown", str(ctx.exception))

    def test_agent_without_signing_refused(self):
        with self.assertRaises(SignError) as ctx:
            resolve_agent(ROSTER, "plain")
        self.assertIn("sign-no-signing", str(ctx.exception))

    def test_ambiguous_identity_refused(self):
        # first-match-wins: a broad pattern earlier in file order would
        # misattribute the signature, so signing is refused, not papered over
        roster = validate_roster({
            "version": 1,
            "agents": [
                {"id": "wild", "display_name": "Wild",
                 "name_patterns": [".*"], "email_domains": [],
                 "signing": {"name": "Wild", "email": "wild@wild.example"}},
                {"id": "claude", "display_name": "Claude (Anthropic)",
                 "name_patterns": ["^Claude\\b"],
                 "email_domains": ["anthropic.com"],
                 "signing": {"name": "Claude",
                             "email": "noreply@anthropic.com"}},
            ],
        })
        with self.assertRaises(SignError) as ctx:
            resolve_agent(roster, "claude")
        self.assertIn("sign-ambiguous", str(ctx.exception))


def _env() -> dict:
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "Sam Human", "GIT_AUTHOR_EMAIL": "sam@example.com",
        "GIT_COMMITTER_NAME": "Sam Human",
        "GIT_COMMITTER_EMAIL": "sam@example.com",
    })
    return e


def _git(repo: Path, *args: str, env: dict) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True, text=True)


def _cli(*args, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(ROOT))


class SignCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ghostauthor-sign-test-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.env = _env()
        self.repo = root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main", env=self.env)
        (self.repo / "f.txt").write_text("1\n", encoding="utf-8")
        _git(self.repo, "add", "-A", env=self.env)
        _git(self.repo, "commit", "-m", "initial", env=self.env)
        self.roster = root / "roster.json"
        self.roster.write_text(json.dumps(ROSTER_RAW), encoding="utf-8")

    def _head(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, env=self.env).stdout.strip()

    def _sign(self, *extra):
        return _cli("sign", self.repo, "--roster", self.roster, *extra,
                    env=self.env)

    def test_dry_run_is_default_and_writes_nothing(self):
        sha = self._head()
        proc = self._sign("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[dry-run]", proc.stdout)
        self.assertEqual(self._head(), sha, "dry-run must not amend HEAD")

    def test_commit_applies_and_audit_roundtrips(self):
        sha = self._head()
        proc = self._sign("--agent", "claude", "--commit")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        new_sha = self._head()
        self.assertNotEqual(new_sha, sha)
        # the load-bearing invariant: sign -> audit attributes to the agent
        audit = _cli(self.repo, "--format", "json", "--roster", self.roster,
                     env=self.env)
        data = json.loads(audit.stdout)
        self.assertEqual(data["commits_by_bucket"]["agent"], 1)
        self.assertEqual(data["agents"][0]["id"], "claude")
        self.assertEqual(data["bucket_commit_shas"]["agent"], [new_sha])

    def test_second_sign_is_idempotent_noop(self):
        self._sign("--agent", "claude", "--commit")
        sha = self._head()
        proc = self._sign("--agent", "claude", "--commit")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already signed", proc.stdout)
        self.assertEqual(self._head(), sha, "no-op must not touch HEAD")

    def test_conflict_refused_and_repo_untouched(self):
        self._sign("--agent", "claude", "--commit")
        sha = self._head()
        proc = self._sign("--agent", "codex", "--commit")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-conflict", proc.stderr)
        self.assertEqual(self._head(), sha, "conflict must not modify")

    def test_unknown_agent_exits_2(self):
        proc = self._sign("--agent", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-agent-unknown", proc.stderr)

    def test_agent_without_signing_exits_2(self):
        proc = self._sign("--agent", "plain")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-no-signing", proc.stderr)

    def test_not_a_repo_exits_2(self):
        proc = _cli("sign", self.repo / "nope", "--roster", self.roster,
                    "--agent", "claude", env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not-a-repo", proc.stderr)

    def test_unborn_head_exits_2(self):
        repo2 = self.repo.parent / "unborn"
        repo2.mkdir()
        _git(repo2, "init", "-b", "main", env=self.env)
        proc = _cli("sign", repo2, "--roster", self.roster, "--agent",
                    "claude", env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no-commits", proc.stderr)

    def test_ambiguous_broad_pattern_refused(self):
        wild = self.roster.parent / "roster-wild.json"
        wild.write_text(json.dumps({
            "version": 1,
            "agents": [
                {"id": "wild", "display_name": "Wild",
                 "name_patterns": [".*"], "email_domains": [],
                 "signing": {"name": "Wild", "email": "wild@wild.example"}},
                ROSTER_RAW["agents"][0],
            ],
        }), encoding="utf-8")
        sha = self._head()
        proc = _cli("sign", self.repo, "--roster", wild, "--agent", "claude",
                    env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-ambiguous", proc.stderr)
        self.assertEqual(self._head(), sha)


if __name__ == "__main__":
    unittest.main()
