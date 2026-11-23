"""MCP handler contract: the positive/negative matrix for _audit_report,
_evidence_report and _coverage_report.

Runs in-process (no stdio) — the handlers are pure functions, so the whole
contract (golden numbers, byte-for-byte identity with the CLI, and every
request-level / repo-level error code) is pinned here. The real stdio
protocol (initialize / tools/list / tools/call) is exercised end-to-end by
verify.py step 23.
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

from examples.make_fixtures import build_agent_heavy, build_mixed  # noqa: E402
from ghostauthor.mcp_server import (_audit_report, _coverage_report,  # noqa: E402
                                    _evidence_report)

MIXED_BASE_TS = 1767225600  # matches make_fixtures.BASE_TS


def _cli(*args):
    p = subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    assert p.returncode == 0, p.stderr
    return p.stdout


class AuditReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-mcp-test-"))
        cls.mixed = build_mixed(cls.tmp)
        cls.heavy = build_agent_heavy(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- positive: the report the MCP serves ----

    def test_golden_numbers(self):
        report, error = _audit_report({"path": str(self.mixed)})
        self.assertIsNone(error)
        payload = json.loads(report)
        self.assertEqual(payload["commits_by_bucket"],
                         {"agent": 6, "cosigned": 1, "unsigned": 5})
        self.assertAlmostEqual(payload["ai_ratio"], 0.5)
        self.assertEqual(payload["ref"], "HEAD")

    def test_byte_stable_with_cli(self):
        # The core invariant: same repo state => identical report on every
        # surface. The MCP JSON must equal the CLI JSON byte for byte.
        report, error = _audit_report({"path": str(self.mixed)})
        self.assertIsNone(error)
        self.assertEqual(report, _cli(str(self.mixed), "--format", "json"))

    def test_window_and_gate_parity(self):
        report, error = _audit_report(
            {"path": str(self.mixed), "since": str(MIXED_BASE_TS + 5 * 3600),
             "max_ai_ratio": 1.0})
        self.assertIsNone(error)
        self.assertEqual(
            report, _cli(str(self.mixed), "--format", "json",
                         "--since", str(MIXED_BASE_TS + 5 * 3600),
                         "--max-ai-ratio", "1.0"))
        self.assertEqual(json.loads(report)["window"]["since"],
                         MIXED_BASE_TS + 5 * 3600)

    def test_ref_scope_parity(self):
        # A ref scoping that the builtin single-branch fixture can answer is
        # the same as HEAD here, but the ref LABEL in the report must match.
        report, error = _audit_report({"path": str(self.mixed), "ref": "HEAD"})
        self.assertIsNone(error)
        self.assertEqual(report, _cli(str(self.mixed), "--format", "json",
                                      "--ref", "HEAD"))
        self.assertEqual(json.loads(report)["ref"], "HEAD")

    def test_gate_breach_is_a_result_not_an_error(self):
        report, error = _audit_report({"path": str(self.heavy),
                                       "max_ai_ratio": 0.5})
        self.assertIsNone(error)
        self.assertTrue(json.loads(report)["gate"]["breached"])

    # ---- repo-level errors: carried in the error string ----

    def test_not_a_repo(self):
        report, error = _audit_report({"path": str(self.tmp / "nope")})
        self.assertIsNone(report)
        self.assertTrue(error.startswith("not-a-repo:"))

    def test_no_commits(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)],
                       check=True, capture_output=True)
        report, error = _audit_report({"path": str(empty)})
        self.assertIsNone(report)
        self.assertTrue(error.startswith("no-commits"))

    # ---- request-level errors ----

    def test_roster_conflict(self):
        _, error = _audit_report({"path": str(self.mixed), "roster": "a",
                                  "roster_dir": "b"})
        self.assertTrue(error.startswith("roster-conflict:"))

    def test_roster_error_missing_file(self):
        _, error = _audit_report(
            {"path": str(self.mixed), "roster": str(self.tmp / "missing.json")})
        self.assertTrue(error.startswith("roster-error:"))

    def test_ref_conflict_all_ref(self):
        _, error = _audit_report({"path": str(self.mixed), "all_refs": True,
                                  "ref": "main"})
        self.assertTrue(error.startswith("ref-conflict:"))

    def test_ref_conflict_all_base(self):
        _, error = _audit_report({"path": str(self.mixed), "all_refs": True,
                                  "base": "main"})
        self.assertTrue(error.startswith("ref-conflict:"))

    def test_ref_invalid_dash_leading(self):
        _, error = _audit_report({"path": str(self.mixed), "ref": "-x"})
        self.assertTrue(error.startswith("ref-invalid:"))

    def test_window_bound_invalid(self):
        _, error = _audit_report({"path": str(self.mixed),
                                  "since": "next tuesday"})
        self.assertTrue(error.startswith("window-bound-invalid:"))

    def test_window_empty(self):
        _, error = _audit_report({"path": str(self.mixed),
                                  "since": "2999-01-01"})
        self.assertIn("window-empty", error)

    def test_window_inverted(self):
        _, error = _audit_report({"path": str(self.mixed),
                                  "since": "2026-06-01", "until": "2026-01-01"})
        self.assertIn("window-invalid", error)

    def test_session_gap_invalid(self):
        _, error = _audit_report({"path": str(self.mixed),
                                  "session_gap_minutes": 0})
        self.assertTrue(error.startswith("session-gap-invalid:"))

    def test_gate_limit_invalid(self):
        _, error = _audit_report({"path": str(self.mixed),
                                  "max_ai_ratio": 2.0})
        self.assertTrue(error.startswith("gate-limit-invalid:"))


class CommitEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-mcp-ev-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- positive: byte identity with the CLI evidence command ----

    def test_scope_parity(self):
        payload, error = _evidence_report({"path": str(self.mixed)})
        self.assertIsNone(error)
        self.assertEqual(payload,
                         _cli("evidence", str(self.mixed), "--format", "json"))

    def test_shas_order_preserved_parity(self):
        shas = json.loads(
            _cli(str(self.mixed), "--format", "json"))["bucket_commit_shas"][
                "agent"]
        rev = list(reversed(shas))
        payload, error = _evidence_report({"path": str(self.mixed),
                                           "shas": rev})
        self.assertIsNone(error)
        self.assertEqual(payload,
                         _cli("evidence", str(self.mixed), *rev,
                              "--format", "json"))
        # The evidences come back in the exact input order.
        got = [e["sha"] for e in json.loads(payload)["evidences"]]
        self.assertEqual(got, rev)

    def test_window_parity(self):
        payload, error = _evidence_report(
            {"path": str(self.mixed), "since": str(MIXED_BASE_TS + 5 * 3600)})
        self.assertIsNone(error)
        self.assertEqual(
            payload, _cli("evidence", str(self.mixed), "--format", "json",
                          "--since", str(MIXED_BASE_TS + 5 * 3600)))

    def test_no_bundle_parameter(self):
        # The bundle writes files; the server never writes files. A bundle
        # argument is simply not part of the tool — evidence still works.
        payload, error = _evidence_report({"path": str(self.mixed),
                                           "bundle": "should-be-ignored"})
        self.assertIsNone(error)
        self.assertEqual(payload,
                         _cli("evidence", str(self.mixed), "--format", "json"))

    # ---- repo-level errors ----

    def test_sha_unknown(self):
        payload, error = _evidence_report(
            {"path": str(self.mixed), "shas": ["deadbeef"]})
        self.assertIsNone(payload)
        self.assertTrue(error.startswith("sha-unknown:"))

    def test_not_a_repo(self):
        payload, error = _evidence_report({"path": str(self.tmp / "nope")})
        self.assertIsNone(payload)
        self.assertTrue(error.startswith("not-a-repo:"))

    # ---- request-level errors ----

    def test_bad_shas_type(self):
        payload, error = _evidence_report(
            {"path": str(self.mixed), "shas": "not-a-list"})
        self.assertIsNone(payload)
        self.assertTrue(error.startswith("bad-shas:"))

    def test_roster_conflict(self):
        _, error = _evidence_report({"path": str(self.mixed), "roster": "a",
                                     "roster_dir": "b"})
        self.assertTrue(error.startswith("roster-conflict:"))

    def test_roster_error_missing_file(self):
        _, error = _evidence_report(
            {"path": str(self.mixed), "roster": str(self.tmp / "missing.json")})
        self.assertTrue(error.startswith("roster-error:"))

    def test_ref_conflict_when_no_shas(self):
        _, error = _evidence_report({"path": str(self.mixed), "all_refs": True,
                                     "ref": "main"})
        self.assertTrue(error.startswith("ref-conflict:"))

    def test_scope_ignored_when_shas_given(self):
        # Naming the commits makes the scope moot — a bad scope must NOT be
        # reported (mirrors the CLI, where scope is ignored with explicit shas).
        shas = json.loads(
            _cli(str(self.mixed), "--format", "json"))["bucket_commit_shas"][
                "agent"]
        payload, error = _evidence_report(
            {"path": str(self.mixed), "shas": [shas[0]], "all_refs": True,
             "ref": "main"})
        self.assertIsNone(error)
        self.assertEqual(len(json.loads(payload)["evidences"]), 1)

    def test_window_bound_invalid(self):
        _, error = _evidence_report({"path": str(self.mixed),
                                     "since": "next tuesday"})
        self.assertTrue(error.startswith("window-bound-invalid:"))

    def test_window_empty(self):
        _, error = _evidence_report({"path": str(self.mixed),
                                     "since": "2999-01-01"})
        self.assertIn("window-empty", error)


if __name__ == "__main__":
    unittest.main()


class AdoptionDiagnosisTests(unittest.TestCase):
    """The third tool: `coverage` must be reachable by an agent.

    An agent that sees a 0% ratio and cannot ask "why" has only two options:
    conclude the tool is broken, or guess. The CLI could already answer; this
    pins that the MCP surface answers with the SAME bytes, so a skill can
    diagnose the cold-start case without shelling out.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-mcp-cov-test-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_default_parity_with_the_cli(self):
        payload, err = _coverage_report({"path": str(self.mixed)})
        self.assertIsNone(err)
        self.assertEqual(payload, _cli("coverage", self.mixed,
                                       "--format", "json"))

    def test_window_parity_with_the_cli(self):
        since = MIXED_BASE_TS + 5 * 3600
        payload, err = _coverage_report({"path": str(self.mixed),
                                         "since": str(since)})
        self.assertIsNone(err)
        self.assertEqual(payload, _cli("coverage", self.mixed, "--format",
                                       "json", "--since", str(since)))

    def test_ref_parity_with_the_cli(self):
        payload, err = _coverage_report({"path": str(self.mixed),
                                         "ref": "HEAD"})
        self.assertIsNone(err)
        self.assertEqual(payload, _cli("coverage", self.mixed, "--format",
                                       "json", "--ref", "HEAD"))

    def test_diagnosis_never_reattributes(self):
        # The signed count must equal the report's agent bucket: a marker is
        # a hint, and a hint may never fill a bucket.
        payload, _ = _coverage_report({"path": str(self.mixed)})
        cov = json.loads(payload)
        report = json.loads(_audit_report({"path": str(self.mixed)})[0])
        self.assertEqual(cov["signed_commits"],
                         report["commits_by_bucket"]["agent"])

    def test_error_codes(self):
        self.assertTrue(_coverage_report(
            {"path": str(self.tmp / "nope")})[1].startswith("not-a-repo:"))
        self.assertTrue(_coverage_report(
            {"path": str(self.mixed), "all_refs": True,
             "ref": "main"})[1].startswith("ref-conflict:"))
        self.assertTrue(_coverage_report(
            {"path": str(self.mixed), "all_refs": "false"})[1]
            .startswith("scope-invalid:"))
        self.assertTrue(_coverage_report(
            {"path": str(self.mixed), "since": "next tuesday"})[1]
            .startswith("window-bound-invalid:"))
        self.assertTrue(_coverage_report(
            {"path": str(self.mixed), "roster": "a", "roster_dir": "b"})[1]
            .startswith("roster-conflict:"))
