"""The shared option semantics — and the surface parity they make possible.

`ghostauthor.options` is the single definition of scope, window, roster,
session gap and gate for the CLI, the MCP server and the web transport.
This file pins:

1. the semantics themselves (the boundaries), and
2. the property they exist for: **the three surfaces cannot drift apart**
   — same request in, same bytes and same error code out.

Every negative case is asserted by name. A rule that cannot go red is a
slogan (the project's own words, and the reason the schema freeze is
enforced rather than documented).
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
from ghostauthor.mcp_server import _audit_report  # noqa: E402
from ghostauthor.options import (OptionError, Scope, resolve_gate,  # noqa: E402
                                 resolve_roster, resolve_session_gap,
                                 resolve_window, scope_from_mapping,
                                 split_code)
from ghostauthor.web import (handle_audit, handle_coverage,  # noqa: E402
                            handle_evidence)


def _cli(*args):
    p = subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    return p.returncode, p.stdout, p.stderr


class ScopeTests(unittest.TestCase):
    """The label is the one spelling of "what was scanned" every surface
    now reports. It used to be written out four times."""

    def test_label_head(self):
        self.assertEqual(Scope().label, "HEAD")

    def test_label_ref(self):
        self.assertEqual(Scope(ref="main").label, "main")

    def test_label_all(self):
        self.assertEqual(Scope(all_refs=True).label, "all")

    def test_label_range(self):
        self.assertEqual(Scope(base="main").label, "main..HEAD")
        self.assertEqual(Scope(base="main", ref="feature").label,
                         "main..feature")

    def test_conflict_all_and_ref(self):
        with self.assertRaises(OptionError) as ctx:
            Scope(all_refs=True, ref="main").validate()
        self.assertEqual(ctx.exception.code, "ref-conflict")

    def test_conflict_all_and_base(self):
        with self.assertRaises(OptionError) as ctx:
            Scope(all_refs=True, base="main").validate()
        self.assertEqual(ctx.exception.code, "ref-conflict")

    def test_ref_invalid_leading_dash(self):
        for scope in (Scope(ref="-evil"), Scope(base="-evil")):
            with self.assertRaises(OptionError) as ctx:
                scope.validate()
            self.assertEqual(ctx.exception.code, "ref-invalid")

    def test_valid_scope_passes(self):
        self.assertIsNotNone(Scope(all_refs=True).validate())
        self.assertIsNotNone(Scope(ref="main", base="dev").validate())

    def test_all_and_all_refs_are_synonyms(self):
        # The web audit endpoint used to spell the flag `all_refs` and its
        # sibling endpoints `all`; a client that guessed wrong silently
        # got HEAD. Both spellings must now mean the same thing.
        self.assertTrue(scope_from_mapping({"all": True}).all_refs)
        self.assertTrue(scope_from_mapping({"all_refs": True}).all_refs)
        self.assertFalse(scope_from_mapping({}).all_refs)


class WindowTests(unittest.TestCase):
    def test_absent_bounds(self):
        self.assertEqual(resolve_window(None, None), (None, None))
        self.assertEqual(resolve_window("", ""), (None, None))

    def test_epoch_and_date_agree(self):
        self.assertEqual(resolve_window("2026-06-01", None)[0],
                         resolve_window("1780272000", None)[0])

    def test_iso_8601(self):
        self.assertEqual(resolve_window("2026-06-01T00:00:00Z", None)[0],
                         resolve_window("2026-06-01", None)[0])

    def test_garbage_bound_is_rejected_by_code(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_window("next tuesday", None)
        self.assertEqual(ctx.exception.code, "window-bound-invalid")

    def test_error_message_carries_the_code_once(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_window("next tuesday", None)
        self.assertEqual(str(ctx.exception).count("window-bound-invalid"), 1)


class RosterTests(unittest.TestCase):
    def test_roster_and_dir_conflict(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_roster("a.json", "b/")
        self.assertEqual(ctx.exception.code, "roster-conflict")

    def test_missing_roster_file_is_roster_error(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_roster("definitely-not-here.json", None)
        self.assertEqual(ctx.exception.code, "roster-error")
        # The validator's own rule id is preserved inside the detail — the
        # operator must see WHICH rule failed, not just that something did.
        self.assertIn("roster-file-missing", ctx.exception.detail)

    def test_builtin_roster_loads(self):
        roster = resolve_roster(None, None)
        self.assertGreater(len(roster.agents), 0)


class GapAndGateTests(unittest.TestCase):
    def test_gap_must_be_an_integer(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_session_gap("soon")
        self.assertEqual(ctx.exception.code, "session-gap-invalid")

    def test_gap_must_be_positive(self):
        for bad in (0, -1):
            with self.assertRaises(OptionError) as ctx:
                resolve_session_gap(bad)
            self.assertEqual(ctx.exception.code, "session-gap-invalid")

    def test_gap_accepts_a_positive_integer(self):
        self.assertEqual(resolve_session_gap(360), 360)
        self.assertEqual(resolve_session_gap("360"), 360)

    def test_gate_off(self):
        self.assertIsNone(resolve_gate(None))
        self.assertIsNone(resolve_gate(""))

    def test_gate_out_of_range(self):
        for bad in (-0.1, 1.1, 2):
            with self.assertRaises(OptionError) as ctx:
                resolve_gate(bad)
            self.assertEqual(ctx.exception.code, "gate-limit-invalid")

    def test_gate_not_a_number(self):
        with self.assertRaises(OptionError) as ctx:
            resolve_gate("lots")
        self.assertEqual(ctx.exception.code, "gate-limit-invalid")

    def test_gate_accepts_the_boundaries(self):
        self.assertEqual(resolve_gate(0), 0.0)
        self.assertEqual(resolve_gate(1), 1.0)


class SplitCodeTests(unittest.TestCase):
    def test_splits_a_coded_message(self):
        self.assertEqual(split_code("window-empty: nothing here"),
                         ("window-empty", "nothing here"))

    def test_falls_back_when_there_is_no_code(self):
        self.assertEqual(split_code("something went wrong"),
                         ("value-error", "something went wrong"))


class SurfaceParityTests(unittest.TestCase):
    """The property the shared module exists for: the three surfaces answer
    the same request identically — same bytes, same error code."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-options-test-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_web_audit_supports_base_and_matches_the_cli(self):
        # Before this, /api/audit silently IGNORED `base`: asking for a
        # two-branch diff returned the whole history — a wrong answer that
        # looks right, which is the worst failure mode for an audit tool.
        rc, cli_out, _ = _cli(str(self.mixed), "--base", "HEAD~1",
                              "--format", "json")
        self.assertEqual(rc, 0)
        st, body = handle_audit({"entries": [
            {"path": str(self.mixed), "base": "HEAD~1"}]})
        self.assertEqual(st, 200)
        entry = body["results"][0]
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["ref"], "HEAD~1..HEAD")
        # RAW bytes: the web serves the CLI's exact document, so a
        # re-serialized report cannot pass this comparison.
        self.assertEqual(entry["report"], cli_out)

    def test_web_coverage_supports_base_and_matches_the_cli(self):
        rc, cli_out, _ = _cli("coverage", str(self.mixed), "--base", "HEAD~1",
                              "--format", "json")
        self.assertEqual(rc, 0)
        st, body = handle_coverage({"path": str(self.mixed),
                                    "base": "HEAD~1"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body["coverage"]), json.loads(cli_out))

    def test_web_audit_accepts_the_all_synonym(self):
        st, body = handle_audit({"entries": [
            {"path": str(self.mixed), "all": True}]})
        self.assertEqual(st, 200)
        entry = body["results"][0]
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["ref"], "all")

    def test_mcp_accepts_the_all_synonym(self):
        payload, error = _audit_report({"path": str(self.mixed), "all": True})
        self.assertIsNone(error)
        self.assertEqual(json.loads(payload)["ref"], "all")

    def test_all_three_surfaces_report_the_same_error_code(self):
        """The web audit endpoint used to collapse every option error into a
        generic `value-error`, hiding the code the CLI prints. One request,
        one code, three surfaces."""
        rc, _, cli_err = _cli(str(self.mixed), "--since", "2999-01-01")
        self.assertEqual(rc, 2)
        self.assertIn("window-empty", cli_err)

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "since": "2999-01-01"})
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["error"]["code"], "window-empty")

        st, body = handle_coverage({"path": str(self.mixed),
                                    "since": "2999-01-01"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "window-empty")

    def test_all_three_surfaces_report_the_same_roster_error(self):
        bad = str(self.tmp / "no-such-roster.json")
        rc, _, cli_err = _cli(str(self.mixed), "--roster", bad)
        self.assertEqual(rc, 2)
        self.assertIn("roster-error", cli_err)

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster": bad})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "roster-error")

    def test_all_three_surfaces_report_the_same_roster_conflict(self):
        rc, _, cli_err = _cli(str(self.mixed), "--roster", "a.json",
                              "--roster-dir", "b")
        self.assertEqual(rc, 2)
        self.assertIn("roster-conflict", cli_err)

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster": "a.json", "roster_dir": "b"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "roster-conflict")

    def test_all_three_surfaces_report_the_same_ref_conflict(self):
        rc, _, cli_err = _cli(str(self.mixed), "--all", "--ref", "main")
        self.assertEqual(rc, 2)
        self.assertIn("ref-conflict", cli_err)

        st, body = handle_audit({"entries": [
            {"path": str(self.mixed), "all": True, "ref": "main"}]})
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["error"]["code"], "ref-conflict")

        st, body = handle_coverage({"path": str(self.mixed),
                                    "all": True, "ref": "main"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "ref-conflict")

    def test_all_three_surfaces_report_the_same_gate_error(self):
        rc, _, cli_err = _cli(str(self.mixed), "--max-ai-ratio", "2")
        self.assertEqual(rc, 2)
        self.assertIn("gate-limit-invalid", cli_err)

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "max_ai_ratio": 2})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "gate-limit-invalid")

    def test_all_three_surfaces_report_the_same_session_gap_error(self):
        rc, _, cli_err = _cli(str(self.mixed), "--session-gap", "0")
        self.assertEqual(rc, 2)
        self.assertIn("session-gap-invalid", cli_err)

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "session_gap_minutes": 0})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "session-gap-invalid")


