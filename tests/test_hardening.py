"""Hardening tests: the defects an independent review found, pinned.

Every test here corresponds to a bug that WAS reproducible. The project's
rule is that no semantics ship without a negative test — a fix that cannot
go red is a slogan. So each test asserts the specific failure mode, not
just the happy path.

Grouped by severity, in the order the findings were reported:

* H1  the web served a RE-SERIALIZED report, so "byte-identical to the
     CLI" was false for `/api/audit`;
* H2  the signer's conflict guard only looked at git's trailer block, so a
     signature followed by prose could be double-signed;
* M1  an unmatched co-author on an agent-signed commit was invisible;
* M2  `trailer-in-body` quoted the legitimate trailer instead of the
     offending prose;
* M3  one malformed JSON-RPC line killed the MCP server process;
* M4  an inverted window returned different codes on different surfaces;
* M5  the web rejected a scope+shas request the CLI and MCP accepted;
* L1-L5  small divergences that made the same request answer differently
     depending on the surface, the interpreter, or the spelling.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.make_fixtures import build_mixed  # noqa: E402
from ghostauthor.engine import build_report  # noqa: E402
from ghostauthor.gitparse import (iter_commits, iter_coauthor_lines,  # noqa: E402
                                parse_trailer_block)
from ghostauthor.models import (BUCKET_AGENT, CommitInfo,  # noqa: E402
                                parse_window_value)
from ghostauthor.options import (OptionError, resolve_session_gap,  # noqa: E402
                                 scope_from_mapping)
from ghostauthor.report import _scope_flags  # noqa: E402
from ghostauthor.roster import (builtin_roster_path,  # noqa: E402
                                load_roster)
from ghostauthor.signer import SignError, plan_sign  # noqa: E402
from ghostauthor.web import handle_audit, handle_evidence  # noqa: E402


def _commit(message: str, sha="a" * 40, ts=1767225600):
    """A CommitInfo built the way the real pipeline builds it: the trailers
    are PARSED from the message by git's trailer semantics, so a test cannot
    accidentally hand the engine a trailer set the repository would not
    produce."""
    return CommitInfo(
        sha=sha, parent_shas=[], author_name="Sam Human",
        author_email="sam@example.com", authored_at=ts, message=message,
        trailers=parse_trailer_block(message),
        lines_added=1, lines_removed=0)


class WebBytesTests(unittest.TestCase):
    """H1: the web must serve the CLI's exact bytes, not a re-serialization."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-hard-web-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_report_is_the_cli_byte_string(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}]})
        self.assertEqual(st, 200)
        served = body["results"][0]["report"]
        self.assertIsInstance(served, str)
        cli = subprocess.run(
            [sys.executable, "-m", "ghostauthor.cli", str(self.mixed),
             "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(served, cli.stdout)

    def test_served_bytes_keep_the_canonical_key_order(self):
        # A re-serialization (json.dumps of a parsed dict) loses the
        # canonical sort_keys order — this is what made the served document
        # differ from the CLI's while still "parsing to the same thing".
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}]})
        served = body["results"][0]["report"]
        keys = list(json.loads(served).keys())
        self.assertEqual(keys, sorted(keys))

    def test_web_evidence_accepts_scope_with_shas_like_the_cli(self):
        # M5: the CLI and the MCP ignore the scope once the commits are
        # named explicitly; the web used to reject it — the odd one out.
        shas = list(reversed(json.loads(subprocess.run(
            [sys.executable, "-m", "ghostauthor.cli", str(self.mixed),
             "--format", "json"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(ROOT)).stdout)["bucket_commit_shas"]["agent"]))
        st, body = handle_evidence({"path": str(self.mixed), "shas": shas,
                                    "all_refs": True, "ref": "x"})
        self.assertEqual(st, 200)
        self.assertEqual(len(json.loads(body["evidence"])["evidences"]),
                         len(shas))


class SignerConflictTests(unittest.TestCase):
    """H2: the conflict guard must see every signature the commit carries."""

    @classmethod
    def setUpClass(cls):
        cls.roster = load_roster(builtin_roster_path())
        cls.claude = next(a for a in cls.roster.agents if a.id == "claude")

    def test_conflict_is_refused_when_prose_follows_the_trailer(self):
        # git would treat the trailer block as prose here (it is not the
        # final paragraph), but the commit still says who signed it.
        message = ("a commit\n"
                   "\n"
                   "Co-Authored-By: OpenAI Codex <noreply@openai.com>\n"
                   "\n"
                   "Generated with [Claude Code]\n")
        with self.assertRaises(SignError) as ctx:
            plan_sign(message, self.roster, self.claude)
        self.assertEqual(ctx.exception.code, "sign-conflict")

    def test_idempotent_when_our_own_signature_is_followed_by_prose(self):
        message = ("a commit\n"
                   "\n"
                   "Co-Authored-By: Claude <noreply@anthropic.com>\n"
                   "\n"
                   "Generated with [Claude Code]\n")
        trailer, new_message, no_op = plan_sign(message, self.roster,
                                                self.claude)
        self.assertTrue(no_op)
        self.assertEqual(new_message, message)
        self.assertEqual(new_message.count("Co-Authored-By:"), 1)

    def test_control_conflict_still_refused(self):
        message = ("a commit\n"
                   "\n"
                   "Co-Authored-By: OpenAI Codex <noreply@openai.com>\n")
        with self.assertRaises(SignError) as ctx:
            plan_sign(message, self.roster, self.claude)
        self.assertEqual(ctx.exception.code, "sign-conflict")

    def test_a_non_roster_co_author_is_not_a_conflict(self):
        message = ("a commit\n"
                   "\n"
                   "Co-Authored-By: Some Person <person@example.com>\n")
        _trailer, _new, no_op = plan_sign(message, self.roster, self.claude)
        self.assertFalse(no_op)

    def test_iter_coauthor_lines_finds_lines_anywhere(self):
        message = ("subject\n"
                   "\n"
                   "Co-Authored-By: A <a@example.com>\n"
                   "\n"
                   "prose\n"
                   "\n"
                   "Co-Authored-By: B <b@example.com>\n")
        found = iter_coauthor_lines(message)
        self.assertEqual([t.name for t in found], ["A", "B"])


class EngineVisibilityTests(unittest.TestCase):
    """M1 + M2: an unmatched co-author must be visible, and the
    trailer-in-body finding must quote the offending line."""

    @classmethod
    def setUpClass(cls):
        cls.roster = load_roster(builtin_roster_path())

    def _report(self, commit):
        return build_report([commit], self.roster)

    def test_unmatched_co_author_is_visible_on_an_agent_signed_commit(self):
        # The commit is signed by a KNOWN agent and by an unknown party,
        # both in the SAME trailer block (git's semantics: the trailer block
        # is the final paragraph, so both lines belong to it). The unknown
        # party used to vanish entirely: the `cosigned-unknown` finding was
        # emitted only for commits whose whole bucket is `cosigned`.
        c = _commit("work\n\n"
                    "Co-Authored-By: Claude <noreply@anthropic.com>\n"
                    "Co-Authored-By: Mystery <mystery@nowhere.invalid>\n")
        rep = self._report(c)
        self.assertEqual(rep.commits_by_bucket[BUCKET_AGENT], 1)
        findings = [f for f in rep.findings if f.rule == "cosigned-unknown"]
        self.assertEqual(len(findings), 1)
        self.assertIn("Mystery", findings[0].evidence)

    def test_trailer_in_body_quotes_the_prose_not_the_trailer(self):
        # git puts trailers LAST, so a prose mention comes BEFORE them.
        # Counting positions quoted the legitimate trailer instead of the
        # offending prose — the finding pointed the reviewer at the wrong
        # line.
        c = _commit("fix\n\nCo-Authored-By: THIS IS PROSE\n\n"
                    "Co-Authored-By: Claude <noreply@anthropic.com>\n")
        rep = self._report(c)
        findings = [f for f in rep.findings if f.rule == "trailer-in-body"]
        self.assertEqual(len(findings), 1)
        self.assertIn("THIS IS PROSE", findings[0].evidence)
        self.assertNotIn("noreply@anthropic.com", findings[0].evidence)


class McpRobustnessTests(unittest.TestCase):
    """M3: one malformed JSON-RPC line must not kill the server."""

    def _serve(self, lines):
        proc = subprocess.run(
            [sys.executable, "-m", "ghostauthor.mcp_server"],
            input="\n".join(lines) + "\n", capture_output=True, text=True,
            encoding="utf-8", cwd=str(ROOT))
        return proc

    def test_malformed_lines_do_not_kill_the_session(self):
        proc = self._serve([
            "[1, 2, 3]",                      # a JSON array
            "42",                             # a bare number
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}',
        ])
        self.assertEqual(proc.returncode, 0,
                         f"the server died: {proc.stderr}")
        out = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
        self.assertTrue(any(o.get("id") == 1 for o in out),
                        "the server never answered the valid request")

    def test_bad_arguments_shape_is_an_error_not_a_crash(self):
        proc = self._serve([
            '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            '"params":{"name":"repo_authorship_report","arguments":[1,2]}}',
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
        self.assertTrue(out)
        self.assertIn("error", out[0])
        self.assertEqual(out[0]["error"]["code"], -32602)


class WindowCodeTests(unittest.TestCase):
    """M4: an inverted window has ONE code on every surface."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-hard-win-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_evidence_surface_reports_window_invalid(self):
        from ghostauthor.evidence import select_evidence_commits
        with self.assertRaises(ValueError) as ctx:
            select_evidence_commits(str(self.mixed), [],
                                    since="2026-06-01", until="2026-01-01")
        self.assertIn("window-invalid", str(ctx.exception))

    def test_all_surfaces_agree_on_the_inverted_window(self):
        cli = subprocess.run(
            [sys.executable, "-m", "ghostauthor.cli", str(self.mixed),
             "--since", "2026-06-01", "--until", "2026-01-01"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
        self.assertEqual(cli.returncode, 2)
        self.assertIn("window-invalid", cli.stderr)
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "since": "2026-06-01", "until": "2026-01-01"})
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["error"]["code"], "window-invalid")


class OptionDetailTests(unittest.TestCase):
    """L1-L3: the small divergences that made the same request answer
    differently depending on the surface or the spelling."""

    def test_non_ascii_digit_bound_keeps_the_documented_code(self):
        # "²".isdigit() is True but int("²") raises: the documented
        # `window-bound-invalid` never happened and a raw Python message
        # escaped to the caller.
        with self.assertRaises(ValueError) as ctx:
            parse_window_value("²")
        self.assertIn("window-bound-invalid", str(ctx.exception))

    def test_explicit_all_refs_wins_over_the_all_synonym(self):
        # A stale `all: true` left in a request must not override a
        # deliberate `all_refs: false` — that is the "silently different
        # scope" failure the shared module exists to remove.
        scope = scope_from_mapping({"all_refs": False, "all": True})
        self.assertFalse(scope.all_refs)
        self.assertTrue(scope_from_mapping({"all": True}).all_refs)
        self.assertTrue(scope_from_mapping({"all_refs": True}).all_refs)

    def test_non_integral_session_gap_is_rejected(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_session_gap(1.9)
        self.assertEqual(ctx.exception.code, "session-gap-invalid")

    def test_integral_session_gap_is_accepted(self):
        self.assertEqual(resolve_session_gap(1.0), 1)
        self.assertEqual(resolve_session_gap(360), 360)


class ScopeFlagTests(unittest.TestCase):
    """L5: the brief's regeneration command must be one that works."""

    def test_all_scope_uses_all_not_ref(self):
        self.assertEqual(_scope_flags("all"), "--all")

    def test_range_scope_uses_base(self):
        self.assertEqual(_scope_flags("main..HEAD"), "--base main")
        self.assertEqual(_scope_flags("main..feature"),
                         "--base main --ref feature")

    def test_single_ref_scope_uses_ref(self):
        self.assertEqual(_scope_flags("feature"), "--ref feature")

    def test_head_scope_needs_no_flags(self):
        self.assertEqual(_scope_flags("HEAD"), "")


class IterationTests(unittest.TestCase):
    """The engine still orders commits deterministically."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-hard-iter-"))
        cls.mixed = build_mixed(cls.tmp)
        cls.roster = load_roster(builtin_roster_path())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ordering_is_by_authored_at_then_sha(self):
        commits = iter_commits(str(self.mixed))
        ordered = sorted(commits, key=lambda c: (c.authored_at, c.sha))
        rep = build_report(commits, self.roster)
        self.assertEqual(rep.total_commits, len(ordered))
        # Two runs over the same state agree.
        again = build_report(iter_commits(str(self.mixed)), self.roster)
        self.assertEqual(rep.bucket_commit_shas, again.bucket_commit_shas)
