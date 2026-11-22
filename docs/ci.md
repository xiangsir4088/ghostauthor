# CI Integration / CI 集成

ghostauthor's exit codes are the CI contract:

| exit | meaning |
|---|---|
| `0` | report produced (gate, if any, not breached) |
| `2` | environment or usage error (not a repo, no commits, bad roster, bad args) |
| `4` | gate breached — agent-signed ratio above `max-ai-ratio` |

Anything that runs a shell can integrate; below are the two supported paths.

## GitHub Actions: the composite action

`.github/workflows/audit.yml` in **your repository**:

```yaml
name: audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # full history — the ledger reads commits
      - uses: xiangsir/ghostauthor@v1
        with:
          max-ai-ratio: "0.5"   # exit 4 (job fails) when agent-signed > 50%
          format: "json"
          out: "audit.json"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ghostauthor-audit
          path: audit.json
```

### Inputs

| input | default | notes |
|---|---|---|
| `path` | `.` | repository path to audit |
| `ref` | — | audit one ref instead of HEAD |
| `base` | — | PR mode: only commits in `REF..target` (git range diff) |
| `all` | `false` | scan all refs |
| `roster` / `roster-dir` | builtin | custom agent roster |
| `since` / `until` | — | time window (`since` inclusive, `until` exclusive) |
| `session-gap` | `360` | minutes; max gap between agent commits in one session |
| `max-ai-ratio` | off | gate threshold, `0..1` |
| `format` | `text` | `text` / `json` / `markdown` / `sarif` / `html` |
| `out` | stdout | write report to file |
| `fail-on-breach` | `true` | `false` records `breached=true` without failing the job |
| `python-version` | `3.11` | interpreter used to run the tool |

### Outputs

| output | meaning |
|---|---|
| `report` | full report text in the chosen format |
| `breached` | `"true"` when the gate was breached (exit 4) |
| `exit-code` | raw exit code `0`/`2`/`4` |

Exit `2` always fails the step — an unusable audit is never a pass.

## PR comment mode

`.github/workflows/pr-audit.example.yml` in this repository is a complete,
copy-paste template: it audits `origin/<base>..HEAD` (only what the PR adds),
posts the markdown report as a PR comment, and leaves the gate off by default
so the comment is an audit artifact rather than a hard block. Uncomment
`max-ai-ratio` there when you want to enforce.

It needs `permissions: pull-requests: write` (included in the template).

## Self-hosting (this repository)

`.github/workflows/ci.yml` is what runs on ghostauthor itself:

1. **test** — unit tests + `python verify.py` on a Linux/Windows × 3.9/3.11/3.13 matrix.
2. **self-audit** — dogfoods the local CLI on this very repository (not the
   published action, so a broken change cannot pass by falling back to the
   last release): a JSON ledger of HEAD as a CI artifact, plus a PR-range
   audit when triggered by a pull request.

## SARIF pipelines

`--format sarif` emits SARIF 2.1.0 for compliance pipelines that consume it
(`gh api .../code-scanning/sarifs`, third-party ingestors). The report is a
ledger, not a findings list: the SARIF result represents the audit conclusion,
per-agent counts included in properties.

## Versioning

The composite action is referenced by tag. Two kinds exist:

- **Semver tags** (`v1.3.0`, `v1.4.0`, …) are the release units. Only these
  can trigger a PyPI publish — the `publish` workflow matches
  `v[0-9]+.[0-9]+.[0-9]+*` and nothing else.
- **Floating aliases** are convenience refs that track a major line. Pinning
  one means "latest stable in this major series": you pick up fixes
  automatically, at the cost of a moving target. Prefer a semver tag when a
  pinned, reproducible build matters; use an alias when you want the latest
  patch.

Declared floating aliases: `v1` (= latest 1.x stable).

`verify` step 17 enforces this: every `uses: xiangsir/ghostauthor@<tag>` in
the shipped workflow examples and in `action.yml` must name either an existing
git tag or a declared floating alias above. A reference to a tag that does not
exist — or to an alias that is not declared here — goes red. This is the check
that catches a template pointing at a ref that was never created.

## Invariants (unchanged in CI)

- Exit `0`/`2`/`4` only; the gate is `>` not `>=`.
- JSON / SARIF output is byte-stable (`sort_keys`) — same repo, same refs,
  same roster ⇒ identical bytes, so CI artifacts are diffable.
- Roster is validated, never repaired; missing ≡ empty, never an exception.
