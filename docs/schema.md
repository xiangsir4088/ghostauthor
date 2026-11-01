# JSON report contract — schema_version 1

**Single source of truth for the frozen read-side contract.** This document,
plus `schema/report.schema.json` (machine-checkable) plus the golden
snapshot (`tests/goldens/report.json`), pins exactly what
`ghostauthor --format json` emits.

The JSON is produced in one place — `ghostauthor/report.py:render_json` —
and the same bytes are what the MCP tool `repo_authorship_report` returns
and what the web transport serves and lets you download. One source of
truth, two skins: there is no second JSON shape to keep in sync.

## How the freeze is enforced

`python verify.py` step 18, on every run:

1. **Schema is valid** — `schema/report.schema.json` is checked against the
   frozen keyword subset implemented in `tests/schemacheck.py` (stdlib-only;
   the project has no `jsonschema` dependency, and "valid" here always means
   "validated with these rules").
2. **Real output validates** — the CLI runs on the `mixed` fixture and its
   stdout must pass the schema.
3. **Golden bytes** — a fixed in-memory dataset is re-rendered and compared
   byte-for-byte to `tests/goldens/report.json`. Indentation, key order and
   the trailing newline are part of the contract.
4. **The freeze bites** — a mutated report (removed key, added key) must be
   *rejected by name*. A freeze that cannot go red is a slogan.

Negative tests live in `tests/test_schema.py`; every pinned semantic has
one (project rule: no semantics without negative tests).

## Top-level keys (15, all required, no additions without a policy step)

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | string, currently `"1"` | contract generation (see policy below) |
| `ref` | string | what was scanned: `HEAD`, `all`, a ref, or `base..target` |
| `repo` | string | resolved absolute repository path |
| `total_commits` | integer ≥ 1 | deduplicated commit count (the no-commits state is an error, never a report) |
| `commits_by_bucket` | `{agent, cosigned, unsigned}`: integers ≥ 0 | deduplicated per-bucket counts |
| `lines_by_bucket` | same shape | added+removed lines per bucket |
| `bucket_commit_shas` | same shape, values are `^[0-9a-f]{40}$` lists | traceability: every count expands to its commit list |
| `ai_ratio` | number 0..1 | agent-bucket commits / total (deduplicated) |
| `agents` | array of agent objects | per-agent stats (may overlap across agents — the cap invariant) |
| `authors` | array of author objects | per-author × per-agent cross-tabulation |
| `sessions` | array of session objects | runs of agent commits within the gap threshold |
| `cosigned_evidence` | array of string | `sha: raw trailer line` — the evidence for `cosigned` |
| `findings` | array of finding objects | traceable judgments; never change the verdict |
| `gate` | object or null | null unless `--max-ai-ratio` was given: `{breached, limit, ratio}` |
| `window` | object or null | null unless a window was applied: `{since, until}` (each int or null) |

### Agent object

