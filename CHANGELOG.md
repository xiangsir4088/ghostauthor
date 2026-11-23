# Changelog

All notable changes to ghostauthor are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions use
[Semantic Versioning](https://semver.org/).

The read-side JSON contract is frozen (`schema_version` 1, see
[docs/schema.md](docs/schema.md)): additive changes never bump the version;
a rename, deletion or semantic change bumps it. The write path is opt-in and
additive in every release below — no repository is ever signed by default.

## [1.6.1] — 2026-11-23

**A wrong type is a request error, never a guess.** v1.6 gave the three
surfaces one definition of each boundary; this release makes that
definition judge the *type* of the value as well as its range. Coercion
was hiding three failures, and one of them returned a confident answer to
a question nobody asked.

### Fixed
- **`"all": "false"` no longer means "scan every ref".** Python's
  `bool("false")` is `True`, so a JSON client that sent a stringified
  boolean silently got the widest possible scope. It is now rejected with
  `scope-invalid` on the CLI's shared option layer, the MCP server and the
  web transport alike. The same rule applies to `all_refs`.
- **A non-string `ref` / `base` is a 400, not a 500.** `{"ref": 123}`
  reached `str.startswith` and escaped as an unhandled `AttributeError`
  (`internal-error` over HTTP). It is now `ref-invalid` with the actual
  type in the detail.
- **`{"roster_dir": []}` no longer falls back to the builtin roster.**
  The empty list is falsy, so the old truthiness test silently answered
  from a roster the caller did not name. Non-path values are now
  `roster-invalid`.
- **A boolean is not a minute count or a gate limit.** `session_gap_minutes:
  true` used to become 1 minute and `max_ai_ratio: true` a gate of 1.0 (an
  "on" gate that silently did nothing); both are now rejected.
- **`coverage` no longer tells first-time users to run a command that
  fails.** The Claude Code marker produced `install-hook --agent
  claude-code`, while the roster only knows `claude`, so the first
  suggested step exited 2 with `sign-agent-unknown`. Marker ids are now
  mapped to roster ids, and a tool with no signing identity in the roster
  gets "add a rule first" instead of a dead command.
- **The web transport validates in the CLI's order** (roster → scope →
  window → gap → gate), so a request with two faults names the same fault
  on every surface.
- **A refused body no longer poisons a keep-alive connection.** The 413
  path read neither the body nor closed the socket, leaving those bytes to
  be parsed as the next request line. It now answers with `Connection:
  close`. A negative `Content-Length` is rejected instead of reaching
  `read(-1)` and parking a worker until the socket timeout.

### Added
- **MCP `adoption_diagnosis`** — the third read-only tool, byte-identical
  to `coverage --format json`. An agent that sees a 0% ratio can now ask
  *why* without shelling out; the diagnosis it returns is the one thing
  the tool exists to make.
- **`portfolio --max-ai-ratio`** — an organisation-level gate on the
  weighted aggregate. The boundary is the report's own (`ratio > limit`
  breaches, equality passes); the report is produced first, then exit 4.
  `portfolio.gate` is an additive key (`null` when no limit was given):
  `schema_version` stays `"1"`.
- **`python -m ghostauthor`** (`ghostauthor/__main__.py`) — the module
  entry point now agrees with the console script.

### Release
- **`publish.yaml` can publish again.** It referenced
  `pypi-publish/pypi-publish@v1`, a repository that does not exist, and
  declared no `id-token: write`, so OIDC could not be signed. It now uses
  `pypa/gh-action-pypi-publish@release/v1`, declares the permission, and
  fails fast when the pushed tag disagrees with `pyproject.toml` (PyPI
  never lets a version number be reused).
- The comment that told maintainers to enter workflow `publish` on PyPI
  was wrong: the field takes the file name, `publish.yaml`.

**No schema bump** for the report/evidence/coverage contracts; the
portfolio schema gains one nullable key.

296 → 312 tests / verify 28 steps green.

## [1.6.0] — 2026-11-23

**One definition of a boundary.** The CLI, the MCP server and the web
transport each parsed the same request-shaped options — scope, time
window, roster, session gap, gate — and each did it in its own copy of
the code. The copies had already drifted, and the drift was not
cosmetic: two of the differences produced **wrong answers that looked
right**.

This release moves those semantics into one module (`ghostauthor/
options.py`) and makes all three surfaces call it — the same lesson the
project already applied to the window-bound parser (`models.py`) and the
evidence selection (`evidence.select_evidence_commits`).

