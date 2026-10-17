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
        capture_output=True, text=True, cwd=str(cwd or ROOT))


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
