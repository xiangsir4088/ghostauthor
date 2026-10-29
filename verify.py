"""One-click self-check: roster + tests + fixtures + CLI + MCP + web + rendering.

Runs every subprocess with sys.executable (no hard-coded interpreter
path) and prints, for each step, what passed — and prints anything it
skipped and WHY. Silent skips are worse than errors, so there are none.

Exit code 0 means the whole matrix is green.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from examples.make_fixtures import (build_agent_heavy, build_malformed,
                                    build_mixed)
from ghostauthor.roster import (RosterError, builtin_roster_path,
                                load_roster)

FAILURES = []
SKIPPED = []


def step(name, fn):
    try:
        fn()
        print(f"[ok]   {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"[FAIL] {name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a verify run must not die silently
        FAILURES.append(name)
        print(f"[ERR]  {name}: {type(exc).__name__}: {exc}")


def skip(name, reason):
    SKIPPED.append(name)
    print(f"[skip] {name}: {reason}")


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "ghostauthor.cli", *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", cwd=str(cwd or ROOT))


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(detail or name)


def main() -> int:
    print("ghostauthor verify — one-click self-check")
    print("=" * 44)

    # 1. built-in roster validates
    def roster_ok():
        roster = load_roster(builtin_roster_path())
        check("roster_ok", len(roster.agents) >= 5,
              f"expected >=5 agents, got {len(roster.agents)}")

    step(f"builtin roster ({len(load_roster(builtin_roster_path()).agents)} agents)", roster_ok)

    # 2. roster validator rejects malformed rosters (itself tested)
    def roster_negative():
        bad = {"version": 1, "agents": [
            {"id": "a", "display_name": "A", "name_patterns": ["("],
             "email_domains": []}]}
        try:
            from ghostauthor.roster import validate_roster
            validate_roster(bad)
        except RosterError:
            return
        raise AssertionError("malformed roster was accepted")

    step("roster validator rejects malformed roster", roster_negative)

    # 3. full unit suite (includes negative cases)
    def unit_tests():
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            capture_output=True, text=True, cwd=str(ROOT))
        check("unit_tests", proc.returncode == 0,
              (proc.stdout + proc.stderr)[-2000:])

    step("unit tests (unittest discover)", unit_tests)

    # 4. build the three fixtures in a temp dir
    tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-verify-"))
    try:
        mixed = build_mixed(tmp)
        heavy = build_agent_heavy(tmp)
        malformed = build_malformed(tmp)
        step(f"fixtures built under {tmp.name}",
             lambda: [check(p.is_dir(), f"{p} missing") for p in
                     (mixed, heavy, malformed)])

        # 5. mixed: exit 0 + golden buckets
        def mixed_ok():
            proc = run_cli(mixed, "--format", "json")
            check("mixed_exit", proc.returncode == 0,
                  f"rc={proc.returncode} stderr={proc.stderr}")
            data = json.loads(proc.stdout)
            b = data["commits_by_bucket"]
            check("mixed_buckets", b == {"agent": 6, "cosigned": 1, "unsigned": 5},
                  f"buckets={b}")
            check("mixed_ratio", abs(data["ai_ratio"] - 0.5) < 1e-9,
                  f"ratio={data['ai_ratio']}")
            agents = {a["id"]: a["commits"] for a in data["agents"]}
            check("mixed_agents", agents == {"claude": 4, "codex": 3},
                  f"agents={agents}")
            rules = {f["rule"] for f in data["findings"]}
            check("mixed_findings",
                  {"cosigned-unknown", "trailer-in-body", "multi-agent-commit"} <= rules,
                  f"rules={rules}")

        step("CLI on 'mixed': exit 0, golden buckets, traceable findings", mixed_ok)

        # 6. agent-heavy: gate contract
        def heavy_ok():
            p0 = run_cli(heavy, "--format", "json")
            check("heavy_no_gate", p0.returncode == 0, p0.stderr)
            data = json.loads(p0.stdout)
            check("heavy_ratio", abs(data["ai_ratio"] - 11 / 12) < 1e-9,
                  f"ratio={data['ai_ratio']}")
            p4 = run_cli(heavy, "--max-ai-ratio", "0.5", "--format", "json")
            check("heavy_gate_breach", p4.returncode == 4,
                  f"expected exit 4, got {p4.returncode}")
            g = json.loads(p4.stdout)["gate"]
            check("heavy_gate_flag", g["breached"] is True, f"gate={g}")
            p_ok = run_cli(heavy, "--max-ai-ratio", "1.0")
            check("heavy_gate_pass", p_ok.returncode == 0,
                  f"expected exit 0, got {p_ok.returncode}")

        step("CLI on 'agent-heavy': gate exits 4 at 0.5, passes at 1.0", heavy_ok)

        # 7. malformed: classified, never crashed
        def malformed_ok():
            proc = run_cli(malformed, "--format", "json")
            check("malformed_exit", proc.returncode == 0,
                  f"rc={proc.returncode} stderr={proc.stderr}")
            data = json.loads(proc.stdout)
            b = data["commits_by_bucket"]
            check("malformed_buckets", b == {"agent": 3, "cosigned": 1,
                                             "unsigned": 1}, f"buckets={b}")
            rules = {f["rule"] for f in data["findings"]}
            check("malformed_findings",
                  {"trailer-no-email", "cosigned-unknown", "trailer-in-body"} <= rules,
                  f"rules={rules}")

        step("CLI on 'malformed': classified with findings, no crash", malformed_ok)

        # 8. rendering to files (out/ is gitignored)
        def render_ok():
            out = ROOT / "out"
            out.mkdir(exist_ok=True)
            for fmt, suffix in (("text", "txt"), ("json", "json"),
                                ("markdown", "md")):
                p = run_cli(mixed, "--format", fmt, "--out", out / f"mixed.{suffix}")
                check(f"render_{fmt}", p.returncode == 0, p.stderr)
                check(f"render_{fmt}_nonempty",
                      (out / f"mixed.{suffix}").stat().st_size > 0)

        step("rendering: text/json/markdown for 'mixed'", render_ok)

        # 9. real MCP protocol: initialize / tools/list / tools/call
        def mcp_ok():
            reqs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "repo_authorship_report",
                            "arguments": {"path": str(mixed)}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
            stdin = "\n".join(json.dumps(r) for r in reqs) + "\n"
            proc = subprocess.run(
                [sys.executable, "-m", "ghostauthor.mcp_server"],
                input=stdin, capture_output=True, text=True, cwd=str(ROOT))
            check("mcp_rc", proc.returncode == 0, proc.stderr)
            lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
            by_id = {l.get("id"): l for l in lines}
            check("mcp_initialize",
                  by_id[1]["result"]["serverInfo"]["name"] == "ghostauthor")
            check("mcp_tools",
                  by_id[2]["result"]["tools"][0]["name"] == "repo_authorship_report")
            result = by_id[3]["result"]
            check("mcp_iserror", result["isError"] is False)
            payload = json.loads(result["content"][0]["text"])
            check("mcp_buckets", payload["commits_by_bucket"]["agent"] == 6,
                  f"buckets={payload['commits_by_bucket']}")

        step("MCP protocol: initialize / tools/list / tools/call", mcp_ok)

        # 10. time window on 'mixed': since inclusive / until exclusive
        # (mixed fixture commits: BASE_TS + i*3600, i = 0..11)
        def window_ok():
            base = 1767225600
            proc = run_cli(mixed, "--format", "json",
                           "--since", str(base + 5 * 3600))
            check("window_rc", proc.returncode == 0,
                  f"rc={proc.returncode} stderr={proc.stderr}")
            data = json.loads(proc.stdout)
            b = data["commits_by_bucket"]
            # i=5..11: agent(6,7,10) + cosigned(9) + unsigned(5,8,11)
            check("window_buckets", b == {"agent": 3, "cosigned": 1,
                                          "unsigned": 3}, f"buckets={b}")
            check("window_meta",
                  data["window"]["since"] == base + 5 * 3600)
            p2 = run_cli(mixed, "--format", "json",
                         "--since", str(base + 5 * 3600),
                         "--until", str(base + 6 * 3600))
            d2 = json.loads(p2.stdout)
            check("window_until_exclusive", d2["total_commits"] == 1,
                  f"total={d2['total_commits']}")

        step("CLI time window: since inclusive / until exclusive", window_ok)

        # 11. roster-dir: cross-file duplicate id must be rejected
        def roster_dir_ok():
            import tempfile
            from ghostauthor.roster import RosterError, load_roster_dir
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for name, disp in (("a.json", "Alpha"),
                                   ("b.json", "Alpha dup")):
                    (root / name).write_text(json.dumps({
                        "version": 1,
                        "agents": [{"id": "alpha", "display_name": disp,
                                    "name_patterns": ["^Alpha\\b"],
                                    "email_domains": []}]}),
                        encoding="utf-8")
                try:
                    load_roster_dir(root)
                    raise AssertionError("cross-file duplicate was accepted")
                except RosterError as exc:
                    check("roster_dir_dup", "roster-dir-dup" in str(exc))

        step("roster-dir: cross-file duplicate id rejected", roster_dir_ok)

        # 12. SARIF output on 'mixed'
        def sarif_ok():
            proc = run_cli(mixed, "--format", "sarif")
            check("sarif_rc", proc.returncode == 0,
                  f"rc={proc.returncode} stderr={proc.stderr}")
            doc = json.loads(proc.stdout)
            check("sarif_version", doc["version"] == "2.1.0")
            run = doc["runs"][0]
            check("sarif_tool",
                  run["tool"]["driver"]["name"] == "ghostauthor")
            rule_ids = {r["ruleId"] for r in run["results"]}
            check("sarif_findings",
                  rule_ids == {"cosigned-unknown", "multi-agent-commit",
                               "trailer-in-body"}, f"rules={rule_ids}")
            check("sarif_rule_descriptors",
                  len(run["tool"]["driver"]["rules"]) == 3)

        step("SARIF 2.1.0: findings mapped to results + rules", sarif_ok)

        # 13. web: a real HTTP server — the JSON it serves is byte-identical
        # to the CLI's, the index page loads, and per-entry errors carry the
        # documented codes (not-a-repo => 404)
        def web_ok():
            import threading
            import urllib.request
            from http.server import ThreadingHTTPServer
            from ghostauthor.web import Handler
            srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{port}"
                with urllib.request.urlopen(base + "/") as resp:
                    check("web_index_status", resp.status == 200)
                    page = resp.read()
                check("web_index_body",
                      b"ghostauthor" in page and b"Run audit" in page)
                payload = json.dumps(
                    {"entries": [{"path": str(mixed)}]}).encode()
                req = urllib.request.Request(
                    base + "/api/audit", data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req) as resp:
                    check("web_audit_status", resp.status == 200)
                    audit = json.loads(resp.read())
                rep = audit["results"][0]["report"]
                check("web_buckets",
                      rep["commits_by_bucket"] == {"agent": 6, "cosigned": 1,
                                                   "unsigned": 5},
                      f"buckets={rep['commits_by_bucket']}")
                check("web_byte_stable",
                      rep == json.loads(
                          run_cli(mixed, "--format", "json").stdout),
                      "the web report differs from the CLI JSON")
                bad = json.dumps(
                    {"entries": [{"path": str(tmp / "nope")}]}).encode()
                req2 = urllib.request.Request(
                    base + "/api/audit", data=bad,
                    headers={"Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req2) as resp:
                    bad_body = json.loads(resp.read())
                entry = bad_body["results"][0]
                check("web_not_a_repo",
                      entry["status"] == 404
                      and entry["error"]["code"] == "not-a-repo",
                      f"entry={entry}")
            finally:
                srv.shutdown()
                srv.server_close()

        step("web: HTTP server, JSON byte-identical to the CLI, error codes",
             web_ok)

        # 15. two-branch diff (--base) + author x agent cross-tab
        def diff_xtab_ok():
            import os
            import subprocess
            diffrepo = Path(tmp) / "diffrepo"
            diffrepo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(diffrepo)],
                           check=True, capture_output=True)

            def dcommit(ts, msg, content, who=("Sam", "sam@example.com")):
                (diffrepo / "f.txt").write_text(content, encoding="utf-8")
                e = dict(os.environ)
                e.update({"GIT_AUTHOR_NAME": who[0],
                          "GIT_AUTHOR_EMAIL": who[1],
                          "GIT_COMMITTER_NAME": who[0],
                          "GIT_COMMITTER_EMAIL": who[1],
                          "GIT_AUTHOR_DATE": f"{ts} +0000",
                          "GIT_COMMITTER_DATE": f"{ts} +0000"})
                subprocess.run(["git", "-C", str(diffrepo), "add", "-A"],
                               check=True, env=e, capture_output=True)
                subprocess.run(["git", "-C", str(diffrepo), "commit", "-q",
                                "-m", msg], check=True, env=e,
                               capture_output=True)

            dcommit(1767225600, "one", "1\n")
            dcommit(1767229200,
                    "two\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                    "2\n")
            subprocess.run(["git", "-C", str(diffrepo), "checkout", "-q",
                            "-b", "side"], check=True, capture_output=True)
            dcommit(1767232800,
                    "side\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                    "3\n", who=("Ana", "ana@example.com"))
            subprocess.run(["git", "-C", str(diffrepo), "checkout", "-q",
                            "main"], check=True, capture_output=True)

            proc = run_cli(diffrepo, "--format", "json", "--base", "main",
                           "--ref", "side")
            check("diff_rc", proc.returncode == 0,
                  f"rc={proc.returncode} stderr={proc.stderr}")
            data = json.loads(proc.stdout)
            check("diff_ref_label", data["ref"] == "main..side",
                  f"ref={data['ref']}")
            check("diff_total", data["total_commits"] == 1,
                  f"total={data['total_commits']}")
            check("diff_buckets",
                  data["commits_by_bucket"] == {"agent": 1, "cosigned": 0,
                                                "unsigned": 0})
            check("diff_xtab",
                  data["authors"][0]["by_agent"] == {"claude": 1}
                  and data["authors"][0]["email"] == "ana@example.com")
            # negative: --base is mutually exclusive with --all
            p2 = run_cli(diffrepo, "--all", "--base", "main")
            check("diff_all_conflict", p2.returncode == 2,
                  f"rc={p2.returncode}")
            # negative: an empty range is no-commits, never a silent report
            p3 = run_cli(diffrepo, "--base", "main", "--ref", "main")
            check("diff_empty_range", p3.returncode == 2
                  and "no-commits" in p3.stderr,
                  f"rc={p3.returncode} stderr={p3.stderr}")

            # cross-tab on the mixed fixture (single author, all columns)
            pm = run_cli(mixed, "--format", "json")
            am = json.loads(pm.stdout)["authors"]
            check("xtab_cells", len(am) == 1
                  and am[0]["by_agent"] == {"claude": 4, "codex": 3,
                                            "cosigned": 1, "unsigned": 5},
                  f"authors={am}")
            pt = run_cli(mixed)
            check("xtab_text", "authors x agents:" in pt.stdout)

        step("CLI two-branch diff (--base) + author x agent cross-tab",
             diff_xtab_ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 16. housekeeping: no stray build artifacts in the source tree
    def clean_ok():
        for stray in (ROOT / "build", ROOT / "dist", ROOT / "ghostauthor.egg-info"):
            check(f"clean_{stray.name}", not stray.exists())

    step("no build artifacts in the source tree", clean_ok)

    # 17. CI assets: action.yml + workflows + docs stay consistent
    def ci_assets_ok():
        action = ROOT / "action.yml"
        check("ci_action_exists", action.exists(), "action.yml missing")
        text = action.read_text(encoding="utf-8")
        # Composite action shape: runs.using composite, one setup-python step.
        check("ci_action_composite", "using: \"composite\"" in text)
        check("ci_action_setup_python", "actions/setup-python@v5" in text)
        # Every declared input must be surfaced in the audit step env block.
        required_inputs = ["path", "ref", "base", "all", "roster", "roster-dir",
                           "since", "until", "session-gap", "max-ai-ratio",
                           "format", "out", "fail-on-breach", "python-version"]
        for name in required_inputs:
            check(f"ci_input_{name}", f"inputs.{name}" in text,
                  f"input {name} not referenced in action.yml")
        for out in ("report", "breached", "exit-code"):
            check(f"ci_output_{out}", f"{out}:" in text,
                  f"output {out} missing in action.yml")
        # Exit-code contract must be preserved in the audit step.
        check("ci_exit4_passthrough", '"$code" -eq 4' in text,
              "action must let exit 4 surface")
        workflows = ROOT / ".github" / "workflows"
        ci = workflows / "ci.yml"
        example = workflows / "pr-audit.example.yml"
        check("ci_workflow_exists", ci.exists())
        check("ci_pr_example_exists", example.exists())
        ci_text = ci.read_text(encoding="utf-8")
        for needle in ("python verify.py", "self-audit", "actions/checkout@v4"):
            check(f"ci_workflow_{needle.split()[0]}", needle in ci_text,
                  f"ci.yml missing {needle!r}")
        ex_text = example.read_text(encoding="utf-8")
        for needle in ("xiangsir/ghostauthor@v1", "pull-requests: write",
                       "pr comment"):
            check(f"ci_example_{needle.split()[0]}", needle in ex_text,
                  f"pr-audit.example.yml missing {needle!r}")
        # Docs cover the same contract the action declares.
        doc = (ROOT / "docs" / "ci.md")
        check("ci_docs_exists", doc.exists())
        doc_text = doc.read_text(encoding="utf-8")
        for name in ("max-ai-ratio", "fail-on-breach", "exit-code"):
            check(f"ci_docs_{name}", name in doc_text,
                  f"docs/ci.md missing {name}")

    step("CI assets (action.yml + workflows + docs) consistent", ci_assets_ok)

    # 18. schema freeze: report.schema.json is valid, the real CLI output
    #     validates against it, the golden snapshot is byte-stable, and the
    #     freeze is ENFORCED (a mutated report must be rejected).
    def schema_freeze_ok():
        from tests.golden import GOLDEN_FILE, golden_bytes
        from tests.schemacheck import (SCHEMA_FILE, load_schema,
                                       schema_meta_errors, validate)
        import copy as _copy
        import json as _json

        check("schema_file_exists", SCHEMA_FILE.exists(),
              "schema/report.schema.json missing")
        schema = load_schema()
        # (a) the schema itself is valid — within the frozen keyword subset
        meta = schema_meta_errors(schema)
        check("schema_meta", not meta, f"{meta[:3]}")
        check("schema_pins_15_keys",
              len(schema["required"]) == 15 and
              set(schema["required"]) == set(schema["properties"]),
              f"keys={sorted(schema['required'])}")

        # (b) the REAL CLI output validates against the frozen schema
        tmp2 = Path(tempfile.mkdtemp(prefix="ghostauthor-schema-"))
        try:
            mixed2 = build_mixed(tmp2)
            proc = run_cli(mixed2, "--format", "json")
            check("schema_cli_exit", proc.returncode == 0, proc.stderr)
            data = _json.loads(proc.stdout)
            errs = validate(data, schema)
            check("schema_cli_output", not errs, f"{errs[:3]}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        # (c) golden snapshot: re-rendering the fixed dataset is byte-stable
        check("golden_exists", GOLDEN_FILE.exists(), "golden snapshot missing")
        check("golden_bytes",
              golden_bytes() == GOLDEN_FILE.read_text(encoding="utf-8"),
              "golden snapshot drifts from render_json output")
        # and the golden itself is a schema-positive instance
        golden_data = _json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
        check("golden_validates", not validate(golden_data, schema),
              f"{validate(golden_data, schema)[:3]}")

        # (d) the freeze is enforced: mutations must be rejected by name
        broken = _copy.deepcopy(data)
        del broken["total_commits"]
        check("freeze_rejects_removed_key",
              any("total_commits" in e for e in validate(broken, schema)))
        broken = _copy.deepcopy(data)
        broken["bogus"] = 1
        check("freeze_rejects_added_key",
              any("bogus" in e for e in validate(broken, schema)))

    step("schema freeze (report.schema.json + golden + enforced)",
         schema_freeze_ok)

    # 19. write path (v1.0): the four invariants, machine-checked on a
    #     fixture repo — round-trip (load-bearing), idempotency,
    #     conflict refusal, default-off. Error paths are pinned too.
    def write_path_ok():
        import json as _json
        import os as _os

        sign_tmp = Path(tempfile.mkdtemp(prefix="ghostauthor-sign-"))
        try:
            repo = sign_tmp / "repo"
            repo.mkdir()
            roster_raw = {
                "version": 1,
                "agents": [
                    {"id": "claude", "display_name": "Claude (Anthropic)",
                     "name_patterns": ["^Claude\\b"],
                     "email_domains": ["anthropic.com"],
                     "signing": {"name": "Claude",
                                 "email": "noreply@anthropic.com"}},
                    {"id": "codex", "display_name": "Codex (OpenAI)",
                     "name_patterns": ["^OpenAI Codex\\b"],
                     "email_domains": ["openai.com"],
                     "signing": {"name": "OpenAI Codex",
                                 "email": "noreply@openai.com"}},
                    {"id": "plain", "display_name": "Plain",
                     "name_patterns": ["^Plain\\b"], "email_domains": []},
                ],
            }
            roster_file = sign_tmp / "roster.json"
            roster_file.write_text(_json.dumps(roster_raw), encoding="utf-8")
            wild_roster = sign_tmp / "roster-wild.json"
            wild_roster.write_text(_json.dumps({
                "version": 1,
                "agents": [
                    {"id": "wild", "display_name": "Wild",
                     "name_patterns": [".*"], "email_domains": [],
                     "signing": {"name": "Wild",
                                 "email": "wild@wild.example"}},
                    roster_raw["agents"][0],
                ],
            }), encoding="utf-8")

            env = dict(_os.environ,
                       GIT_AUTHOR_NAME="Sam Human",
                       GIT_AUTHOR_EMAIL="sam@example.com",
                       GIT_COMMITTER_NAME="Sam Human",
                       GIT_COMMITTER_EMAIL="sam@example.com")

            def g(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               env=env, capture_output=True, text=True)

            g("init", "-b", "main")
            (repo / "f.txt").write_text("1\n", encoding="utf-8")
            g("add", "-A")
            g("commit", "-m", "initial")

            # CLI subprocesses get the controlled identity too: a real
            # `git commit --amend` needs a committer, and this step must
            # be hermetic (CI runners have no global git identity).
            def cli_env(*args):
                return subprocess.run(
                    [sys.executable, "-m", "ghostauthor.cli",
                     *[str(a) for a in args]],
                    capture_output=True, text=True, encoding="utf-8",
                    env=env, cwd=str(ROOT))

            def sign(*extra, roster=roster_file):
                return cli_env("sign", repo, "--roster", roster, *extra)

            def head():
                return subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"],
                    capture_output=True, text=True, env=env).stdout.strip()

            def message():
                return subprocess.run(
                    ["git", "-C", str(repo), "log", "-1", "--format=%B"],
                    capture_output=True, text=True, env=env).stdout

            # (a) default-off: no --commit means nothing is written
            p = sign("--agent", "claude")
            check("sign_default_off_rc", p.returncode == 0, p.stderr)
            check("sign_default_off_dryrun", "[dry-run]" in p.stdout)
            sha0 = head()
            check("sign_default_off_sha", head() == sha0,
                  "dry-run must not amend HEAD")
            check("sign_default_off_msg",
                  "Co-Authored-By: Claude" not in message())

            # (b) round-trip (load-bearing): sign -> audit attributes to
            #     the SAME roster agent
            p = sign("--agent", "claude", "--commit")
            check("sign_commit_rc", p.returncode == 0, p.stderr)
            sha1 = head()
            check("sign_commit_sha_changed", sha1 != sha0)
            audit = cli_env(repo, "--format", "json", "--roster", roster_file)
            check("sign_audit_rc", audit.returncode == 0, audit.stderr)
            data = _json.loads(audit.stdout)
            check("sign_roundtrip_bucket",
                  data["commits_by_bucket"]["agent"] == 1,
                  f"buckets={data['commits_by_bucket']}")
            check("sign_roundtrip_agent",
                  bool(data["agents"]) and data["agents"][0]["id"] == "claude",
                  f"agents={data['agents']}")
            check("sign_roundtrip_sha",
                  data["bucket_commit_shas"]["agent"] == [sha1])

            # (c) idempotency: a second sign is a no-op, the diff is empty
            p = sign("--agent", "claude", "--commit")
            check("sign_idempotent_rc", p.returncode == 0, p.stderr)
            check("sign_idempotent_noop", "already signed" in p.stdout)
            check("sign_idempotent_sha", head() == sha1,
                  "second sign must produce an empty diff")

            # (d) no-merge on conflict: another roster agent => refused,
            #     repository untouched
            p = sign("--agent", "codex", "--commit")
            check("sign_conflict_rc", p.returncode == 2)
            check("sign_conflict_err", "sign-conflict" in p.stderr)
            check("sign_conflict_untouched", head() == sha1)

            # (e) error paths exit 2 and change nothing
            p = sign("--agent", "nope")
            check("sign_unknown_agent",
                  p.returncode == 2 and "sign-agent-unknown" in p.stderr)
            p = sign("--agent", "plain")
            check("sign_no_signing",
                  p.returncode == 2 and "sign-no-signing" in p.stderr)
            p = sign("--agent", "claude", roster=wild_roster)
            check("sign_ambiguous_broad_pattern",
                  p.returncode == 2 and "sign-ambiguous" in p.stderr,
                  p.stderr)
            check("sign_ambiguous_untouched", head() == sha1)
        finally:
            shutil.rmtree(sign_tmp, ignore_errors=True)

    step("write path (v1.0): round-trip / idempotency / conflict / "
         "default-off", write_path_ok)

    print("=" * 44)
    if SKIPPED:
        print(f"skipped ({len(SKIPPED)}): " + ", ".join(SKIPPED))
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)} step(s): " + ", ".join(FAILURES))
        return 1
    print("RESULT: PASS — all steps green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
