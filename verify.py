"""One-click self-check: roster + tests + fixtures + CLI + MCP + rendering.

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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 10. housekeeping: no stray build artifacts in the source tree
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
