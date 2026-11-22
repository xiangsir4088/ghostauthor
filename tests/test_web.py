"""Web API contract: the positive/negative matrix for handle_audit.

Runs in-process (no sockets) — the transport logic is a pure function, so
the whole contract (golden numbers, per-entry error codes, and byte-for-byte
identity with the CLI's JSON) is pinned here. The real HTTP layer is
exercised end-to-end by the web step in verify.py.
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
from ghostauthor.web import handle_audit, handle_coverage  # noqa: E402

MIXED_BASE_TS = 1767225600  # matches make_fixtures.BASE_TS


class WebAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-web-test-"))
        cls.mixed = build_mixed(cls.tmp)
        cls.heavy = build_agent_heavy(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def audit(self, **params):
        params.setdefault("entries", [{"path": str(self.mixed)}])
        return handle_audit(params)

    # ---- positive: the report the web serves ----

    def test_single_audit_golden_numbers(self):
        st, body = self.audit()
        self.assertEqual(st, 200)
        r = body["results"][0]
        self.assertEqual(r["status"], 200)
        self.assertIsNone(r["error"])
        self.assertEqual(r["ref"], "HEAD")
        self.assertEqual(r["report"]["commits_by_bucket"],
                         {"agent": 6, "cosigned": 1, "unsigned": 5})
        self.assertAlmostEqual(r["report"]["ai_ratio"], 0.5)

    def test_report_byte_stable_with_cli(self):
        # The core invariant: same repo state => same report, on every
        # transport. The web JSON must equal the CLI JSON exactly.
        st, body = self.audit()
        self.assertEqual(st, 200)
        cli = subprocess.run(
            [sys.executable, "-m", "ghostauthor.cli", str(self.mixed),
             "--format", "json"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(ROOT))
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(body["results"][0]["report"], json.loads(cli.stdout))

    def test_window_applied_per_repo(self):
        st, body = self.audit(since=str(MIXED_BASE_TS + 5 * 3600))
        r = body["results"][0]
        self.assertEqual(r["status"], 200)
        # i=5..11: agent(6,7,10) + cosigned(9) + unsigned(5,8,11)
        self.assertEqual(r["report"]["commits_by_bucket"],
                         {"agent": 3, "cosigned": 1, "unsigned": 3})
        self.assertEqual(r["report"]["window"]["since"],
                         MIXED_BASE_TS + 5 * 3600)

    def test_gate_breach_is_a_result_not_an_error(self):
        st, body = handle_audit({"entries": [{"path": str(self.heavy)}],
                                 "max_ai_ratio": 0.5})
        self.assertEqual(st, 200)
        r = body["results"][0]
        self.assertEqual(r["status"], 200)
        self.assertTrue(r["report"]["gate"]["breached"])

    def test_gate_pass(self):
        st, body = handle_audit({"entries": [{"path": str(self.heavy)}],
                                 "max_ai_ratio": 1.0})
        self.assertFalse(body["results"][0]["report"]["gate"]["breached"])

    def test_comparison_survives_one_bad_side(self):
        st, body = handle_audit({"entries": [
            {"path": str(self.mixed)},
            {"path": str(self.tmp / "nope")},
        ]})
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["status"], 200)
        self.assertEqual(body["results"][1]["status"], 404)
        self.assertEqual(body["results"][1]["error"]["code"], "not-a-repo")

    # ---- per-entry errors: reported inside the entry, request stays 200 ----

    def test_not_a_repo_is_404(self):
        st, body = handle_audit(
            {"entries": [{"path": str(self.tmp / "nope")}]}
        )
        self.assertEqual(st, 200)
        r = body["results"][0]
        self.assertEqual(r["status"], 404)
        self.assertEqual(r["error"]["code"], "not-a-repo")

    def test_unborn_head_repo_is_422(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)],
                       check=True, capture_output=True)
        st, body = handle_audit({"entries": [{"path": str(empty)}]})
        r = body["results"][0]
        self.assertEqual(r["status"], 422)
        self.assertEqual(r["error"]["code"], "no-commits")

    def test_ref_conflict_is_per_entry_400(self):
        st, body = handle_audit({"entries": [
            {"path": str(self.mixed), "all_refs": True, "ref": "main"}]})
        r = body["results"][0]
        self.assertEqual(r["status"], 400)
        self.assertEqual(r["error"]["code"], "ref-conflict")

    def test_window_empty_is_per_entry_400(self):
        st, body = self.audit(since="2999-01-01")
        r = body["results"][0]
        self.assertEqual(r["status"], 400)
        self.assertIn("window-empty", r["error"]["detail"])

    def test_window_inverted_is_per_entry_400(self):
        st, body = self.audit(since="2026-06-01", until="2026-01-01")
        r = body["results"][0]
        self.assertEqual(r["status"], 400)
        self.assertIn("window-invalid", r["error"]["detail"])

    # ---- request-level errors: the whole request is 400 ----

    def test_bad_entries(self):
        for bad in ({}, {"entries": []}, {"entries": [1, 2, 3]}):
            st, body = handle_audit(bad)
            self.assertEqual(st, 400)
            self.assertEqual(body["error"]["code"], "bad-entries")

    def test_bad_window_bound(self):
        st, body = self.audit(since="next tuesday")
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "window-bound-invalid")

    def test_roster_conflict(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster": "a.json", "roster_dir": "b"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "roster-conflict")

    def test_missing_roster_file(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster": str(self.tmp / "missing.json")})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "roster-error")

    def test_bad_gate_limit(self):
        st, body = self.audit(max_ai_ratio=2.0)
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "gate-limit-invalid")

    def test_bad_session_gap(self):
        st, body = self.audit(session_gap_minutes=0)
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "session-gap-invalid")


class WebCoverageAPITests(unittest.TestCase):
    """POST /api/coverage: same bytes as the CLI, same error-code mapping.

    Exercised in-process (the handler is a pure function); the socket layer
    is covered by verify's web step.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-webcov-test-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "ghostauthor.cli", "coverage",
             str(self.mixed), *[str(a) for a in args]],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))

    def test_byte_identical_to_the_cli(self):
        st, body = handle_coverage({"path": str(self.mixed)})
        self.assertEqual(st, 200)
        self.assertEqual(body["coverage"],
                         self._cli("--format", "json").stdout)
        payload = json.loads(body["coverage"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["adoption"], "partial")

    def test_window_is_forwarded(self):
        since = MIXED_BASE_TS + 5 * 3600
        st, body = handle_coverage({"path": str(self.mixed),
                                    "since": str(since)})
        self.assertEqual(st, 200)
        self.assertEqual(body["coverage"],
                         self._cli("--format", "json",
                                   "--since", str(since)).stdout)

    def test_error_mapping(self):
        self.assertEqual(handle_coverage({})[0], 400)               # missing-path
        self.assertEqual(handle_coverage({"path": str(self.mixed),
                                          "all": True, "ref": "x"})[0], 400)
        self.assertEqual(handle_coverage({"path": str(self.mixed),
                                          "ref": "-bad"})[0], 400)
        self.assertEqual(handle_coverage({"path": str(self.mixed),
                                          "since": "nope"})[0], 400)
        self.assertEqual(handle_coverage({"path": str(self.mixed),
                                          "roster": "a",
                                          "roster_dir": "b"})[0], 400)
        self.assertEqual(handle_coverage(
            {"path": str(self.tmp / "nope")})[0], 404)
        # a window that excludes everything is a request-level 400, like
        # the CLI's exit 2 — never an empty report
        self.assertEqual(handle_coverage({"path": str(self.mixed),
                                          "since": MIXED_BASE_TS + 10**7})[0],
                         400)


if __name__ == "__main__":
    unittest.main()
