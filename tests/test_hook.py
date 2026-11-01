"""Commit-msg hook (v1.1): signing at commit time, no sha rewrite.

The hook reuses the write-path pure core (resolve_agent + plan_sign);
what changes is the message source: a commit message FILE instead of
`git log -1`. Pinned here as unit tests (sign_message_file) and CLI
subprocess tests (a real `git commit` fires the installed hook), and
re-checked machine-side in verify.py step 20.

Boundaries: the hook is repo-level, installed only by explicit
`install-hook`, and is marker-protected — a foreign hook is never
overwritten or removed. `--no-verify` remains the documented escape
hatch (bypassing it leaves an unsigned commit, which the audit shows).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ghostauthor.roster import validate_roster
from ghostauthor.signer import HOOK_MARKER, sign_message_file

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
ROSTER = validate_roster(json.loads(json.dumps(ROSTER_RAW)))

CLAUDE_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
CODEX_TRAILER = "Co-Authored-By: OpenAI Codex <noreply@openai.com>"


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
                   capture_output=True, text=True, encoding="utf-8")


def _cli(*args, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(ROOT))


class SignMessageFileTests(unittest.TestCase):
    """The hook core, without git: a file instead of `git log -1`."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ghostauthor-msgfile-")
        self.addCleanup(tmp.cleanup)
        self.msg = Path(tmp.name) / "msg"

    def _write(self, content: str) -> None:
        # bytes, not text: text mode would translate \n to \r\n on
        # Windows and hide the byte-preservation behavior under test
        self.msg.write_bytes(content.encode("utf-8"))

    def test_fresh_message_gets_trailer_written_to_file(self):
        self._write("hello\n")
        trailer, new, no_op = sign_message_file(self.msg, ROSTER, "claude")
        self.assertFalse(no_op)
        self.assertEqual(trailer, CLAUDE_TRAILER)
        self.assertEqual(self.msg.read_bytes(), new.encode("utf-8"))
        self.assertIn(CLAUDE_TRAILER, new)

    def test_idempotent_when_same_agent_already_signed(self):
        msg = f"hello\n\n{CLAUDE_TRAILER}\n"
        self._write(msg)
        _trailer, new, no_op = sign_message_file(self.msg, ROSTER, "claude")
        self.assertTrue(no_op)
        self.assertEqual(self.msg.read_bytes(), msg.encode("utf-8"),
                         "no-op must leave the file byte-identical")

    def test_conflict_refused_and_file_untouched(self):
        msg = f"hello\n\n{CODEX_TRAILER}\n"
        self._write(msg)
        with self.assertRaises(Exception) as ctx:
            sign_message_file(self.msg, ROSTER, "claude")
        self.assertIn("sign-conflict", str(ctx.exception))
        self.assertEqual(self.msg.read_bytes(), msg.encode("utf-8"),
                         "a refused sign must not touch the file")

    def test_non_utf8_message_refused_and_file_untouched(self):
        raw = b"hello\n\xff\xfe binary garbage\n"
        self.msg.write_bytes(raw)
        with self.assertRaises(Exception) as ctx:
            sign_message_file(self.msg, ROSTER, "claude")
        self.assertIn("sign-msg-undecodable", str(ctx.exception))
        self.assertEqual(self.msg.read_bytes(), raw)

    def test_unknown_agent_refused(self):
        self._write("hello\n")
        with self.assertRaises(Exception) as ctx:
            sign_message_file(self.msg, ROSTER, "nope")
        self.assertIn("sign-agent-unknown", str(ctx.exception))
        self.assertEqual(self.msg.read_text(encoding="utf-8"), "hello\n")


class HookCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ghostauthor-hook-test-")
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
        self.hook = self.repo / ".git" / "hooks" / "commit-msg"

    def _head(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
            env=self.env).stdout.strip()

    def _message(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "log", "-1", "--format=%B"],
            capture_output=True, text=True, encoding="utf-8",
            env=self.env).stdout

    def _install(self, *extra) -> subprocess.CompletedProcess:
        return _cli("install-hook", self.repo, "--roster", self.roster,
                    *extra, env=self.env)

    def test_install_creates_marker_hook(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.hook.is_file())
        text = self.hook.read_text(encoding="utf-8")
        self.assertIn(HOOK_MARKER, text)
        self.assertIn("--agent claude", text)
        # the custom roster path is pinned into the generated script
        self.assertIn(str(self.roster.resolve()), text)

    def test_real_commit_is_signed_and_audit_roundtrips(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.repo / "f.txt").write_text("2\n", encoding="utf-8")
        _git(self.repo, "add", "-A", env=self.env)
        _git(self.repo, "commit", "-m", "feat: x", env=self.env)
        self.assertIn(CLAUDE_TRAILER, self._message())
        # the load-bearing invariant through the hook: sign at commit
        # time -> audit attributes to the same roster agent
        audit = _cli(self.repo, "--format", "json", "--roster", self.roster,
                     env=self.env)
        self.assertEqual(audit.returncode, 0, audit.stderr)
        data = json.loads(audit.stdout)
        self.assertEqual(data["commits_by_bucket"]["agent"], 1)
        self.assertEqual(data["agents"][0]["id"], "claude")

    def test_hook_is_idempotent_on_presigned_message(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.repo / "f.txt").write_text("2\n", encoding="utf-8")
        _git(self.repo, "add", "-A", env=self.env)
        _git(self.repo, "commit", "-m", f"presigned\n\n{CLAUDE_TRAILER}",
             env=self.env)
        message = self._message()
        self.assertEqual(message.count(CLAUDE_TRAILER), 1,
                         "the hook must never add a second trailer")

    def test_conflict_blocks_commit_and_leaves_no_commit(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.repo / "f.txt").write_text("2\n", encoding="utf-8")
        _git(self.repo, "add", "-A", env=self.env)
        sha = self._head()
        blocked = subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m",
             f"conflicting\n\n{CODEX_TRAILER}"],
            capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertNotEqual(blocked.returncode, 0,
                            "a commit-msg failure must abort the commit")
        self.assertIn("sign-conflict", blocked.stderr)
        self.assertEqual(self._head(), sha,
                         "a blocked commit must not exist")

    def test_no_verify_bypasses_hook_and_stays_visible(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.repo / "f.txt").write_text("2\n", encoding="utf-8")
        _git(self.repo, "add", "-A", env=self.env)
        _git(self.repo, "commit", "--no-verify", "-m", "bypass", env=self.env)
        self.assertNotIn(CLAUDE_TRAILER, self._message())
        # the bypass is not hidden: the audit shows it as unsigned
        audit = _cli(self.repo, "--format", "json", "--roster", self.roster,
                     env=self.env)
        data = json.loads(audit.stdout)
        self.assertEqual(data["commits_by_bucket"]["unsigned"], 2)

    def test_uninstall_removes_ours_only(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = _cli("uninstall-hook", self.repo, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removed", proc.stdout)
        self.assertFalse(self.hook.exists())
        # idempotent: nothing to remove is not an error
        proc = _cli("uninstall-hook", self.repo, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to remove", proc.stdout)
        # a foreign hook is never removed
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        self.hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        proc = _cli("uninstall-hook", self.repo, env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("hook-not-ours", proc.stderr)
        self.assertTrue(self.hook.exists())

    def test_install_refuses_foreign_hook(self):
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        foreign = "#!/bin/sh\nexit 0\n"
        self.hook.write_text(foreign, encoding="utf-8")
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("hook-exists", proc.stderr)
        self.assertEqual(self.hook.read_text(encoding="utf-8"), foreign,
                         "a foreign hook must be left byte-identical")

    def test_install_replaces_own_hook_idempotently(self):
        proc = self._install("--agent", "claude")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self._install("--agent", "claude")
        self.assertEqual(
            proc.returncode, 0,
            proc.stderr + " (reinstalling over our own hook must be a "
            "no-harm replace)")
        self.assertIn(HOOK_MARKER,
                      self.hook.read_text(encoding="utf-8"))

    def test_builtin_install_pins_no_roster_flag(self):
        proc = _cli("install-hook", self.repo, "--agent", "claude",
                    env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.hook.read_text(encoding="utf-8")
        self.assertNotIn("--roster", text)
        self.assertIn(HOOK_MARKER, text)

    def test_not_a_repo_exits_2(self):
        # a path that is not a git repo
        proc = _cli("install-hook", self.repo / "nope", "--roster",
                    self.roster, "--agent", "claude", env=self.env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not-a-repo", proc.stderr)

    def test_unknown_agent_exits_2(self):
        proc = self._install("--agent", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-agent-unknown", proc.stderr)
        self.assertFalse(self.hook.exists())

    def test_agent_without_signing_exits_2(self):
        proc = self._install("--agent", "plain")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sign-no-signing", proc.stderr)
        self.assertFalse(self.hook.exists())


if __name__ == "__main__":
    unittest.main()
