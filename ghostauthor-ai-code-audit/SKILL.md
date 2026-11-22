---
name: ai-code-provenance-audit
description: >-
  Use when the user asks to determine who authored or signed the commits in a
  git repository, audit the AI-generated code ratio, verify whether a commit,
  branch or PR was signed by a coding agent, or produce auditable AI
  provenance evidence for compliance or code review. Triggers on phrases like
  "who wrote this code", "how much of this is AI", "is this commit AI",
  "AI code ratio", "provenance audit", "who signed these commits",
  "audit AI authorship".
license: MIT
metadata:
  author: xiangsir
  tool: ghostauthor
  version: "1.3"
---

# AI Code Provenance Audit (ghostauthor)

A deterministic, auditable ledger that answers one question from git history:
**which coding agent signed which commits, how many lines, in which sessions.**
It reads the `Co-Authored-By:` git trailer block that agents already write — it
never guesses style, never uses an LLM, never touches the network, and on the
read side never writes anything.

## The one truth to state up front

`unsigned` is a category, not an accusation: **unsigned ≠ human**. It means
"no signature was found", nothing more. Never conclude a commit is human-written
just because it is unsigned, and never accuse an unsigned commit of being
AI-written — this tool refuses to answer the second question by design.

## Install (on the machine that holds the repo being audited)

Audit tools must run on the audited machine. `ghostauthor` is pure stdlib
Python — no venv ritual, no supply chain of its own:

```bash
pip install ghostauthor            # v1.3+
# or the always-latest checkout:
pip install git+https://atomgit.com/xiangsir/ghostauthor.git
```

Prefer the MCP server when the agent is already wired for MCP: it exposes the
exact same pipeline as read-only tools `repo_authorship_report` (full audit)
and `commit_evidence` (per-commit evidence). The MCP server never writes a
file; the `--bundle` artifact lives on the CLI only.

## Step 1 — Choose the scope

Ask (or default) what the user wants audited, then pick the scope:

| Want | Command |
|---|---|
| Whole history (HEAD) | `ghostauthor <path>` |
| One branch/tag/commit | `ghostauthor <path> --ref feature-x` |
| Two-branch diff (what a PR adds) | `ghostauthor <path> --base main --ref feature-x` |
| A quarter, not all history | `ghostauthor <path> --since 2026-07-01 --until 2026-10-01` |
| All refs | `ghostauthor <path> --all` |

Window semantics: `--since` is inclusive, `--until` is exclusive. An empty or
inverted window is a contract error (exit 2), never a silently empty report.

## Step 2 — Run and read the verdict

Default is human-readable text. For machines or CI use `--format json` (the
frozen contract) or `--format sarif` (SARIF 2.1.0 for compliance pipelines).

Read the report as three layers:

1. **Buckets** — `commits_by_bucket`: `agent` (at least one trailer matched the
   roster), `cosigned` (has trailers, none matched), `unsigned` (none). The
   `ai_ratio` is `agent / total` — the deduplicated number.
2. **Per agent** — each roster agent's commits, lines added/removed, session
   count and longest session. **Attribution may overlap** (one commit, several
   agents): per-agent sums can exceed the total. That is a documented fact, not
   a contradiction.
3. **Findings** — judgments that never change the verdict: `cosigned-unknown`
   (trailer present but no roster rule), `trailer-in-body` (a `Co-Authored-By`
   line in the prose body — ignored by design), `multi-agent-commit`,
   `trailer-no-email`.

## Step 3 — Verify before you conclude

- **Every number is traceable.** The JSON report carries the full commit list
  per bucket, per agent and per session (`bucket_commit_shas`). If you cannot
  expand a number into commits, do not state it.
- **Same repo state ⇒ same bytes.** JSON is byte-stable (`sort_keys`). If a
  report does not reproduce byte-for-byte on re-run, something changed — do not
  trust it.
- **Per-commit evidence.** To answer "why did the audit attribute THIS commit
  this way", run `ghostauthor <path> <sha> evidence` (or the MCP
  `commit_evidence` tool). It names the exact roster selector that matched each
  trailer. A verdict you cannot expand into evidence is a verdict you cannot
  defend.

## CI gate (the exit-code contract)

`--max-ai-ratio` turns the audit into a gate. The exit code IS the contract:

| Code | Meaning |
|---|---|
| `0` | report produced; gate (if any) not breached |
| `2` | environment/usage error: not a repo, no commits, bad roster, bad args |
| `4` | gate breached: agent-signed ratio **above** `--max-ai-ratio` |

Boundary: a ratio exactly equal to the limit is a **PASS** (`>` not `>=`).

## Custom roster (when the builtin does not fit)

The builtin roster knows 8 agents. For your own agents, or to fix a
misattribution, point at a roster file or directory:

```bash
ghostauthor <path> --roster team-roster.json
ghostauthor <path> --roster-dir rosters/          # merge *.json, sorted by name
```

Roster rules: `id`, `display_name`, `name_patterns`, `email_domains`. **File
order is the precedence — first match wins.** A roster is validated, never
silently repaired: a bad roster is rejected with a rule id (exit 2). See
`references/roster-guide.md`.

## Write path (opt-in) — only if the user explicitly asks

The read side is advice. The write side is a claim (attestation) and is
deliberately supervised: it is off by default, dry-runs unless `--commit` is
passed, signs one commit at a time, and refuses to merge another agent's
signature. Do not run it on your own initiative. See `references/attestation.md`.

```bash
ghostauthor sign <path> --agent claude          # dry-run: shows what would change
ghostauthor sign <path> --agent claude --commit # actually amends HEAD
```

## Guardrails (never cross these)

- Read side only — do not sign, do not amend, do not rewrite history unless the
  user explicitly asked and understood the write path.
- Only the **trailer block** counts. A `Co-Authored-By` line in the prose body
  is text, not metadata — report it, don't count it.
- Never call an unsigned commit "human". Never call a signed commit "AI
  written" — a signature is a claim, and the tool does not verify claims are
  true.
- Keep the audit read-only on the machine it runs on. Do not send repo contents
  anywhere.

## Reference

- `references/attribution-model.md` — buckets, cap invariant, sessions.
- `references/usage-cheatsheet.md` — full CLI option table and MCP tools.
- `references/attestation.md` — detection vs. attestation boundary.
- `references/roster-guide.md` — custom agent matching.
