"""Build the three example repositories deterministically.

Everything is pinned — commit dates, author identities, file contents —
so two runs produce byte-identical repositories (identical commit
SHAs). That is what makes the golden numbers asserted in verify.py
trustworthy.

The three fixtures cover three different conclusions:
  mixed        a healthy mix of human and agent work (normal case)
  agent-heavy  almost entirely agent-driven (gate case)
  malformed    everything the loader can be fed wrong (negative case)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple
from pathlib import Path

BASE_TS = 1767225600  # 2026-01-01T00:00:00Z, fixed

CLAUDE = "Co-Authored-By: Claude <noreply@anthropic.com>"
CODEX = "Co-Authored-By: OpenAI Codex <noreply@openai.com>"


def _env(ts: int) -> Dict[str, str]:
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "Sam Human",
        "GIT_AUTHOR_EMAIL": "sam@example.com",
        "GIT_COMMITTER_NAME": "Sam Human",
        "GIT_COMMITTER_EMAIL": "sam@example.com",
        "GIT_AUTHOR_DATE": f"{ts} +0000",
        "GIT_COMMITTER_DATE": f"{ts} +0000",
    })
    return e


def _git(repo: Path, *args: str, env: Dict[str, str]) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True, text=True)


def _new_repo(root: Path, name: str) -> Path:
    repo = root / name
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True,
                   capture_output=True, text=True)
    return repo


def _commit(repo: Path, ts: int, msg: str, files: Dict[str, str]) -> None:
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    env = _env(ts)
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-m", msg, env=env)


def build_mixed(root: Path) -> Path:
    """A healthy mix of human and agent work — the 'normal' case."""
    repo = _new_repo(root, "mixed")
    steps: List[Tuple[str, Optional[str], Dict[str, str]]] = [
        ("init project", None, {"main.py": "print('hello')\n"}),
        ("add parser", CLAUDE, {"parser.py": "x = 1\n"}),
        ("add cli", None, {"cli.py": "def main(): pass\n"}),
        ("fix off-by-one", CLAUDE, {"parser.py": "x = 2\n"}),
        ("add tests", CODEX, {"tests.txt": "assert 1\n"}),
        ("refactor cli", None, {"cli.py": "def main(n=1): pass\n"}),
        ("perf pass", CODEX, {"parser.py": "x = 3\n"}),
        ("docs", CLAUDE, {"README.md": "# demo\n"}),
        ("chore: bump", None, {"notes.txt": "1\n"}),
        ("hotfix", "Co-Authored-By: VibeBot <vibe@example.com>",
         {"cli.py": "def main(n=1): n += 1\n"}),
        ("multi-tool session", CLAUDE + "\n" + CODEX, {"notes.txt": "2\n"}),
        ("mention in prose", None, {"notes.txt": "3\n"}),
    ]
    for i, (subject, trailer, files) in enumerate(steps):
        if i == len(steps) - 1:
            # a trailer-shaped line in the WRONG paragraph: git does not
            # treat it as metadata (the final paragraph is prose), and
            # neither do we — but we flag it as a finding
            msg = (subject + "\n\nnotes below\n\nCo-Authored-By: Claude "
                   "<noreply@anthropic.com>\n\nthe real end paragraph")
        else:
            msg = subject if not trailer else f"{subject}\n\n{trailer}"
        _commit(repo, BASE_TS + i * 3600, msg, files)
    return repo


def build_agent_heavy(root: Path) -> Path:
    """Almost entirely agent-driven — the gate case."""
    repo = _new_repo(root, "agent-heavy")
    for i in range(12):
        if i == 0:
            _commit(repo, BASE_TS + i * 3600, "scaffold",
                    {"app.py": "print(0)\n"})
        else:
            _commit(repo, BASE_TS + i * 3600, f"agent change {i}\n\n{CLAUDE}",
                    {"app.py": f"print({i})\n"})
    return repo


def build_malformed(root: Path) -> Path:
    """Everything the loader can be fed wrong — it must classify, never crash."""
    repo = _new_repo(root, "malformed")
    steps: List[Tuple[str, str, Dict[str, str]]] = [
        ("duplicate trailer",
         CLAUDE + "\nCo-Authored-By: Claude (Anthropic) <noreply@anthropic.com>",
         {"f.txt": "1\n"}),
        ("no email", "Co-Authored-By: Claude", {"f.txt": "2\n"}),
        ("lowercase key", "co-authored-by: OpenAI Codex <noreply@openai.com>",
         {"f.txt": "3\n"}),
        ("empty value", "Co-Authored-By:", {"f.txt": "4\n"}),
        ("prose after trailer",
         CLAUDE + "\nbut see notes for details", {"f.txt": "5\n"}),
    ]
    for i, (subject, trailer, files) in enumerate(steps):
        _commit(repo, BASE_TS + i * 3600, f"{subject}\n\n{trailer}", files)
    return repo


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out") / "fixtures"
    for fn in (build_mixed, build_agent_heavy, build_malformed):
        print(f"built {fn.__name__} -> {fn(root)}")
