# ghostauthor

> **Don't let "looks AI-written" pass as an audit conclusion — the signature is the auditable evidence.**
> 别让「看着像 AI 写的」当成审计结论——署名才是可审计的证据。

`ghostauthor` is a deterministic ledger of AI code provenance. It reads what
agents already sign — the `Co-Authored-By:` git trailers — and answers, for
any repository: **which agent signed which commits, how many lines, in what
sessions.** No LLM, no style detection, no network. Python, stdlib only.

## What problem does this solve

"AI code ratio" is already a number everyone quotes and nobody can defend.
Today it comes from one of three places:

1. **Usage trackers** count tokens spent in a session. That measures
   *activity*, not *output* — a long session can merge zero code.
2. **Style classifiers** guess "this looks like AI". Probabilistic — and it
   cannot answer the question an auditor actually asks: *which commit, which
   agent, when*?
3. **Host-bound dashboards** (e.g. GitLab's `refs/notes/ai`) work great —
   if your whole org lives on that host and that mechanism.

The real problem is not "can we measure AI output?". The real problem is that
**"AI wrote it" is currently an unverifiable claim in commit history.** The
agents already sign their work in a standard place — git trailers. Nobody
reads those signatures the way `git log` reads everything else: with rigor.

`ghostauthor` does exactly that one thing, and treats the answer like an
audit artifact: reproducible, traceable, appealable.

## Start here if the ledger is empty

A provenance ledger has a cold-start problem: if the agents in your repository
never wrote a trailer, the audit is *correct* and *useless*. Every commit is
`unsigned`, the ratio is `0.0%`, and it looks like the tool is broken. It is
not — the signatures are missing, and there are exactly two reasons:

```bash
ghostauthor coverage /path/to/your/repo
```

`coverage` separates them, because they have different fixes:

| What it finds | What it means | Fix |
|---|---|---|
| A trailer no roster rule claims | The signature exists (`Co-Authored-By: Cursor <…>`), the roster just does not know that name | **Data**: add a roster rule (`--roster`) |
| Unsigned commits carrying a literal tool marker (`Generated with [Claude Code]`) | The agent never emitted a trailer | **Write path**: `install-hook`, or turn the trailer on in the agent |
| Unsigned commits with no marker | Unknown. It may be a human, or an agent that leaves no trace | **Nothing is claimed.** The ledger records signatures; it does not guess |

It also reports coverage by author and by ISO week, so "when did adoption
start, and who is still outside it" is a measurement rather than a guess.
`coverage` never changes an attribution — markers are hints, and they are
labelled as hints.

## 60-second start

```bash
pip install ghostauthor
# (PyPI, v1.3+; always-latest alternative:
#  pip install git+https://atomgit.com/xiangsir/ghostauthor.git)
ghostauthor /path/to/your/repo
ghostauthor /path/to/your/repo --format json --max-ai-ratio 0.5
ghostauthor /path/to/your/repo --since 2026-07-01 --until 2026-10-01
# audit a quarter, not a history (epoch, YYYY-MM-DD or ISO 8601; since is
# inclusive, until is exclusive)
ghostauthor /path/to/your/repo --ref feature-x
# audit one branch/tag/commit instead of HEAD
ghostauthor /path/to/your/repo --base main --ref feature-x
# two-branch diff: only what feature-x adds over main (git range main..feature-x)
ghostauthor /path/to/your/repo --format sarif
# SARIF 2.1.0 for compliance pipelines
ghostauthor /path/to/your/repo --format html --out audit.html
# a self-contained brief for readers who will not run the CLI: no script,
# no remote asset, no timestamp — the same state regenerates the same bytes
ghostauthor portfolio ~/code --format html --out portfolio.html
# the same ledger across every repository under a directory (aggregate is
# weighted by commit, never a mean of ratios); a broken repo is recorded
# in the output instead of aborting the scan
```

Signing (opt-in, v1.0) — the only write path, off by default:

```bash
ghostauthor sign /path/to/repo --agent claude
# dry-run by default: shows the amended message, writes nothing
ghostauthor sign /path/to/repo --agent claude --commit
# actually amends HEAD with the agent's Co-Authored-By trailer (roster `signing` identity)
```

Signing at commit time (opt-in, v1.1) — a repo-level `commit-msg` hook,
installed only when you ask for it:

```bash
ghostauthor install-hook /path/to/repo --agent claude
# every commit now carries the trailer at commit time — before the commit
# exists, so no amend and no sha rewrite. Replaces its own hook
# idempotently; never touches a foreign one.
ghostauthor uninstall-hook /path/to/repo
# back to unsigned; a one-off bypass is `git commit --no-verify` (the
# audit shows the bypass as an unsigned commit — nothing is hidden)
```

And a lint that warns — without rejecting — when a legal roster is fragile:

```bash
ghostauthor lint-roster --roster my-team.json
# [warn] lint-catch-all / lint-overlap; exit 0 either way. Validation still
# rejects malformed rosters (exit 2); lint only surfaces fragility
```

Drilling into a verdict (v1.2) — per-commit evidence and a reproducible
bundle:

```bash
ghostauthor evidence /path/to/repo <sha>
# which roster rule matched each trailer and why the audit attributed the
# commit the way it did — the same attribution code as the audit, so the
# two can never disagree
ghostauthor evidence /path/to/repo <sha> --format json
# machine-readable; the JSON contract is frozen (schema_version 1,
# schema/evidence.schema.json — the same additive/bump policy as the report)
ghostauthor evidence /path/to/repo --bundle out/bundle
# a reproducible evidence package: report.json (frozen schema) +
# evidence.json + the exact roster used + manifest.txt with sha256s — no
# timestamps, so re-running on the same state is byte-identical
# (canonical UTF-8/LF)
```

The JSON contract it reads and writes under is frozen: `schema_version` in
the output, `schema/report.schema.json` as the machine-checkable form,
`docs/schema.md` as the single source of truth (compatibility policy
included). What signing means — and what it deliberately is **not** — is
documented in [docs/attestation.md](docs/attestation.md): the read side
reports, the write side makes a claim, and the tool makes claims easy and
auditable, not true.

No repository handy? Clone this one and build the bundled demo fixtures
(deterministic: fixed dates, identities, contents — the SHAs are stable):

```bash
git clone https://atomgit.com/xiangsir/ghostauthor.git
cd ghostauthor
python -m examples.make_fixtures out/fixtures
python -m ghostauthor.cli out/fixtures/mixed
```

Output, verbatim from a real run on the `mixed` fixture:

```text
ghostauthor — deterministic AI code provenance ledger
repo: D:\Projects\atomcode\ghostauthor\out\fixtures\mixed   ref: HEAD

commits: 12 total
  agent-signed         6  (50.0%)
  cosigned-unknown     1  (8.3%)
  unsigned             5  (41.7%)

lines touched: 18 total
  agent 9  cosigned 2  unsigned 7

agents:
  ID         NAME                      COMMITS   +ADDED  -REMOVED  SESSIONS  LONGEST
  claude     Claude (Anthropic)              4        4         2         1        4
  codex      Codex (OpenAI)                  3        3         2         1        3

authors x agents:
  AUTHOR                         claude     codex  cosigned  unsigned   TOTAL
  Sam Human <sam@example.com>         4         3         1         5      12

findings (3):
  [warn] cosigned-unknown  9f4f9cc34f31: Co-Authored-By: VibeBot <vibe@example.com>
         Co-Authored-By present but no roster rule matched
  [info] multi-agent-commit  99186ce21e94
         commit carries signatures of several agents: claude, codex
  [warn] trailer-in-body  bf73ac1eecac: Co-Authored-By: Claude <noreply@anthropic.com>
         Co-Authored-By line outside the trailer block is prose, not metadata — ignored by design
```

As a CI gate, the exit code is the contract — also a real run, on the
`agent-heavy` fixture (11 of 12 commits agent-signed):

```text
$ python -m ghostauthor.cli out/fixtures/agent-heavy --max-ai-ratio 0.5
...
gate: ai ratio 91.7% vs limit 50.0% — BREACHED
$ echo $?
4
```

| Exit code | Meaning |
|---|---|
| `0` | report produced; gate (if any) not breached |
| `2` | environment/usage error: not a repo, no commits, bad roster, bad args |
| `4` | gate breached: agent-signed ratio above `--max-ai-ratio` |

It also ships a **read-only MCP server** (three tools — `repo_authorship_report`,
`commit_evidence` and `adoption_diagnosis` — stdio, zero dependencies) — see
`ghostauthor/mcp_server.py`. Each exposes its CLI command's option surface
(scope, window, roster, gate) and returns bytes that are **identical to the
CLI's** for the same request. It reports; it never executes. That is an invariant, not a
promise.

## Web UI

The same ledger with a local dashboard: one self-contained HTML page served
by a stdlib-only `http.server`. No framework, no build step, no CDN — the
"runs on the machine it audits" invariant holds for the web version too, and
the page only ever triggers the read-only `git log` in the repository you
point it at. The JSON it shows and lets you download is **byte-identical to
the CLI's** `--format json` for the same repo state: one source of truth,
two skins.

```bash
ghostauthor-web                       # → http://127.0.0.1:8321
ghostauthor-web --port 9000           # different port
ghostauthor-web --host 0.0.0.0        # only if you really mean it
```

It binds `127.0.0.1` by default. Enter one or two repository paths — each
optionally scoped to a ref (`main` vs a feature branch) — plus the same
options the CLI has: time window, session gap, `--max-ai-ratio` gate, roster
file or dir. Add a second entry to compare two codebases, or one codebase at
two refs, side by side. Every count expands to its commit list, and every
commit row drills into its **evidence** — which roster rule matched each
trailer and why the audit attributed the commit that way; a standalone query
box covers shas outside the current audit scope. The raw byte-stable JSON is
one click away. Under the hood the dashboard is two JSON endpoints —
`/api/audit` and `/api/evidence` — each serving bytes identical to the CLI for
the same request, so the page and the terminal can never disagree. Like the
CLI and the MCP server, the web transport is **read-only**: it reports, it
never executes.

## Design trade-offs

| Decision | Why |
|---|---|
| No LLM anywhere in the verdict path | A reviewer that can be steered by wording will eventually be steered by wording. Verdicts must be re-runnable, auditable, and appealable. |
| Trailers only — no style detection | Provenance, not guessing. "Unsigned" is a category, not an accusation: unsigned ≠ human. |
| Stdlib only, zero dependencies | An audit tool must run on the machine it audits. No venv ritual, no supply chain of its own. |
| Read-only MCP server, two read-only tools | The read side reports; it never executes. That is an invariant, not a promise. |
| One write path, opt-in, supervised (v1.0) | The moment a tool can *act*, the risk model changes from "advice" to "supervised decision". So the write path crosses that line **on purpose and visibly**: `ghostauthor sign` is an explicit subcommand (no hook, no default), dry-run by default, idempotent, refuses conflicts, and signs only with identities the roster itself validates. The boundary is written down in [docs/attestation.md](docs/attestation.md). |
| Exit codes `0/2/4` as a CI contract | A tool nobody can wire into a pipeline is a toy. |
| Missing ≡ empty, never an exception | Incomplete data is the norm. A tool that crashes on a malformed trailer demands "fill it in completely first" — nobody does that under pressure. |

## Invariants — what keeps this honest for years

These are not features. They are the things that would quietly rot the tool:

1. **Provenance, not style.** Only the git trailer block is read — git's
   semantics, not our invention. A `Co-Authored-By:` line in the *body* of a
   message is prose: ignored as data, surfaced as a `trailer-in-body`
   finding. Without this boundary, the tool becomes a guesser wearing a
   ledger's skin.
2. **Every number is traceable.** Every bucket, agent and session count
   carries its commit list in the JSON report. A verdict you cannot expand
   into evidence is a verdict you cannot defend in a review.
3. **The cap.** One commit can carry several agent signatures. Per-agent
   attribution may overlap — but the deduplicated totals never do. The
   report shows both numbers, so "per-agent sums exceed the total" is a
   documented fact, not a contradiction.
4. **Same repo state ⇒ same report.** Commits are ordered by
   `(authored_at, sha)` read from the commits — never the wall clock, never
   hash-iteration luck. JSON output is byte-stable (`sort_keys`): diffable,
   CI-friendly, archive-able.
5. **Boundary semantics are pinned by tests.** A ratio *exactly at*
   `--max-ai-ratio` is a PASS (`>`, not `>=`); a window *includes* its
   `--since` bound and *excludes* its `--until` bound. Changing any of
   these semantics requires changing the test on purpose.

The write path (v1.0) adds five more — see
[docs/attestation.md](docs/attestation.md) for the full boundary:

6. **Round-trip (load-bearing).** Sign a commit with a roster agent and
   the audit must attribute it to that agent — never `cosigned`, never
   `unsigned`. A signature that could not be read back is refused at
   roster validation (`roster-signing-roundtrip`) and again at sign time
   (`sign-ambiguous`), not written.
7. **Idempotency.** Signing an already-signed commit is a no-op; the
   second run's diff is empty.
8. **Opt-in & consent.** The command exists only when explicitly invoked
    (no default flow); dry-run is the default and `--commit` is the
    explicit consent. The v1.1 commit-msg hook extends the same line: it
    exists only after an explicit `install-hook`, is repo-level,
    marker-protected (a foreign `commit-msg` is never overwritten or
    removed), and removable at any time — no repository is ever signed
    by default.
9. **No-merge on conflict.** A commit already signed by *another* roster
   agent is refused (`sign-conflict`) — conflicts are for humans, never
   for the tool to resolve.
10. **Single source of truth.** Matching (read) and signing (write) share
    one roster; the signing identity is part of that agent's roster
    record, not env or free text.

## A traceable sample (real run, JSON fragment)

From the same `mixed` fixture — every count below expands to the commit
lists under `bucket_commit_shas`:

```json
{
  "agents": [
    {
      "commits": 4,
      "display_name": "Claude (Anthropic)",
      "first_ts": 1767229200,
      "id": "claude",
      "last_ts": 1767261600,
      "lines_added": 4,
      "lines_removed": 2,
      "longest_session_commits": 4,
      "sessions": 1
    },
    {
      "commits": 3,
      "display_name": "Codex (OpenAI)",
      "first_ts": 1767240000,
      "id": "codex",
      "last_ts": 1767261600,
      "lines_added": 3,
      "lines_removed": 2,
      "longest_session_commits": 3,
      "sessions": 1
    }
  ],
  "ai_ratio": 0.5,
  "bucket_commit_shas": {
    "agent": [
      "deb8b1e68469d720a92ba6b1ab7efedc0dbb07b2",
      "5dc8f5c47ad6ecda46bc52b1f2ca38f05f70c904",
      "d9bdca2577cb2a312e8397dbef7e94cffe01d298",
      "06ba554e15d5ec38c87d17134470b357deb137e5",
      "b7439afbe93d8edd462b4abe2473a1b3d8a10855",
      "99186ce21e94660c0466914561ccd0e7e0943c09"
    ],
    ...
```

## How to contribute

The most valuable contribution to this project is **not code — it is the
roster**. The builtin roster ships 8 agents — 7 of them with a canonical
`signing` identity, so `ghostauthor sign --agent claude` works out of the
box; yours is probably not on it:

- `ghostauthor/roster/agents.json` is declarative: `id`, `display_name`,
  `name_patterns` (regexes matched against the trailer name) and
  `email_domains` (matched against the trailer's email domain). The first
  match in file order wins — precedence is data, not magic.
- An optional `signing` section (`{"name": ..., "email": ...}`) opts the
  agent into the write path: `ghostauthor sign --agent <id>` will then
  sign with exactly that identity — and the validator rejects any
  identity that would not read back to its own agent
  (`roster-signing-roundtrip`).
- Rosters are **validated, not trusted**: duplicate ids, broken regexes,
  empty selectors are rejected with a rule id — never silently repaired. A
  silently "fixed" roster would make the verdict unauditable.
- Teams can keep several roster files in one directory (`--roster-dir`):
  files merge in sorted filename order, and a duplicate id *across* files
  is rejected (`roster-dir-dup`) — conflicts are for humans to resolve,
  never for merge order to hide.
- Bring a test: `tests/test_roster.py` is the validator's own spec.

The full writing guide — matching semantics (first-match-wins, prefix
matches, explicit anchors), every validation rule id, lint rules and
best practice — is [docs/roster.md](docs/roster.md).

For code contributions, read `docs/architecture.md` first — each module has
things it is forbidden to do (the engine does no rendering, the report does
no recomputation, the MCP server does no execution). Keep it that way.

## Roadmap

- [x] v0.2 — `--since/--until` time windows (audit a quarter, not a history)
- [x] v0.2 — SARIF 2.1.0 output for compliance pipelines
- [x] v0.2 — multiple roster files / `--roster-dir`
- [x] v0.3 — `--ref`: audit one branch/tag/commit instead of HEAD
- [x] v0.3 — web UI: local read-only dashboard, stdlib only, byte-stable JSON, side-by-side comparison
- [x] v0.3.1 — `--base`: CLI two-branch diff (git range `base..target`; the web UI compares two refs side by side)
- [x] v0.3.1 — per-author × per-agent cross-tabulation (every cell traceable to a commit list)
- [x] v0.3.2 — encoding hardening: UTF-8 stdout/stderr and git subprocess (`i18n.logOutputEncoding`, `core.quotepath=false`) so Chinese Windows (GBK locale) never mangles reports; web UI light theme
- [x] v0.4.0 — CI integration: composite GitHub Action, self-audit workflow, PR comment template
- [x] v0.5.0 — read-side contract frozen: `schema_version` in the JSON, `schema/report.schema.json`, golden snapshot, verify enforces the freeze (a mutation goes red)
- [x] v1.0.0 — write path: opt-in `ghostauthor sign` (dry-run default, `--commit` to apply, idempotent, conflict refused) with the detection/attestation boundary documented in `docs/attestation.md`
- [x] v1.1.0 — commit-time signing: repo-level `commit-msg` hook via `install-hook` / `uninstall-hook` (signed at commit time — no amend, no sha rewrite; marker-protected against foreign hooks; `--no-verify` is the documented escape hatch) + `lint-roster` (catch-all / cross-agent-overlap warnings, exit 0) + canonical `signing` identities for the builtin roster
- [x] v1.2.0 — per-commit `evidence` (which roster rule matched each trailer and why — the same attribution code as the audit, so the two can never disagree) + `--bundle` reproducible evidence package (report + evidence + roster snapshot + sha256 manifest, no timestamps) + `roster.match_detail` (reason-carrying match)
- [x] v1.3.0 — installable from PyPI (`pip install ghostauthor`; OIDC trusted publishing, no token in the repo) + the evidence JSON contract frozen (`schema/evidence.schema.json`, enforced by verify — a mutation goes red) + `docs/roster.md` (writing guide) + `README.zh.md`
- [x] v1.4.0 — read-side surface parity: the MCP `repo_authorship_report` gains the full option surface (scope, window, roster, gate) and a second read-only tool `commit_evidence`; the web gains `/api/evidence` + an in-dashboard evidence drill-down. Every surface now serves the **same bytes** as the CLI for the same request — parity by construction, not by test, because report/evidence selection is one shared function
- [x] v1.5.0 — **product surface**: `coverage` (adoption diagnosis — separates an unrecognized signature from a missing one, and never guesses), `--format html` (a self-contained, timestamp-free brief that can be mailed or archived), `portfolio` (multi-repository aggregate, weighted by commit, partial failures recorded). Read-side additions only — no schema bump
- [x] v1.6.0 — **one definition of a boundary**: scope / window / roster / session gap / gate move into `ghostauthor/options.py`, called by the CLI, the MCP server and the web transport. Fixes the drift the copies had accumulated — `/api/audit` silently ignored `base`, lost the CLI's error codes, and spelled the scope flag differently from its own siblings. Read-side only — no schema bump
- [x] v1.6.1 — **a wrong type is a request error, never a guess**: `"all": "false"` is now rejected (`scope-invalid`) instead of being coerced to true by `bool()` — the old behaviour silently scanned every ref and returned a confident, wrong answer; `ref: 123` went from a 500 to a 400 (`ref-invalid`); `roster_dir: []` no longer falls back to the builtin roster; a boolean is no longer accepted as a minute count or a gate limit. The MCP gains a third read-only tool `adoption_diagnosis` (byte-identical to `coverage --format json`), and `portfolio --max-ai-ratio` adds an organisation-level gate (exit 4). `coverage`'s fix suggestions now name real roster ids (`claude`, not `claude-code`), and `python -m ghostauthor` works. `portfolio.gate` is an additive key; `schema_version` stays `1`
- [x] v1.6.2 — **the consolidation, finished**: all three surfaces now resolve scope through the same function in `options.py`. v1.6.0 moved the rules there, but only the web transport actually called it — the CLI and the MCP server each kept their own copy, and both carried a comment claiming the rule lived in `options.py`. The copies happened to agree, so nothing looked wrong. The MCP server also stops coercing a mistyped sha (`[123]` → `bad-shas`, the same code the web returns), `portfolio` validates its options before scanning instead of reporting a wrong summary, and an unreadable roster is a request error (`roster-error`, exit 2) rather than a traceback or a dead server
- [x] v1.7.0 — **every remaining copy, retired**: the window filter and its two codes move to `options.apply_window` (engine, coverage and evidence all call it); the gate verdict and the ratio move to `engine.build_gate` / `engine.compute_ratio`, so the single-repo report and the portfolio decide on one number — a rounded copy used to give the opposite verdict at the same boundary; `gate.ratio` is the exact float (rounding is the renderers' job); `coverage`'s no-signatures advice names a roster agent that can actually sign; the finding-rule catalogue lives in one place (`models.RULE_INFO`); the documentation catches up (the third MCP tool, `scope-invalid`/`roster-invalid`, the 3.11/3.13 matrix, the Python 3.10 floor). `schema_version` stays `1`

## Free and commercial

The CLI is MIT and always will be. It is also the whole product for one
repository: audit, evidence, signing, the MCP server, the local dashboard.

Everything a **team or an auditor** needs on top of that is a separate,
paid surface — and the boundary is deliberately drawn at the point where
work stops being a command and becomes a deliverable:

| Free (MIT, this repository) | Commercial |
|---|---|
| CLI audit, JSON / text / markdown / SARIF | Branded HTML briefs for a board or a client |
| Per-commit evidence, evidence bundles | Multi-repository portfolio reports with history |
| Local web dashboard | Hosted scans, scheduled snapshots, trend lines |
| `coverage` diagnosis | Policy-as-code: per-path / per-team rules |
| `portfolio` over local checkouts | Organisation roll-up with SSO and retention |
| Single-repository CI gate | Audit liaison: methodology pack, reviewer Q&A |

Nothing in the free tier is crippled after the fact, and the JSON contract is
frozen either way: a report you generate today validates against the same
schema next year. If you need the commercial surface, open an issue with
`commercial` in the title.

## Tests

376 tests pin **properties**, not coverage:

- **Reproducibility** — same repo state ⇒ identical report, regardless of input order
- **Trailer-block semantics** — negative cases: signature in the body, signature followed by prose, CRLF messages, missing email, lowercase keys, continuation lines
- **The validator, validated** — malformed rosters that must be rejected, each checked against its rule id
- **The cap** — a multi-agent commit is counted once in totals; per-agent overlap is documented, not hidden
- **Gate boundary** — exactly at the limit is a pass; a breach exits 4
- **Window boundary** — `--since` inclusive, `--until` exclusive; an empty or inverted window is a contract error, not a silently empty report
- **Window-bound parsing** — epoch / date / ISO 8601 resolve to the same instants; naive values are UTC; garbage is rejected
- **Roster-dir merge** — sorted filename order, cross-file duplicate ids rejected by rule id
- **Signing identity** — the roster `signing` section is optional; a signing identity that would not read back to its own agent is rejected (`roster-signing-roundtrip`), never repaired
- **Write path** — dry-run is the default (nothing lands without `--commit`); sign → audit round-trips to the same agent; a second sign is a no-op; another roster agent's signature is refused (`sign-conflict`); an identity that roster precedence would misattribute is refused (`sign-ambiguous`); every error path exits 2 and changes nothing
- **Commit-msg hook** — a real `git commit` fires the installed hook and the trailer round-trips to the audit; a presigned message keeps exactly one trailer (idempotent); a conflicting commit is **blocked** (non-zero commit-msg exit aborts the commit, nothing lands); `--no-verify` bypasses and the audit shows the unsigned commit; `install-hook` refuses a foreign `commit-msg` (`hook-exists`) and `uninstall-hook` refuses one it did not install (`hook-not-ours`); the generated script pins its own interpreter, PYTHONPATH and roster
- **Roster lint** — a catch-all `name_patterns` entry is warned (`lint-catch-all`), a signing identity that roster precedence would attribute to another agent is warned (`lint-overlap`); warnings are advisory (exit 0, deterministic, byte-stable JSON), a malformed roster is still a hard error (exit 2); the builtin roster lints clean
- **Evidence** — for every commit, the evidence's attribution equals the audit's bucket (they are the same code); a matched trailer names the exact roster selector (`match_detail` agrees with `match`); the JSON is byte-stable; an unknown SHA exits 2 (`sha-unknown`); a `--bundle` is reproducible (same state + path ⇒ same bytes), its `report.json` equals the audit's report, its manifest sha256s match the files on disk, and writing into an existing directory is refused (`bundle-exists`)
- **Evidence freeze** — the real `evidence --format json` output validates against `schema/evidence.schema.json`; the bundle's `evidence.json` is the same bytes; a removed key, an added key and a bogus attribution are each rejected by name
- **Schema freeze** — the real `render_json` output validates against `schema/report.schema.json`; the golden snapshot is byte-stable; removed/added keys are rejected by name
- **SARIF mapping** — byte-stable; findings → results with rule descriptors; a breached gate is an `error`-level result
- **Framing contract** — the git-output parser is pinned against crafted streams, including rejection of unframed output
- **Ref scoping** — a ref limits the scan to that branch/tag/commit; combining it with `--all` is rejected (`ref-conflict`); a missing path is `not-a-repo`, never a server failure
- **Two-branch diff** — `--base` scans git's `base..target` range; an empty range is `no-commits` (never a silent report); `--base` + `--all` is `ref-conflict`; a `-`-leading base is `ref-invalid`
- **Cross-tab** — per-author rows, per-agent columns: multi-agent commits land in every matched column (the cap invariant), row totals are deduplicated, every cell expands to a commit list
- **Web parity** — the web transport serves byte-identical JSON to the CLI; request-level errors (bad window/gate/roster) are 400, per-entry errors (not-a-repo 404, no-commits 422) carry their documented codes
- **MCP parity** — all three MCP tools return the CLI's exact bytes for the same request: `repo_authorship_report` mirrors the full option surface, `commit_evidence` mirrors `evidence --format json` (multi-sha input order preserved, no bundle — the server never writes files), `adoption_diagnosis` mirrors `coverage --format json` (so an agent that sees 0% can ask *why* without shelling out); request errors carry the CLI's codes, repo errors (`sha-unknown`, `not-a-repo`) are pinned
- **Web evidence** — `/api/evidence` serves the CLI's `evidence` bytes for scope / multi-sha / window requests; `sha-unknown` is 422, `not-a-repo` is 404, request errors are 400; the dashboard carries the evidence UI (query box + per-sha inline drill-down)
- **Adoption diagnosis** — `coverage` bands are inclusive at the lower bound (0.2 / 0.8) and zero is its own band (`none`, never `minimal`); an unrecognized trailer is reported as a signature with its fix, a known tool marker is a hint on an unsigned commit and never changes a bucket, and a commit with no marker produces no claim at all; author rows reconcile with the bucket totals; the JSON is byte-stable and validates against `schema/coverage.schema.json`
- **Portfolio weighting** — the aggregate is `sum(signed)/sum(commits)`, never a mean of per-repository ratios (pinned with 1 commit at 100% vs 99 at 0%); a failed repository is counted, named and excluded from the totals instead of aborting the run; all repositories failing is `portfolio-empty` (exit 2); per-repository agent detail expands the rollup; `--max-ai-ratio` applies the report's boundary to the aggregate (`ratio > limit` breaches, equality passes) and exits 4 after producing the report
- **HTML brief** — byte-stable (no generation timestamp), self-contained (no `<script>`, no remote URL), every interpolated value escaped, and the method statement travels with the document — the properties that let it sit next to the JSON as evidence rather than as a screenshot
- **Web coverage** — `/api/coverage` serves the CLI's `coverage` bytes for the same request; the dashboard shows an inline diagnosis prompt only below 20% coverage (a healthy ledger is never nagged), and its error mapping matches the exit codes
- **A wrong type is a request error, never a guess** — `"all": "false"` is rejected (`scope-invalid`) instead of being coerced, because Python's `bool("false")` is `True` and the old behaviour silently scanned every ref; `ref: 123` is a 400, not a 500; `roster_dir: []` no longer falls back to the builtin roster; a boolean is not a minute count or a gate limit
- **Option parity** — one definition of scope / window / roster / session gap / gate (`ghostauthor/options.py`), called by all three surfaces: the same bad request yields the **same error code** on the CLI, the MCP server and the web transport, and the web serves the CLI's exact bytes for the same scope — including a two-branch diff, which `/api/audit` used to silently ignore; `all` and `all_refs` are synonyms everywhere; a malformed entry is a `400`, never an unhandled exception
- **Byte identity is compared as bytes** — the web serves the report as the CLI's exact byte string, and the assertions compare **raw strings**, not parsed dicts: a re-serialization (different key order, different indentation) parses to the same thing and would pass a dict comparison while making the claim false
- **The write path sees every signature** — the conflict guard scans the whole message, not just git's trailer block: a commit already signed by another agent is refused even when prose follows the trailer (and signing is still idempotent when *our* signature is the one followed by prose)
- **Nothing hides** — an unmatched co-author is surfaced on *every* commit, not only on the ones whose whole bucket is `cosigned`; the `trailer-in-body` finding quotes the offending prose line, not the legitimate trailer that follows it
- **A malformed request never kills a server** — one bad JSON-RPC line is answered with a JSON-RPC error and the MCP server keeps serving; a stalled request body is answered and the connection closed instead of parking a worker thread
- **The same bound means the same thing everywhere** — the accepted ISO 8601 subset is pinned by an explicit parser, not by the interpreter's `fromisoformat`, so a bound behaves identically on every supported Python

One click runs the whole matrix (roster → unit suite → 3 deterministic
fixture repos → CLI exit codes → real MCP `initialize`/`tools/list`/
`tools/call` → rendering → time window → roster-dir merge → SARIF → live
web server, byte-stable vs the CLI → two-branch diff + cross-tab → CI
assets → schema freeze, enforced → write path: round-trip / idempotency /
conflict / default-off → hook: install / sign-at-commit / idempotent /
conflict / uninstall + roster lint → evidence: audit agreement / byte
stability / reproducible bundle → evidence freeze, enforced → MCP surface
parity: 3 tools, each byte-identical to the CLI, every error code → web
evidence: `/api/evidence` byte-identical, 400/404/422, UI markers), and
prints anything it skips and why:

```bash
python verify.py
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history (v0.1.0 →
present), including the read-side contract-freeze policy and the additive-only
write path.

## License

MIT — see [LICENSE](LICENSE).
