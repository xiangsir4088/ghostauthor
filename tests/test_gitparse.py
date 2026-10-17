"""Trailer parsing: git trailer-block semantics, including the negative
cases where a line only LOOKS like a signature.
"""
import unittest

from ghostauthor.gitparse import parse_trailer_block


class TrailerBlockTests(unittest.TestCase):
    def test_plain_trailer(self):
        msg = ("feat: thing\n\n"
               "Signed-off-by: me <me@x.io>\n"
               "Co-Authored-By: Claude <noreply@anthropic.com>\n")
        ts = parse_trailer_block(msg)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0].name, "Claude")
        self.assertEqual(ts[0].email, "noreply@anthropic.com")

    def test_trailer_without_email(self):
        ts = parse_trailer_block("fix\n\nCo-Authored-By: Ghost\n")
        self.assertEqual(ts[0].name, "Ghost")
        self.assertEqual(ts[0].email, "")  # missing == empty, never an error

    def test_multiple_trailers(self):
        msg = "m\n\nCo-Authored-By: A <a@a.io>\nCo-Authored-By: B <b@b.io>\n"
        self.assertEqual([t.name for t in parse_trailer_block(msg)], ["A", "B"])

    def test_key_is_case_insensitive(self):
        ts = parse_trailer_block(
            "m\n\nco-authored-by: Claude <noreply@anthropic.com>\n")
        self.assertEqual(ts[0].name, "Claude")

    def test_crlf_message(self):
        msg = "m\r\n\r\nCo-Authored-By: Claude <noreply@anthropic.com>\r\n"
        self.assertEqual(parse_trailer_block(msg)[0].name, "Claude")

    def test_continuation_kept_in_raw_only(self):
        msg = ("m\n\n"
               "Co-Authored-By: Claude <noreply@anthropic.com>\n"
               "  (continued line)\n")
        ts = parse_trailer_block(msg)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0].name, "Claude")
        self.assertEqual(ts[0].email, "noreply@anthropic.com")
        self.assertIn("continued", ts[0].raw)

    # ---- negative: prose that looks like a trailer must be ignored ----

    def test_trailer_in_body_is_ignored(self):
        msg = ("m\n\n"
               "Co-Authored-By: Claude <noreply@anthropic.com>\n\n"
               "some other paragraph here\n")
        self.assertEqual(parse_trailer_block(msg), [])

    def test_prose_last_paragraph_is_ignored(self):
        msg = ("m\n\n"
               "Co-Authored-By: Claude <noreply@anthropic.com>\n"
               "but see below for more context\n")
        self.assertEqual(parse_trailer_block(msg), [])

    def test_no_trailer_block_at_all(self):
        self.assertEqual(parse_trailer_block("just a message\n\nno trailers"), [])

    def test_empty_message(self):
        self.assertEqual(parse_trailer_block(""), [])
        self.assertEqual(parse_trailer_block("\n\n"), [])

    # ---- log framing: the parse contract is pinned, not assumed ----

    def test_parse_log_framing_roundtrip(self):
        from ghostauthor import gitparse
        g = gitparse
        sha1, sha2 = "a" * 40, "b" * 40
        out = (
            g.RECORD_START + sha1 + g.FIELD_SEP + "" + g.FIELD_SEP +
            "Sam" + g.FIELD_SEP + "sam@x.io" + g.FIELD_SEP + "1767225600" +
            g.FIELD_SEP + g.RECORD_SEP +
            "first commit\n" + g.RECORD_SEP +
            "1\t0\tmain.py" + "\n" +
            g.RECORD_START + sha2 + g.FIELD_SEP + sha1 + g.FIELD_SEP +
            "Sam" + g.FIELD_SEP + "sam@x.io" + g.FIELD_SEP + "1767229200" +
            g.FIELD_SEP + g.RECORD_SEP +
            "second\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n" +
            g.RECORD_SEP + "2\t1\tparser.py"
        )
        commits = g._parse_log(out)
        self.assertEqual([c.sha for c in commits], [sha1, sha2])
        self.assertEqual(commits[0].lines_added, 1)
        self.assertEqual(len(commits[1].trailers), 1)
        self.assertEqual(commits[1].trailers[0].name, "Claude")

    def test_parse_log_rejects_unframed_output(self):
        from ghostauthor import gitparse
        with self.assertRaises(gitparse.GitError) as ctx:
            gitparse._parse_log("not framed at all")
        self.assertEqual(ctx.exception.code, "malformed-output")

    def test_unborn_head_is_no_commits(self):
        # A repo with everything staged but nothing committed must be
        # classified as no-commits (exit 2), not a generic git failure.
        import subprocess
        import tempfile
        from pathlib import Path
        from ghostauthor.gitparse import GitError, iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "empty"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)],
                           check=True, capture_output=True)
            with self.assertRaises(GitError) as ctx:
                iter_commits(str(repo))
            self.assertEqual(ctx.exception.code, "no-commits")