**No schema bump.** `report.schema.json`, `evidence.schema.json`,
`coverage.schema.json` and `portfolio.schema.json` are untouched; every
read-side contract keeps `schema_version` `"1"`.

### Added
- **`ghostauthor/options.py`** — the single definition of scope, window,
  roster, session gap and gate. `Scope` carries the one spelling of
  "what was scanned" (`base..target`, `all`, `HEAD`) that used to be
  written out four separate times; `resolve_window`,
  `resolve_roster`, `resolve_session_gap` and `resolve_gate` raise one
  `OptionError` shape (`"<code>: <detail>"`) that every surface already
  knew how to print.
- **`/api/audit` accepts `base`** — the two-branch diff the other two
  web endpoints already supported.
- **The web transport accepts `all` *and* `all_refs` everywhere**, and
  so does the MCP server. The two spellings are synonyms, not a trap.
- **`tests/test_options.py`** — 37 tests pinning the boundaries and the
  surface-parity property (one request in, the same code and the same
  bytes out of all three surfaces).
- **verify step 28** — the option-parity matrix: the same bad request
  must produce the same code on the CLI, the MCP server and the web
  transport, and the web must serve the CLI's exact bytes for the same
  scope, including a two-branch diff.
- **verify step 17, hardened** — it now pins the **owner** in every
  `uses:` line to the repository this project publishes to, and it
  requires a declared floating alias to be a **real tag**. Declaring
  `v1` in the docs while no `v1` tag existed was the half-fix that
  shipped the broken template.

### Fixed
- **`/api/audit` silently ignored `base`** — asking for a two-branch
  diff returned the whole history. For an audit tool this is the worst
  failure mode there is: a wrong answer that looks right.
- **`/api/audit` crashed on a malformed entry** — a well-sized list of
  non-objects raised `AttributeError` out of the HTTP layer. With
  HTTP/1.1 keep-alive the client saw a dropped connection instead of a
  `400`. A malformed entry is a malformed *request* and is now rejected
  as one (`bad-entries`).
- **The web audit endpoint lost the CLI's error codes** — every option
  error collapsed into a generic `value-error` with the real code
  hidden in the detail text. All three endpoints now report the CLI's
  own code (`window-empty`, `ref-invalid`, `roster-error`, …).
- **The scope flag was spelled two ways** — `all_refs` in `/api/audit`,
  `all` in `/api/evidence` and `/api/coverage`. A client that guessed
  wrong asked for all refs and silently got HEAD.
- **The CLI reported `--all --ref` in argparse's words** (no code),
  while the MCP server and the web transport reported `ref-conflict` —
  the code `docs/schema.md` documents. The CLI now reports the same
  code; the argparse mutually-exclusive group was removed so all three
  surfaces answer identically.
- **An unexpected exception in an HTTP handler escaped to the server
  loop** — the client got no response at all instead of a `500`. The
  handler now has a last line of defence, and `OSError` (an unreadable
  path) is mapped to `500` rather than dropped.
- **An unbounded `Content-Length` was read into memory** before the
  request was validated. Bodies above 32 MiB are refused with `413`.
- **The shipped consumer template pointed at a repository that does not
  exist**: `uses: xiangsir/ghostauthor@v1` resolved to a GitHub
  repository that is not there (the project publishes as
  `xiangsir4088/ghostauthor`). Copying the documented template failed
  with "Unable to resolve action" — for two independent reasons: the
  wrong owner *and* a ref that was never created.
- **CHANGELOG.md was missing its `## [1.4.0]` section header** — v1.4.0's
  content rendered as part of v1.5.0, and the `[1.4.0]` link
  definition at the bottom of the file was orphaned.
- **The packaged skill documented a command that does not run** —
  `ghostauthor <path> <sha> evidence` is rejected by argparse (exit 2);
  the form is `ghostauthor evidence <path> <sha>`. The skill's version
  metadata was also stale, and it said nothing about `coverage`,
  `portfolio` or the HTML brief.

### Fixed
- **The web did NOT serve the CLI's bytes.** `/api/audit` parsed the
  report back into a dict and re-serialized it, so the served document
  was compacted and lost the canonical key order — the "byte-identical to
  the CLI" claim was false for one of the three read surfaces (the MCP
  tool and `/api/evidence` already returned the exact byte string). The
  endpoint now serves the CLI's bytes as a string, like its siblings, and
  the dashboard parses that string — so the "download .json" button
  delivers the CLI's artifact, not a browser re-serialization of it.
  **The assertions that "covered" it compared parsed dicts**, which cannot
  tell a re-serialization from the original; they now compare raw bytes.
