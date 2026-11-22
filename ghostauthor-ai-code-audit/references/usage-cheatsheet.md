# Usage cheatsheet

## CLI

```
ghostauthor [PATH] [options]
```

| Flag | Default | Contract |
|---|---|---|
| `--all` | off | scan all refs; exclusive with `--ref` and `--base` (`ref-conflict`) |
| `--ref REF` | HEAD | one ref; `-`-leading refs rejected (`ref-invalid`) |
| `--base REF` | off | git range `REF..target`; empty range is `no-commits`, never an empty report |
| `--format` | text | `text` / `json` / `markdown` / `sarif` |
| `--out FILE` | stdout | write the report to FILE |
| `--roster FILE` | builtin | custom roster; exclusive with `--roster-dir` (`roster-conflict`) |
| `--roster-dir DIR` | off | merge `*.json` in sorted filename order; cross-file duplicate ids rejected |
| `--since BOUND` | off | inclusive; epoch seconds, `YYYY-MM-DD` (UTC midnight) or ISO 8601 (naive = UTC) |
| `--until BOUND` | off | **exclusive**; same formats |
| `--session-gap MIN` | 360 | max minutes between an agent's commits in one session |
| `--max-ai-ratio X` | off | 0..1; exit 4 if agent ratio **exceeds** X (`>` not `>=`) |

Subcommands:

```
ghostauthor sign <path> --agent claude [--commit]      # opt-in write path
ghostauthor evidence <path> [sha...] [--format json] [--bundle DIR]
ghostauthor install-hook <path> --agent claude          # commit-time signing (opt-in)
ghostauthor uninstall-hook <path>
ghostauthor lint-roster [--roster FILE]
```

## Exit codes (the CI contract)

| Code | Meaning |
|---|---|
| `0` | report produced; gate (if any) not breached |
| `2` | environment/usage error: not a repo, no commits, bad roster, bad args |
| `4` | gate breached: agent-signed ratio above `--max-ai-ratio` |

## MCP server (read-only, stdio)

- `repo_authorship_report` — the full audit; output is byte-identical to the
  CLI's `--format json`. Inputs: `path`, `all_refs`, `ref`, `base`, `since`,
  `until`, `session_gap_minutes`, `max_ai_ratio`, `roster`, `roster_dir`.
- `commit_evidence` — per-commit evidence; byte-identical to
  `evidence --format json`. Inputs add `shas` (input order preserved). No
  `bundle` — the server never writes files.

Both return the same codes as the CLI; repo-level errors (`not-a-repo`,
`no-commits`, `sha-unknown`) surface as `isError: true`.

## Web UI (read-only, stdlib)

```
ghostauthor-web            # → http://127.0.0.1:8321
ghostauthor-web --port 9000
```

Binds `127.0.0.1` by default. One self-contained page, no build step, no CDN.
Endpoints: `POST /api/audit` (1–2 repos side by side) and `POST /api/evidence`
(single repo, per-commit drill-down). Errors: 400 request-level, 404
`not-a-repo`, 422 `no-commits` / `sha-unknown`. A breached gate is still 200 —
a breach is a verdict, not a failure.
