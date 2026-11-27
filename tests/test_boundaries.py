"""Boundary tests: the M/L findings of the deep-learning review, pinned.

The H1-H4 round (v1.6.2) fixed the four high-severity defects. This file
pins the twelve medium/low findings of the same review — the ones that were
not crashes but **drift**: two copies of one boundary that could answer
differently, one quantity written with two roundings, a rule catalogue
that lived in two files, a command in a deliverable that could not run,
and documentation that had drifted away from the code it describes.

Grouped by finding:

* **M1** the window FILTER was written out three times (engine / coverage /
  evidence) with the same predicate and the same two error codes;
* **M2** the gate VERDICT was copied into the portfolio, and the copy
  compared a ROUNDED ratio while the report compared the exact one — the
  same boundary, two verdicts;
* **M3** one quantity, two roundings: `ai_ratio` and `gate.ratio` were the
  same number written differently in the same document;
* **M4** the `no-signatures` action printed a placeholder command that
  cannot run even when the roster had a signable agent;
* **M5** the supported-Python floor and the docs had drifted apart;
* **M6** verify.py's step numbers skipped 14, so a document that cited a
  step pointed at the wrong check;
* **L1** the HTML brief's "regenerate" command was produced by string
  surgery on the scope label, and an unknown label became a plausible
  command that cannot run;
* **L2** the conflict error named a flag the CLI does not have;
* **L3** the roster's precise rule id had no contract about where it sits;
* **L4** the finding-rule catalogue lived in two files, so a rule added to
  one and not the other rendered a SARIF result with an empty description;
* **L5** "byte-identical" was stated without its version premise;
* **L6** the skill pack was described as part of the wheel.

Every test here asserts the SPECIFIC failure mode, not the happy path:
a fix that cannot go red is a slogan.
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ghostauthor import cli as cli_mod  # noqa: E402
from ghostauthor import coverage as coverage_mod  # noqa: E402
from ghostauthor import engine as engine_mod  # noqa: E402
from ghostauthor import evidence as evidence_mod  # noqa: E402
from ghostauthor import models as models_mod  # noqa: E402
from ghostauthor import options as options_mod  # noqa: E402
from ghostauthor import portfolio as portfolio_mod  # noqa: E402
from ghostauthor import report as report_mod  # noqa: E402
from ghostauthor.coverage import build_coverage  # noqa: E402
from ghostauthor.engine import (build_gate, build_report,  # noqa: E402
                                compute_ratio)
from ghostauthor.gitparse import parse_trailer_block  # noqa: E402
from ghostauthor.models import CommitInfo, Finding, RULE_INFO  # noqa: E402
from ghostauthor.options import (OptionError, Scope, apply_window,  # noqa: E402
                                 resolve_roster)
from ghostauthor.report import _scope_flags, render_markdown, render_sarif  # noqa: E402
from ghostauthor.roster import (AgentRule, Roster, builtin_roster_path,  # noqa: E402
                                load_roster)


def _commit(message: str, sha="a" * 40, ts=1767225600):
    """A CommitInfo built the way the real pipeline builds it: the trailers
    are PARSED from the message by git's trailer semantics, so a test cannot
    hand the engine a trailer set the repository would not produce."""
    return CommitInfo(
        sha=sha, parent_shas=[], author_name="Sam Human",
        author_email="sam@example.com", authored_at=ts, message=message,
        trailers=parse_trailer_block(message),
        lines_added=1, lines_removed=0)


def _roster():
    return load_roster(builtin_roster_path())


class WindowFilterTests(unittest.TestCase):
    """M1: ONE window filter, ONE pair of error codes."""

    def test_the_three_surfaces_call_the_same_function(self):
        # The filter is one function. A module that re-implements it (even
        # correctly) is free to drift from the other two tomorrow.
        self.assertIs(engine_mod.apply_window, apply_window)
        self.assertIs(coverage_mod.apply_window, apply_window)
        self.assertIs(evidence_mod.apply_window, apply_window)

    def test_inverted_bounds_are_invalid_not_empty(self):
        # An invalid window and an empty one are different diagnoses. The
        # bounds are validated BEFORE the filter, so an inverted window is
        # `window-invalid` on every surface — never `window-empty`.
        with self.assertRaises(ValueError) as ctx:
            apply_window([_commit("x")], since=2000, until=1000)
        self.assertIn("window-invalid", str(ctx.exception))
        self.assertNotIn("window-empty", str(ctx.exception))

    def test_empty_input_is_no_commits(self):
        with self.assertRaises(ValueError) as ctx:
            apply_window([], since=None, until=None)
        self.assertIn("no-commits", str(ctx.exception))

    def test_a_window_that_selects_nothing_is_window_empty(self):
        with self.assertRaises(ValueError) as ctx:
            apply_window([_commit("x", ts=1000)], since=2000, until=3000)
        self.assertIn("window-empty", str(ctx.exception))
        self.assertNotIn("window-invalid", str(ctx.exception))

    def test_since_is_inclusive_and_until_is_exclusive(self):
        c = _commit("x", ts=1000)
        # since INCLUSIVE: a commit exactly at `since` is in the window.
        self.assertEqual(len(apply_window([c], since=1000, until=1001)), 1)
        # until EXCLUSIVE: a commit exactly at `until` is not.
        with self.assertRaises(ValueError) as ctx:
            apply_window([c], since=999, until=1000)
        self.assertIn("window-empty", str(ctx.exception))
        # and a commit before `since` is not in the window either.
        with self.assertRaises(ValueError) as ctx:
            apply_window([c], since=1001, until=1002)
        self.assertIn("window-empty", str(ctx.exception))
        # since == until is an INVALID window, not an empty one.
        with self.assertRaises(ValueError) as ctx:
            apply_window([c], since=1000, until=1000)
        self.assertIn("window-invalid", str(ctx.exception))

    def test_the_audit_and_the_coverage_agree_on_the_code(self):
        roster = _roster()
        c = _commit("x", ts=1000)
        codes = []
        for call in (lambda: build_report([c], roster, since=2000, until=3000),
                     lambda: build_coverage([c], roster, since=2000, until=3000)):
            with self.assertRaises(ValueError) as ctx:
                call()
            codes.append(str(ctx.exception).split(":")[0])
        self.assertEqual(codes, ["window-empty", "window-empty"])

    def test_the_audit_and_the_coverage_agree_on_an_inverted_window(self):
        roster = _roster()
        c = _commit("x", ts=1000)
        codes = []
        for call in (lambda: build_report([c], roster, since=3000, until=2000),
                     lambda: build_coverage([c], roster, since=3000, until=2000)):
            with self.assertRaises(ValueError) as ctx:
                call()
            codes.append(str(ctx.exception).split(":")[0])
        self.assertEqual(codes, ["window-invalid", "window-invalid"])


class GateVerdictTests(unittest.TestCase):
    """M2: ONE gate verdict, computed on the SAME number everywhere."""

    def test_the_portfolio_delegates_to_the_engine(self):
        self.assertIs(portfolio_mod.build_gate, build_gate)

    def test_exactly_at_the_limit_is_a_pass(self):
        gate = build_gate(0.5, 0.5)
        self.assertFalse(gate["breached"])

    def test_just_above_the_limit_breaches(self):
        gate = build_gate(0.5 + 1e-12, 0.5)
        self.assertTrue(gate["breached"])

    def test_the_two_surfaces_agree_on_the_rounded_boundary(self):
        # 1/3 against a limit of 0.333333: the report compares the EXACT
        # ratio, the portfolio compared a ROUNDED one — so the same
        # boundary could breach in one surface and not in the other.
        ratio = 1 / 3
        limit = 0.333333
        report_gate = build_gate(ratio, limit)
        portfolio_gate = portfolio_mod.gate_result(ratio, limit)
        self.assertEqual(portfolio_gate, report_gate)
        self.assertTrue(report_gate["breached"])
        self.assertTrue(portfolio_gate["breached"])

    def test_no_limit_means_no_gate(self):
        self.assertIsNone(build_gate(0.5, None))
        self.assertIsNone(portfolio_mod.gate_result(0.5, None))

    def test_an_out_of_range_limit_is_rejected(self):
        for limit in (-0.1, 1.5):
            with self.assertRaises(ValueError) as ctx:
                build_gate(0.5, limit)
            self.assertIn("gate-limit-invalid", str(ctx.exception))

    def test_the_portfolio_rejects_a_bad_limit_before_scanning(self):
        # Validated inside the per-repository scan, every repository
        # "failed" and the operator saw `portfolio-empty: every repository
        # failed to scan` instead of the real cause.
        with self.assertRaises(ValueError) as ctx:
            portfolio_mod.build_portfolio([Path(".")], _roster(),
                                          max_ai_ratio=1.5)
        self.assertIn("gate-limit-invalid", str(ctx.exception))


class RatioPrecisionTests(unittest.TestCase):
    """M3: one quantity, one value, one rounding place — the renderer."""

    def test_the_ratio_is_exact(self):
        self.assertEqual(compute_ratio(1, 3), 1 / 3)

    def test_a_zero_denominator_is_zero(self):
        self.assertEqual(compute_ratio(0, 0), 0.0)
        self.assertEqual(compute_ratio(3, 0), 0.0)

    def test_the_report_carries_one_ratio_not_two_roundings(self):
        # `ai_ratio` and `gate.ratio` are the same quantity. The report
        # used to carry the rounded one in `gate.ratio` and the exact one
        # in `ai_ratio`: two numbers for one quantity in one artifact.
        rep = build_report(_three_commits(), _roster(), max_ai_ratio=0.9)
        self.assertEqual(rep.gate["ratio"], rep.ai_ratio)
        self.assertEqual(rep.gate["ratio"], 1 / 3)

    def test_the_markdown_reads_the_model_not_the_buckets(self):
        # A renderer that recomputes the ratio from the buckets is
        # re-deriving a number the model already holds. Mutating the model
        # must change the rendering.
        rep = build_report(_three_commits(), _roster())
        self.assertIn("33.3%", render_markdown(rep))
        rep.ai_ratio = 0.5
        self.assertIn("50.0%", render_markdown(rep))

    def test_the_portfolio_total_is_not_rounded(self):
        entries = [portfolio_mod.RepoEntry(name="a", path="a", ref="HEAD",
                                          total_commits=3, agent_commits=1,
                                          ai_ratio=1 / 3)]
        totals = portfolio_mod.aggregate_totals(entries)
        self.assertEqual(totals["ai_ratio"], 1 / 3)

    def test_the_mean_repo_ratio_is_not_rounded(self):
        entries = [portfolio_mod.RepoEntry(name="a", path="a", ref="HEAD",
                                          total_commits=3, agent_commits=1,
                                          ai_ratio=1 / 3),
                   portfolio_mod.RepoEntry(name="b", path="b", ref="HEAD",
                                          total_commits=3, agent_commits=2,
                                          ai_ratio=2 / 3)]
        totals = portfolio_mod.aggregate_totals(entries)
        self.assertAlmostEqual(totals["mean_repo_ratio"], 0.5, places=12)

    def test_the_json_carries_the_exact_ratio(self):
        rep = build_report(_three_commits(), _roster(), max_ai_ratio=0.9)
        doc = json.loads(report_mod.render_json(rep))
        self.assertEqual(doc["ai_ratio"], 1 / 3)
        self.assertEqual(doc["gate"]["ratio"], 1 / 3)


class CoverageActionTests(unittest.TestCase):
    """M4: a command printed in a deliverable must be a command that runs."""

    def _no_signatures_action(self, roster):
        cov = build_coverage([_commit("plain human work")], roster)
        return next(a for a in cov.actions if a.code == "no-signatures")

    def test_the_action_names_a_signable_roster_agent(self):
        roster = _roster()
        action = self._no_signatures_action(roster)
        self.assertNotIn("<agent-id>", action.command)
        self.assertTrue(
            action.command.startswith("ghostauthor install-hook . --agent "),
            action.command)

    def test_the_named_agent_can_actually_sign(self):
        # Naming an id that cannot sign installs a hook that fails at
        # commit time — worse than saying "add a rule first".
        roster = _roster()
        action = self._no_signatures_action(roster)
        agent_id = action.command.rsplit("--agent ", 1)[1].strip()
        agent = next(a for a in roster.agents if a.id == agent_id)
        self.assertIsNotNone(agent.signing)

    def test_without_a_signable_agent_the_placeholder_is_used(self):
        roster = Roster(version=1, agents=[
            AgentRule(id="plain", display_name="Plain",
                      name_patterns=(re.compile("^Plain\b"),),
                      email_domains=frozenset())])
        action = self._no_signatures_action(roster)
        self.assertIn("<agent-id>", action.command)
        self.assertIn("No roster agent has a signing identity", action.detail)


class ScopeFlagRoundTripTests(unittest.TestCase):
    """L1: the brief's regenerate command must be one that works."""

    def _parse(self, flags):
        """Turn `--all` / `--base X --ref Y` back into a scope mapping."""
        parts = flags.split()
        scope = {"all_refs": False, "ref": None, "base": None}
        i = 0
        while i < len(parts):
            if parts[i] == "--all":
                scope["all_refs"] = True
            elif parts[i] == "--base":
                scope["base"] = parts[i + 1]
                i += 1
            elif parts[i] == "--ref":
                scope["ref"] = parts[i + 1]
                i += 1
            i += 1
        return scope

    def test_every_scope_label_round_trips_back_to_its_scope(self):
        for scope in (Scope(all_refs=True),
                      Scope(ref="main"),
                      Scope(ref="feature/x"),
                      Scope(base="main", ref="feature"),
                      Scope(base="main")):
            flags = _scope_flags(scope.label)
            self.assertTrue(flags, f"no flags for {scope.label!r}")
            back = self._parse(flags)
            self.assertEqual(back["all_refs"], scope.all_refs, scope.label)
            self.assertEqual(back["ref"], scope.ref, scope.label)
            self.assertEqual(back["base"], scope.base, scope.label)

    def test_the_default_scope_needs_no_flags(self):
        self.assertEqual(_scope_flags(Scope().label), "")

    def test_a_malformed_range_yields_no_command_rather_than_a_wrong_one(self):
        # A range is `base..target` and exactly that. A second "..", an
        # empty side — these are not labels this project produces, and the
        # old string surgery turned them into a plausible-looking command
        # that cannot run.
        for label in ("a..b..c", "..x", "x..", "main..feature..x"):
            self.assertEqual(_scope_flags(label), "", label)

    def test_a_plain_name_is_a_ref_name_not_an_unknown_label(self):
        # `Scope(ref="garbage").label` IS "garbage" — a single label is a
        # ref name, and the round trip has to keep working for it.
        self.assertEqual(_scope_flags(Scope(ref="garbage").label),
                         "--ref garbage")
        self.assertEqual(_scope_flags(""), "")

    def test_every_flag_emitted_is_a_flag_the_cli_accepts(self):
        known = set(cli_mod.build_parser()._option_string_actions)
        for scope in (Scope(all_refs=True),
                      Scope(ref="main"),
                      Scope(base="main", ref="feature"),
                      Scope(base="main")):
            for flag in re.findall(r"--[a-z-]+", _scope_flags(scope.label)):
                self.assertIn(flag, known, f"{flag} is not a CLI flag")