class OptionTypeTests(unittest.TestCase):
    """A wrong TYPE is a request error, never a guess.

    Coercion used to hide three real failures:

    * ``"all": "false"`` was truthy, so a client that asked for HEAD got
      every ref — a confident, wrong answer, which is the worst outcome this
      project can produce;
    * ``"ref": 123`` reached ``str.startswith`` and escaped as a 500;
    * ``"roster_dir": []`` is falsy, so it silently used the builtin roster
      instead of the one the caller named.

    These tests pin the fix at the request boundary, on every surface.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-optype-test-"))
        cls.mixed = build_mixed(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_string_boolean_is_rejected_not_coerced(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed),
                                              "all": "false"}]})
        self.assertEqual(st, 200)  # per-entry failure, request is well-formed
        self.assertEqual(body["results"][0]["status"], 400)
        self.assertEqual(body["results"][0]["error"]["code"],
                         "scope-invalid")

        st, body = handle_coverage({"path": str(self.mixed), "all": "false"})
        self.assertEqual((st, body["error"]["code"]), (400, "scope-invalid"))

        payload, err = _audit_report({"path": str(self.mixed),
                                      "all_refs": "false"})
        self.assertIsNone(payload)
        self.assertTrue(err.startswith("scope-invalid:"))

    def test_a_real_boolean_still_works(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed),
                                              "all": False}]})
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["status"], 200)
        self.assertEqual(body["results"][0]["ref"], "HEAD")

    def test_non_string_ref_is_a_400_not_a_500(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed),
                                              "ref": 123}]})
        self.assertEqual(body["results"][0]["status"], 400)
        self.assertEqual(body["results"][0]["error"]["code"], "ref-invalid")

        st, body = handle_evidence({"path": str(self.mixed), "ref": 123})
        self.assertEqual((st, body["error"]["code"]), (400, "ref-invalid"))

    def test_non_path_roster_is_rejected_not_ignored(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster": 123})
        self.assertEqual((st, body["error"]["code"]), (400, "roster-invalid"))

        # [] used to be falsy and silently select the builtin roster.
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "roster_dir": []})
        self.assertEqual((st, body["error"]["code"]), (400, "roster-invalid"))

    def test_booleans_are_not_numbers(self):
        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "session_gap_minutes": True})
        self.assertEqual((st, body["error"]["code"]),
                         (400, "session-gap-invalid"))

        st, body = handle_audit({"entries": [{"path": str(self.mixed)}],
                                 "max_ai_ratio": True})
        self.assertEqual((st, body["error"]["code"]),
                         (400, "gate-limit-invalid"))

    def test_scope_label_is_only_built_after_validation(self):
        # A rejected entry must not carry a rendered ref label: that is a
        # half-answer, and a reader could take it as the scope actually used.
        st, body = handle_audit({"entries": [{"path": str(self.mixed),
                                              "ref": 123}]})
        self.assertIsNone(body["results"][0]["ref"])