- **The signer's conflict guard could be bypassed.** It looked only at
  git's trailer block — the final paragraph. A commit that already
  carried another agent's signature but had prose after the trailer block
  (a tool marker, a note) was signed a second time instead of being
  refused: two agents on one commit, which is exactly the state the
  module says it never creates. The read side and the write side now
  share one definition of "this commit carries a signature"
  (`gitparse.iter_coauthor_lines`), so they cannot disagree about it.
- **An unmatched co-author was invisible on an agent-signed commit.**
  The `cosigned-unknown` finding was emitted only for commits whose whole
  bucket was `cosigned`, so a commit signed by a known agent *and* an
  unknown party hid the unknown party completely. The finding is now
  emitted for any commit; `cosigned_evidence` keeps its documented
  meaning.
- **The `trailer-in-body` finding quoted the wrong line.** git puts
  trailers LAST, so a prose mention comes *before* them — counting
  positions quoted the legitimate trailer instead of the offending prose,
  and the wrong line propagated into the text, markdown, HTML and SARIF
  renderings. The offending prose line is now quoted.
- **One malformed JSON-RPC line killed the MCP server.** A peer sending a
  JSON array, a number or a string ended the process — taking every
  subsequent request with it. The server now answers with a JSON-RPC
  error and keeps serving; the three error classes are distinguished
  (`-32602` invalid params, `-32601` unknown method, `-32603` internal).
- **An inverted window returned different codes on different surfaces.**
  The evidence surfaces filtered first and reported `window-empty` where
  the audit and coverage surfaces reported `window-invalid`. The bounds
  are now validated before filtering, so all three report
  `window-invalid`.
- **The web rejected a request the CLI and the MCP accepted.** The scope
  is irrelevant once the auditor names the commits explicitly — the CLI
  and the MCP ignore it there, the web rejected it. The rule now lives in
  one place (`options.resolve_scope`).
- **A client that under-delivered its body held a worker thread
  forever.** `Content-Length` bounded the size, not the stall; with
  keep-alive the thread parked indefinitely. The handler now has a socket
  timeout, and a stalled body is answered and the connection closed.
- **A window bound behaved differently on different supported
  interpreters.** `datetime.fromisoformat` was used, and CPython 3.11
  widened it to accept most ISO 8601 forms while 3.9 accepts almost
  none — so the same bound was accepted on one supported Python and
  rejected on another. The accepted set is now pinned by an explicit
  parser in `models.py`, not by the interpreter.
- **A non-ASCII digit lost the documented error code.** `"²".isdigit()`
  is true but `int("²")` raises, so the documented
  `window-bound-invalid` never happened and a raw Python message
  escaped to the caller.
- **`all_refs: false` could not override a stale `all: true`** — a
  client sending both spellings got all-refs when it asked for HEAD. An
  explicit `all_refs` now wins.
- **A non-integral session gap was silently truncated** (`1.9` became
  `1`), accepting a value the CLI's own argparse refuses.
- **The portfolio brief hardcoded `HEAD`** in its scope row, so a
  portfolio produced with `--ref feature/x` stated the wrong scope.
- **The audit brief's "Regenerate" command could not work.** It emitted
  `--ref all` for the all-refs scope (not a git revision) and
  `--ref main..HEAD` for a range. It now emits `--all` / `--base` /
  `--ref` as the scope requires — in a document whose purpose is
  reproducibility, a command that fails is worse than a wrong number.

### Changed
- The CLI, the MCP server and the web transport resolve scope, window,
  roster, session gap and gate through `ghostauthor/options.py`.
  Behaviour is unchanged where it was already correct; where the copies
  disagreed, the documented code wins.
- The CLI's `--all` / `--ref` are no longer an argparse mutually
  exclusive group: the conflict is reported as `ref-conflict`, the code
  the other two surfaces return and `docs/schema.md` documents.
- `/api/audit` returns the report as a **string** (the CLI's exact
  bytes) rather than an embedded object, matching `/api/evidence` and
  `/api/coverage`. The dashboard parses it.

236 → 296 tests / verify 27 → 28 steps green.


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

## [1.4.0] — 2026-11-22

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

[1.6.1]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.6.1
[1.6.0]: https://atomgit.com/xiangsir/ghostauthor/releases/tag/v1.6.0
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