class RefExtractionTests(unittest.TestCase):
    """Ref scoping (additive over HEAD), pinned against a real repository."""

    def _build(self, root):
        import os
        import shutil
        import subprocess
        from pathlib import Path
        repo = Path(root) / "refrepo"
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True)

        def commit(ts, msg, content):
            (repo / "f.txt").write_text(content, encoding="utf-8")
            e = dict(os.environ)
            e.update({"GIT_AUTHOR_NAME": "Sam",
                      "GIT_AUTHOR_EMAIL": "sam@example.com",
                      "GIT_COMMITTER_NAME": "Sam",
                      "GIT_COMMITTER_EMAIL": "sam@example.com",
                      "GIT_AUTHOR_DATE": f"{ts} +0000",
                      "GIT_COMMITTER_DATE": f"{ts} +0000"})
            subprocess.run(["git", "-C", str(repo), "add", "-A"],
                           check=True, env=e, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg],
                           check=True, env=e, capture_output=True)

        commit(1767225600, "one", "1\n")
        commit(1767229200,
               "two\n\nCo-Authored-By: Claude <noreply@anthropic.com>", "2\n")
        # `side` branches off `two` and adds one more commit; main stays at two.
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"],
                       check=True, capture_output=True)
        commit(1767232800, "side change", "3\n")
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"],
                       check=True, capture_output=True)
        return repo

    def test_ref_scopes_to_branch(self):
        import tempfile
        from ghostauthor.gitparse import iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = self._build(td)
            head = iter_commits(str(repo))
            self.assertEqual(len(head), 2)
            side = iter_commits(str(repo), ref="side")
            self.assertEqual(len(side), 3)
            self.assertEqual(len({c.sha for c in side}
                                 - {c.sha for c in head}), 1)

    def test_all_refs_covers_every_branch(self):
        import tempfile
        from ghostauthor.gitparse import iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = self._build(td)
            self.assertEqual(len(iter_commits(str(repo), all_refs=True)), 3)

    def test_ref_conflict_is_rejected(self):
        import tempfile
        from ghostauthor.gitparse import iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = self._build(td)
            with self.assertRaises(ValueError) as ctx:
                iter_commits(str(repo), all_refs=True, ref="main")
            self.assertIn("ref-conflict", str(ctx.exception))

    def test_ref_starting_with_dash_is_rejected(self):
        import tempfile
        from ghostauthor.gitparse import iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = self._build(td)
            with self.assertRaises(ValueError) as ctx:
                iter_commits(str(repo), ref="-e")
            self.assertIn("ref-invalid", str(ctx.exception))

    def test_missing_path_is_not_a_repo(self):
        import tempfile
        from pathlib import Path
        from ghostauthor.gitparse import GitError, iter_commits
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(GitError) as ctx:
                iter_commits(str(Path(td) / "does-not-exist"))
            self.assertEqual(ctx.exception.code, "not-a-repo")

    def test_unknown_ref_is_git_failed(self):
        import tempfile
        from ghostauthor.gitparse import GitError, iter_commits
        with tempfile.TemporaryDirectory() as td:
            repo = self._build(td)
            with self.assertRaises(GitError) as ctx:
                iter_commits(str(repo), ref="no-such-branch")
            self.assertEqual(ctx.exception.code, "git-failed")


if __name__ == "__main__":
    unittest.main()
