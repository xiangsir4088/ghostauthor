# AI Code Provenance Audit (ghostauthor)

A **skill for coding agents** that wraps the deterministic `ghostauthor` ledger
— an auditable answer to "which coding agent signed which commits, how many
lines, in which sessions" in any git repository.

## What it gives your agent

- A read-only, no-LLM, no-network way to audit AI code provenance in a repo.
- Correct interpretation of the buckets (`agent` / `cosigned` / `unsigned`),
  the `ai_ratio`, sessions and findings.
- A CI gate contract (exit codes `0` / `2` / `4`).
- Per-commit evidence, so any verdict can be defended.
- Optional write path (`sign`, `commit-msg` hook) that is opt-in and supervised.

## Install

```
pip install ghostauthor
```

The skill teaches the agent when and how to call the CLI or the MCP server
(`repo_authorship_report`, `commit_evidence`). It runs on the machine that
holds the repo being audited.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | The trigger + step-by-step method |
| `references/attribution-model.md` | Buckets, cap invariant, sessions |
| `references/usage-cheatsheet.md` | Full CLI option table + MCP tools |
| `references/attestation.md` | Detection vs. attestation boundary |
| `references/roster-guide.md` | Custom agent matching |

## Notes

- `unsigned ≠ human` — never accuse an unsigned commit of being AI-written.
- The JSON report is frozen at `schema_version "1"` and byte-stable.
- MIT licensed; upstream: https://atomgit.com/xiangsir/ghostauthor