`commits` (≥1), `display_name`, `first_ts` / `last_ts` (int), `id`
(`^[a-z0-9][a-z0-9_-]*$` — the roster's id rule), `lines_added`,
`lines_removed` (≥0), `longest_session_commits` (≥1), `sessions` (≥1).

### Author object

`by_agent` (string → integer ≥ 1; keys are roster agent ids and/or the
bucket keys `cosigned` / `unsigned` — non-zero cells only),
`by_agent_shas` (string → sha lists), `commits` (≥1, deduplicated row
total), `name`, `email`.

### Session object

`agent` (roster id), `commit_shas` (non-empty), `start_ts`, `end_ts`.

### Finding object

`rule` (non-empty rule id), `severity` (`"info"` | `"warn"`), `evidence`,
`detail`.

## Compatibility policy

The contract changes in only two ways:

- **Additive** (a new key, a new allowed value in an open-ended position):
  a **minor** release. `schema_version` stays as-is; the schema file, this
  document and the golden snapshot are updated in the same change —
  verify step 18 enforces that the three cannot drift.
- **Breaking** (rename, delete, retype, semantic change): a **bump of
  `schema_version`** (and a major release). `schema_version` is a string so
  future values like `"1.1"` are representable without a type change.

SARIF output is **not** under this freeze — it is already pinned at the
OASIS 2.1.0 level and validated there. Text/markdown rendering is a
presentation layer: stable, but not contract.

## CLI surface (frozen with this schema)

`ghostauthor [PATH]` with:

| Flag | Default | Contract |
|---|---|---|
| `--all` | off | scan all refs; mutually exclusive with `--ref` and `--base` (`ref-conflict`) |
| `--ref REF` | HEAD | one ref; `-`-leading refs rejected (`ref-invalid`) |
| `--base REF` | off | git range `REF..target`; empty range is `no-commits`, never an empty report |
| `--format` | `text` | `text` / `json` / `markdown` / `sarif` |
| `--out FILE` | stdout | write the report to FILE |
| `--roster FILE` | builtin | custom roster; exclusive with `--roster-dir` (`roster-conflict`) |
| `--roster-dir DIR` | off | merge `*.json` in sorted filename order; cross-file duplicate ids rejected (`roster-dir-dup`) |
| `--since BOUND` | off | inclusive; epoch seconds, `YYYY-MM-DD` (UTC midnight) or ISO 8601 (naive = UTC) |
| `--until BOUND` | off | **exclusive**; same formats |
| `--session-gap MIN` | 360 | max minutes between agent commits in one session; must be positive |
| `--max-ai-ratio X` | off | 0..1; exit 4 if agent ratio **exceeds** X (`>` not `>=`) |
| `--version` | — | print version and exit 0 |

Boundary semantics are pinned by tests: exactly at the limit is a PASS;
a window includes `--since` and excludes `--until`; an inverted or empty
window is a contract error (exit 2), never a silently empty report.

### Exit codes (the CI contract)

| Code | Meaning |
|---|---|
| `0` | report produced; gate (if any) not breached |
| `2` | environment or usage error: not a repo, no commits, bad roster, bad args |
| `4` | gate breached: agent-signed ratio above `--max-ai-ratio` |

## MCP surface (frozen with this schema)

One tool, `repo_authorship_report`, stdio JSON-RPC 2.0
(`protocolVersion` `2025-03-26`):

- **inputs** (all optional): `path` (string, default `.`), `all_refs`
  (boolean), `session_gap_minutes` (integer, default 360),
  `max_ai_ratio` (number, 0..1)
- **output**: the tool's text content is exactly `render_json(report)` —
  the bytes validated by this schema
- **errors**: `isError: true` with `error: <message>`; the server never
  executes anything beyond the read-only `git log`

## Invariants the schema depends on

1. **Provenance, not style** — only the trailer block is read;
   `unsigned ≠ human`.
2. **Every number traceable** — every count has its sha list.
3. **The cap** — per-agent attribution may overlap; deduplicated totals
   never do.
4. **Same repo state ⇒ same report** — ordered by `(authored_at, sha)`,
   `sort_keys` JSON, byte-stable.
5. **Boundary semantics pinned by tests.**

## The second frozen contract: the evidence JSON (v1.3)

`ghostauthor evidence --format json` (and the `--bundle` `evidence.json`)
is pinned by `schema/evidence.schema.json` with the same discipline:
`schema_version` `"1"`, five root keys (`schema_version`,
`roster_source`, `agents_in_roster`, `commits`, `evidences`),
`additionalProperties: false`, the same additive/bump compatibility
policy (additive ⇒ minor, bump `schema_version` ⇒ breaking).

Enforced by `verify.py` step 22, on every run: the schema is valid
within the frozen keyword subset; the real CLI output AND the bundle's
`evidence.json` validate; the bundle's file equals the CLI's bytes; and
mutations (removed key, added key, bogus `attribution` value) are
**rejected by name** — a freeze that cannot go red is a slogan.

The evidence document is emitted by the same attribution code as the
report (`engine.attribute_commit`) — the two frozen contracts therefore
cannot disagree about a commit. There is deliberately **no golden
snapshot** for the evidence JSON: the payload carries the roster source
path as the user gave it, and a golden would pin the auditor's
filesystem layout.