class ScopeErrorTextTests(unittest.TestCase):
    """L2: the error must name a flag the CLI really has."""

    def _detail(self, **kwargs):
        with self.assertRaises(OptionError) as ctx:
            Scope(**kwargs).validate()
        self.assertEqual(ctx.exception.code, "ref-conflict")
        return ctx.exception.detail

    def test_the_conflict_error_names_the_real_cli_flag(self):
        detail = self._detail(all_refs=True, ref="main")
        self.assertIn("--all", detail)
        self.assertNotIn("--all_refs", detail)

    def test_the_base_conflict_also_names_the_real_cli_flag(self):
        detail = self._detail(all_refs=True, base="main")
        self.assertIn("--all", detail)

    def test_every_flag_the_error_names_is_a_flag_the_cli_has(self):
        known = set(cli_mod.build_parser()._option_string_actions)
        for kwargs in ({"all_refs": True, "ref": "main"},
                       {"all_refs": True, "base": "main"}):
            detail = self._detail(**kwargs)
            named = re.findall(r"--[a-z-]+", detail)
            self.assertTrue(named, detail)
            for flag in named:
                self.assertIn(flag, known, f"{flag} is not a CLI flag")


class RosterRuleIdTests(unittest.TestCase):
    """L3: the roster's precise rule id has a documented position."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-bound-roster-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _bad_roster(self, content):
        path = self.tmp / "bad.json"
        path.write_text(content, encoding="utf-8")
        return path

    def _rule_id(self, path):
        with self.assertRaises(OptionError) as ctx:
            resolve_roster(str(path))
        self.assertEqual(ctx.exception.code, "roster-error")
        return ctx.exception.detail

    def test_the_surface_code_stays_roster_error(self):
        detail = self._rule_id(self._bad_roster(
            '{"version": 1, "agents": [{"id": "BAD", "display_name": "x", '
            '"name_patterns": ["^X"], "email_domains": []}]}'))
        self.assertTrue(detail.startswith("roster-"), detail)

    def test_the_detail_starts_with_the_rule_id(self):
        detail = self._rule_id(self._bad_roster(
            '{"version": 1, "agents": [{"id": "BAD", "display_name": "x", '
            '"name_patterns": ["^X"], "email_domains": []}]}'))
        rule_id, _, rest = detail.partition(": ")
        self.assertEqual(rule_id, "roster-agent-id")
        self.assertTrue(rest.strip())

    def test_the_rule_id_is_one_the_roster_module_actually_raises(self):
        source = (ROOT / "ghostauthor" / "roster.py").read_text(encoding="utf-8")
        known = set(re.findall(r'_fail\(\s*"([a-z0-9-]+)"', source))
        self.assertTrue(known)
        detail = self._rule_id(self._bad_roster(
            '{"version": 1, "agents": [{"id": "BAD", "display_name": "x", '
            '"name_patterns": ["^X"], "email_domains": []}]}'))
        rule_id = detail.partition(":")[0]
        self.assertIn(rule_id, known)

    def test_an_unreadable_roster_keeps_the_same_shape(self):
        path = self.tmp / "gone.json"
        detail = self._rule_id(path)
        self.assertTrue(detail.startswith("roster-"), detail)


class RuleCatalogueTests(unittest.TestCase):
    """L4: ONE catalogue — the engine names the ids, the report describes
    them. A rule in one file and not the other rendered a SARIF result with
    an EMPTY description, silently."""

    def test_every_rule_the_engine_can_emit_is_in_the_catalogue(self):
        roster = _roster()
        commits = [
            _commit("work\n\nCo-Authored-By: Claude\n", sha="b" * 40),
            _commit("work\n\nCo-Authored-By: Mystery <mystery@nowhere.invalid>\n",
                    sha="c" * 40),
            _commit("fix\n\nCo-Authored-By: THIS IS PROSE\n\n"
                    "Co-Authored-By: Claude <noreply@anthropic.com>\n",
                    sha="d" * 40),
            _commit("work\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                    "Co-Authored-By: OpenAI Codex <noreply@openai.com>\n",
                    sha="e" * 40),
        ]
        rep = build_report(commits, roster, max_ai_ratio=0.0)
        emitted = {f.rule for f in rep.findings}
        emitted.add("ai-ratio-gate")  # the gate rule, when it breaches
        self.assertTrue(emitted)
        self.assertTrue(emitted.issubset(set(RULE_INFO)),
                        f"no description for: {emitted - set(RULE_INFO)}")

    def test_the_catalogue_covers_every_rule_the_engine_emits_in_practice(self):
        # The four findings the engine can raise from a commit, plus the
        # gate rule: all of them must be describable.
        roster = _roster()
        commits = [
            _commit("work\n\nCo-Authored-By: Claude\n", sha="b" * 40),
            _commit("work\n\nCo-Authored-By: Mystery <mystery@nowhere.invalid>\n",
                    sha="c" * 40),
            _commit("fix\n\nCo-Authored-By: THIS IS PROSE\n\n"
                    "Co-Authored-By: Claude <noreply@anthropic.com>\n",
                    sha="d" * 40),
            _commit("work\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                    "Co-Authored-By: OpenAI Codex <noreply@openai.com>\n",
                    sha="e" * 40),
        ]
        rep = build_report(commits, roster, max_ai_ratio=0.0)
        emitted = {f.rule for f in rep.findings}
        self.assertEqual(
            emitted,
            {"trailer-no-email", "cosigned-unknown", "trailer-in-body",
             "multi-agent-commit"})

    def test_no_catalogue_entry_is_empty(self):
        for rule_id, (name, description) in RULE_INFO.items():
            self.assertTrue(name.strip(), rule_id)
            self.assertTrue(description.strip(), rule_id)

    def test_the_sarif_fallback_is_the_rule_id_not_nothing(self):
        # The empty shortDescription was the silent failure: a SARIF rule
        # with no description looks like a rule with no explanation.
        rep = build_report([_commit("plain")], _roster())
        rep.findings.append(Finding(rule="not-in-the-catalogue",
                                    severity="warn", evidence="x",
                                    detail="y"))
        doc = json.loads(render_sarif(rep))
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        entry = next(r for r in rules if r["id"] == "not-in-the-catalogue")
        self.assertEqual(entry["shortDescription"]["text"],
                         "not-in-the-catalogue")

    def test_the_catalogue_lives_in_models(self):
        # ONE source: the engine names the ids from here and the report
        # describes them from here.
        self.assertIs(report_mod.RULE_INFO, models_mod.RULE_INFO)


class PackagingContractTests(unittest.TestCase):
    """M5/L6: the supported floor, and which channel ships what."""

    def _pyproject(self):
        return (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_the_supported_floor_is_310(self):
        m = re.search(r'requires-python\s*=\s*"([^"]+)"', self._pyproject())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), ">=3.10")

    def test_no_classifier_claims_39(self):
        text = self._pyproject()
        self.assertNotIn("Python :: 3.9", text)
        self.assertIn("Python :: 3.10", text)

    def test_the_wheel_carries_only_the_package(self):
        # `include = ["ghostauthor*"]` matches packages, and a directory
        # without `__init__.py` is not one — that is what keeps the skill
        # pack out of the wheel.
        m = re.search(r"include\s*=\s*\[([^\]]*)\]", self._pyproject())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip().strip('"'), "ghostauthor*")

    def test_the_skill_pack_is_not_a_python_package(self):
        self.assertFalse(
            (ROOT / "ghostauthor-ai-code-audit" / "__init__.py").exists())
        self.assertTrue(
            (ROOT / "ghostauthor-ai-code-audit" / "SKILL.md").is_file())

    def test_the_docs_say_the_skill_pack_ships_separately(self):
        arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn("What ships where", arch)
        self.assertIn("not** in the wheel", arch)

    def test_the_docs_state_the_byte_stability_version_premise(self):
        arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn("Byte stability has a version premise", arch)
        schema = (ROOT / "docs" / "schema.md").read_text(encoding="utf-8")
        self.assertIn("one tool version", schema)


class VerifyStepNumberingTests(unittest.TestCase):
    """M6: the step numbers are the order — no gaps, no duplicates."""

    def _numbers(self):
        text = (ROOT / "verify.py").read_text(encoding="utf-8")
        return [int(m) for m in re.findall(r"^\s*# (\d+)\.", text, re.M)]

    def test_the_step_numbers_are_continuous_from_one(self):
        numbers = self._numbers()
        self.assertTrue(numbers)
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_every_step_has_a_numbered_comment(self):
        text = (ROOT / "verify.py").read_text(encoding="utf-8")
        calls = len(re.findall(r"^\s*step\(", text, re.M))
        self.assertEqual(calls, len(self._numbers()))

    def test_the_docs_cite_steps_that_exist(self):
        numbers = set(self._numbers())
        for doc in ("schema.md", "ci.md", "architecture.md"):
            text = (ROOT / "docs" / doc).read_text(encoding="utf-8")
            for cited in re.findall(r"steps?\s+(\d+)", text):
                self.assertIn(int(cited), numbers,
                              f"{doc} cites step {cited}, which does not exist")


def _three_commits():
    """One agent-signed commit out of three — the ratio is exactly 1/3."""
    return [
        _commit("work\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
                sha="a" * 40),
        _commit("plain human work", sha="b" * 40),
        _commit("more human work", sha="c" * 40),
    ]


if __name__ == "__main__":
    unittest.main()
