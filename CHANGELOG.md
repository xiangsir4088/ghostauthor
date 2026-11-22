# Changelog

All notable changes to ghostauthor are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions use
[Semantic Versioning](https://semver.org/).

The read-side JSON contract is frozen (`schema_version` 1, see
[docs/schema.md](docs/schema.md)): additive changes never bump the version;
a rename, deletion or semantic change bumps it. The write path is opt-in and
additive in every release below — no repository is ever signed by default.

## [1.5.0] — 2026-11-22

**Product surface**: the ledger stops being a command you have to interpret
and becomes three things a team can actually use — a diagnosis for the empty
ledger, a deliverable for someone who will not run the CLI, and an aggregate
for an organisation rather than a repository.

All three are read-side additions. **No schema bump**: `report.schema.json`
and `evidence.schema.json` are untouched, and the new outputs carry their own
`schema_version: "1"`.

### Added
- **`ghostauthor coverage`**: adoption diagnosis. It exists because a
  provenance ledger has a cold-start problem — if the agents never wrote a
  trailer, a 0.0% report is *correct* and *useless*, and the operator
  concludes the tool is broken. `coverage` separates the two causes and gives
  each a different fix:
  - a trailer the roster does not recognize (`Co-Authored-By: Cursor <…>`) →
    a **data** fix, with the exact trailer and commit count;
  - an unsigned commit carrying a known tool's literal marker
    (`Generated with [Claude Code]`) → a **write-path** fix, with the
    suggested `install-hook` command.

  Markers are **hints, never attributions**: `signed_commits` is unchanged by
  them, and a commit with no marker produces no claim at all. Adoption bands
  (`none` / `minimal` / `partial` / `adopted`, boundaries 0.2 and 0.8,
  lower-bound inclusive) are pinned by tests. Also reports coverage by author
  and by ISO week (UTC), and the first/last signed commit.
- **`--format html`** (+ `--title`): a self-contained audit brief for a
  board, a client or an archive. No `<script>`, no remote asset, and **no
  generation timestamp** — it is a pure function of the report, so the same
  repository state regenerates the same bytes, which is what lets it sit next
  to the JSON as evidence rather than as a screenshot. Every interpolated
  value is HTML-escaped; the method statement ("no LLM, no network, no style
  classifier") travels with the document.
- **`ghostauthor portfolio`**: the same ledger across repositories, from a
  directory walk (`--max-depth`) or a `--repos-file` list. The aggregate is
  **weighted by commit** (`sum(signed) / sum(commits)`), never a mean of
  per-repository ratios — the unweighted figure is reported next to it so the
  two can never be confused. A repository that cannot be scanned is recorded
  in `errors` and excluded from the totals instead of aborting the run; every
  repository failing is `portfolio-empty` (exit 2), not an empty success.
  Per-repository agent detail expands the cross-repository rollup.
- **Web `/api/coverage`**: the same diagnosis bytes over HTTP, plus an in-place prompt in the dashboard when a report's ratio is below 20% — the moment a first-time user is most likely to conclude the tool is broken. Clicking it runs the diagnosis and renders the actions inline; the hint is absent above 20%, so a healthy ledger is never nagged.
- **`schema/coverage.schema.json`** and **`schema/portfolio.schema.json`**: both diagnostic outputs are frozen on the same terms as the report (`schema_version` `1`, `additionalProperties: false`, additive ⇒ no bump). Frozen separately from the report on purpose: they explain adoption, they never re-attribute a commit, and a change in how adoption is *explained* must not look like a change in what attribution *means*.
- **`docs/commercial.md`**: the business design — what is actually sold
  (a number that survives cross-examination, not the CLI), the three buyer
  segments and their triggers, the free/paid boundary and its two rules,
  pricing shape and rationale, why open-core before SaaS, distribution,
  moat, risks, a four-quarter plan and a deliberately unglamorous revenue
  expectation.
- **README**: a "Start here if the ledger is empty" section (the cold-start
  problem, with the fix table), the new commands in the 60-second start, and
  a "Free and commercial" boundary table.
- **verify steps 25–27**: coverage (bands, signature-vs-silence, author
  reconciliation, window semantics, byte stability), portfolio (weighted
  aggregate, partial-failure recording, agent rollup, self-contained
  byte-stable HTML), HTML brief (byte stability, no script / remote /
  timestamp, measured numbers present).

199 → 236 tests / verify 27 steps green.



**Surface parity**: the third skin catches up with the read side. The
ledger was already byte-identical across CLI / MCP / Web for the audit;
it now is for per-commit evidence too, and the MCP audit tool accepts the
full CLI option set instead of a four-parameter subset.

### Added
- **MCP `commit_evidence`**: per-commit evidence over stdio, byte-identical
  to `ghostauthor evidence --format json`. It deliberately has **no**
  `bundle` parameter — a bundle writes files, and the server never writes
  files. Two tools, both pure reads over the same pipeline.
- **MCP audit parity**: `repo_authorship_report` grows from 4 to 10
  parameters (`ref`, `base`, `roster`, `roster_dir`, `since`, `until`,
  `session_gap_minutes`, `max_ai_ratio` in addition to the original four),
  each mapped to the CLI's behaviour and error code (`ref-conflict`,
  `ref-invalid`, `roster-conflict`, `window-bound-invalid`,
  `session-gap-invalid`, `gate-limit-invalid`).
- **Web `/api/evidence`**: the same evidence JSON, so the browser can
  drill from a bucket into the exact trailers and roster rule that produced
  it. The UI expands a row in place and carries a sha query box.
- **verify steps 23–24**: MCP surface parity (2 tools, byte equality for
  audit *and* evidence, every error code) and Web evidence parity (byte
  equality, 400/404/422 mapping, UI markers).

### Fixed
- **`install-hook` broke on any machine with a non-empty `PYTHONPATH`**.
  The generated hook appended the caller's `PYTHONPATH` with a POSIX `:`,
  but a Windows Python splits `sys.path` on `;` — so the whole string became
  one invalid path and the hook died with `ModuleNotFoundError:
  No module named 'ghostauthor'` *after* a successful self-test (the
  self-test ran with a clean env, so it could not catch this). The hook now
  **replaces** `PYTHONPATH` with the package parent (ghostauthor has zero
  runtime dependencies, so nothing is lost), and all paths are quoted with
  `shlex.quote` instead of hand-rolled quotes. Regression pinned in
  `tests/test_hook.py`.

169 → 199 tests / verify 24 steps green.

## [1.3.0] — 2026-11-03

Installable release surface. The tool now publishes to PyPI and ships the
documentation a team needs to contribute a roster.

### Added
- **PyPI**: `pip install ghostauthor` is the primary install path (zero
  runtime dependencies). `pyproject.toml` carries the PyPI metadata and a
  `pip install git+https://…` fallback stays documented.
- **`.github/workflows/publish.yaml`**: OIDC trusted publishing — a semver
  tag triggers the PyPI release with **no token in the repository**. The
  trigger matches only `v[0-9]+.[0-9]+.[0-9]+*`, so the floating `v1` tag
  cannot accidentally publish.
- **Evidence JSON contract frozen**: `schema/evidence.schema.json`
  (`schema_version` 1) pins the `ghostauthor evidence --format json` shape
  (5 root keys); verify step 22 enforces the freeze — a removed key, an added
  key or a bogus attribution each go red. The additive/bump policy is the
  same as the report schema.
- **`docs/roster.md`**: the roster writing guide — matching semantics
  (first-match-wins, prefix matches, explicit anchors), every validation rule
  id, lint rules and best practice. The roster is the most valuable
  contribution to this project; this is its entry point.
- **`README.zh.md`**: full Chinese translation of the README (the English
  version remains canonical).
- **verify step 22**: evidence freeze (schema valid / real output validates /
  mutations rejected by name).

### Fixed
- **`pyproject.toml` TOML section boundaries**: the `[project.urls]` table was
  first written as a multi-line inline table (rejected by older TOML parsers
  on Python 3.9 — `pytest` could not even start) and then mis-placed before
  `classifiers`, so the section header swallowed them (setuptools:
  `project.urls.classifiers must be string`, `python -m build` failed). Both
  fixed: `[project.urls]` is now a standard table placed after `classifiers`.
- Clean-venv packaging smoke: `python -m build` → sdist + wheel →
  `pip install` in a bare venv → `ghostauthor --version` + a fixture audit all
  pass.

169 tests / verify 22 steps green.

## [1.2.0] — 2026-11-01

Per-commit evidence: the audit answers *how many*; evidence answers *why this
commit was attributed the way it was*.

### Added
- **`ghostauthor evidence`**: per-commit evidence — for each trailer, the
  exact roster selector that matched (`name pattern '…'` or `email domain
  '…'`), the attributed agent / cosigned / unsigned conclusion, and the raw
  line. Single sha, multiple shas (input order preserved), or a full-scope
  scan (window semantics identical to the audit). `text` and `json` are both
  byte-stable.
- **`engine.attribute_commit`**: the single source of truth for per-commit
  attribution, extracted so the audit and the evidence command consume the
  *same* function — they are structurally unable to disagree about a commit.
- **`roster.match_detail`**: the match projection that carries the *reason*
  (which selector hit), differential-tested against `match`.
- **`gitparse.get_commit`**: single-commit extraction with the identical
  framing as `iter_commits`, so a per-sha read equals a scan read. An unknown
  sha is `sha-unknown` (exit 2).
- **`ghostauthor evidence --bundle DIR`**: a reproducible evidence package —
  `report.json` (the audit, frozen schema, byte-identical) + `evidence.json`
  + a per-file `roster/` snapshot + `manifest.txt` with sha256s. No wall-clock
  timestamps and canonical UTF-8/LF, so re-running on the same state and path
  yields identical bytes. Writing into an existing directory is refused
  (`bundle-exists`, exit 2).
- **verify step 21**: per-commit audit agreement / byte stability /
  reproducible bundle / refuse-overwrite.

169 tests / verify 21 steps green.

## [1.1.0] — 2026-11-01

Commit-time signing: the write path now signs at the moment a commit is born,
removing the amend-and-rewrite-sha workflow.

### Added
- **`ghostauthor install-hook` / `uninstall-hook`**: a repo-level
  `commit-msg` hook — the trailer is added while the commit message is still
  editable, so there is **no amend and no sha rewrite**. The hook is installed
  only on explicit request, pins its own interpreter / `PYTHONPATH` / roster,
  self-tests once before installing (`hook-selftest-failed` ⇒ not installed),
  and is marker-protected: a foreign `commit-msg` is never overwritten
  (`hook-exists`) and `uninstall-hook` only removes a hook it installed
  (`hook-not-ours`). `--no-verify` is the documented escape hatch; a bypassed
  commit still shows as unsigned in the audit.
- **`_commit-msg-hook`** internal subcommand: shares the `resolve_agent` +
  `plan_sign` pure-function core with `sign`, so round-trip / idempotency /
  conflict-refusal behave identically in both entry points. A conflict is a
  non-zero commit-msg exit — the commit is **blocked**, not landed.
- **`ghostauthor lint-roster`**: static, advisory-only checks —
  `lint-catch-all` (a catch-all `name_patterns` entry) and `lint-overlap` (a
  signing identity that roster precedence would attribute to another agent).
  Always exit 0; roster validation (exit 2) is unchanged.
- **Builtin roster**: the 7 agents with a canonical identity now carry a
  `signing` section, so `ghostauthor sign --agent claude` works out of the
  box (the `gpt` agent has no canonical identity and is intentionally not
  forced into one).
- **verify step 20**: hook install / real-commit round-trip / idempotent
  presign / conflict blocks the commit / `--no-verify` / uninstall idempotent /
  foreign-hook protection + lint machine checks.

159 tests / verify 20 steps green.

## [1.0.0] — 2026-10-29

**A category change**: the tool now has a write path.

### Added
- **`ghostauthor sign`**: opt-in trailer signing. No hook and no default flow
  — the subcommand exists only when explicitly invoked. Dry-run is the default
  (it prints the amended message and writes nothing); `--commit` amends HEAD.
  Signing is idempotent (a re-sign is a no-op); a commit already signed by
  *another* roster agent is refused (`sign-conflict`); an identity that roster
  precedence would misattribute is refused (`sign-ambiguous`). Every error
  path exits 2 and changes nothing.
- **Roster `signing` section** (`{"name", "email"}`) with the
  `roster-signing-*` validations. The load-bearing constraint is
  `roster-signing-roundtrip`: the signing identity must match *this agent's
  own* rules, so a signed commit is always attributed back to that agent by
  the read side.
- **`docs/attestation.md`**: the detection-vs-proof boundary, the honest abuse
  surface, and where the tool's defenses end and human judgment begins.
- **README invariants 6–10** (round-trip, idempotency, opt-in & consent,
  no-merge on conflict, single source of truth).
- **verify step 19**: round-trip / idempotency / conflict / default-off and
  the error paths, machine-checked with a controlled git identity (CI has no
  global one).

129 tests / verify 19 steps green.

## [0.5.0] — 2026-10-29

Read-side contract freeze: the JSON the tool emits is now a documented,
machine-checkable contract.

### Added
- **`schema_version: "1"`** in the JSON output (a string, leaving room for
  `"1.1"`); the MCP and Web transports emit the identical bytes.
- **`schema/report.schema.json`**: the frozen top-level structure (15 keys),
  the machine-checkable form of the contract.
- **`docs/schema.md`**: the single source of truth — the compatibility policy
  (additive ⇒ no bump; rename/delete/semantic ⇒ bump) and the CLI / MCP /
  exit-code contract list.
- **`tests/schemacheck.py`**: a stdlib-only JSON-Schema-subset validator
  (no new dependency).
- **verify step 18**: schema meta is valid / the real CLI output validates /
  the golden snapshot is byte-stable / the freeze is enforced (a removed or
  added key goes red by name).

100 tests / verify 18 steps green.

## [0.4.0] — 2026-10-22

CI integration: ghostauthor as a composite GitHub Action.

### Added
- **`action.yml`**: a composite GitHub Action with 14 inputs mirroring the CLI
  (path / ref / base / all / roster / roster-dir / since / until /
  session-gap / max-ai-ratio / format / out / fail-on-breach /
  python-version), 3 outputs (`report` / `breached` / `exit-code`), and the
  exit-4 gate passed through (`fail-on-breach: false` records the breach
  without failing the job; exit 2 always fails).
- **`.github/workflows/ci.yml`**: self-audit on this repository — a
  Linux/Windows × 3.9/3.11/3.13 matrix running the unit tests + `verify.py`,
  plus a **local CLI** audit (not the published action, so a broken change
  cannot pass by falling back to the last release): a JSON ledger of HEAD as
  an artifact, and a `--base origin/<base>` range audit on PRs.
- **`.github/workflows/pr-audit.example.yml`**: a copy-paste PR-comment
  template (audits `origin/<base>..HEAD`, posts the markdown report; the gate
  is off by default).
- **`docs/ci.md`**: the exit-code contract, the inputs/outputs tables, the PR
  comment mode and the SARIF notes.
- **verify step 17**: CI asset consistency (every input referenced, outputs
  present, exit-4 passthrough, docs cover the same contract).

88 tests / verify 17 steps green.

## [0.3.2] — 2026-10-18

Encoding hardening: reports stay readable on machines whose locale is not
UTF-8.

### Fixed
- **UTF-8 everywhere**: `stdout`/`stderr` are forced to UTF-8 (with
  `errors="replace"`) and the git subprocess pins
  `i18n.logOutputEncoding=UTF-8` and `core.quotepath=false`, so a Chinese
  Windows GBK locale can no longer mangle report text or non-ASCII paths.
- **Web UI light theme**.

88 tests / verify 16 steps green.

## [0.3.1] — 2026-10-17

Two-branch diff and the per-author × per-agent cross-tabulation.

### Added
- **`--base`**: a git `base..target` range — audit only what `target`
  (HEAD or `--ref`) adds over `base`. Mutually exclusive with `--all`
  (`ref-conflict`); an empty range is `no-commits`; a `-`-leading base is
  `ref-invalid`.
- **`Report.authors`**: the per-author × per-agent cross-tabulation. A
  multi-agent commit lands in every matched column (the cap invariant); every
  cell carries its commit list.
- The web UI compares two refs of the same repository side by side.

86 tests / verify 16 steps green.

## [0.3.0] — 2026-10-17

A local, read-only dashboard over the same ledger.

### Added
- **`ghostauthor-web`**: a stdlib-only `http.server` dashboard serving one
  self-contained, CDN-free page. 1–2 repositories per request, each
  optionally scoped to a ref; the JSON it shows and downloads is
  **byte-identical to the CLI's** `--format json`. Error codes are the
  exit-code contract translated to HTTP (request-level 400 / not-a-repo 404 /
  no-commits 422; a gate breach is a 200 — a conclusion, not an error).
- **`--ref`** (additive): audit one branch/tag/commit instead of HEAD
  (`iter_commits(ref=)`); mutually exclusive with `--all`; a `-`-leading ref
  is `ref-invalid`.
- Nonexistent paths are classified `not-a-repo`, not `git-failed`.

75 tests / verify 14 steps green.

## [0.2.0] — 2026-10-16

Time windows, multi-file rosters, and SARIF output.

### Added
- **`--since` / `--until`**: audit a window of history. `since` is inclusive,
  `until` is exclusive; epoch, `YYYY-MM-DD` or ISO 8601 (naive = UTC).
- **`--roster-dir`**: merge every `*.json` roster in a directory (sorted
  filename order); a duplicate id across files is rejected (`roster-dir-dup`).
- **SARIF 2.1.0** (`--format sarif`) for compliance pipelines.

52 tests / verify 13 steps green.

## [0.1.0] — 2026-10-16

First release.

### Added
- **CLI**: deterministic AI code provenance ledger — reads the
  `Co-Authored-By:` git trailers and reports which agent signed which
  commits, line counts, sessions and traceable findings. Text / JSON /
  markdown / SARIF output; `0/2/4` exit-code contract.
- **Read-only MCP server** (`repo_authorship_report`, stdio, zero
  dependencies): it reports; it never executes.
- **Builtin roster** (`roster/agents.json`) + the roster validator
  (validate, never repair).
- **3 deterministic fixtures** (`examples/make_fixtures.py`) and
  **`verify.py`** (10 steps).

38 tests / verify 10 steps green.

### Fixed
- **Unborn-HEAD repositories** (everything staged, nothing committed) are
  classified `no-commits`, not a git failure.

[1.4.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.4.0
[1.3.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.3.0
[1.2.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.2.0
[1.1.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.1.0
[1.0.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.0.0
[0.5.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.5.0
[0.4.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.4.0
[0.3.2]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.3.2
[0.3.1]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.3.1
[0.3.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.3.0
[0.2.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.2.0
[0.1.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v0.1.0
