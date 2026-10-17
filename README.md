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

## 60-second start

```bash
pip install git+https://atomgit.com/xiangsir/ghostauthor.git
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
```

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

It also ships a **read-only MCP server** (one tool, `repo_authorship_report`,
stdio, zero dependencies) — see `ghostauthor/mcp_server.py`. It reports; it
never executes. That is an invariant, not a promise.

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
two refs, side by side. Every count expands to its commit list; the raw
byte-stable JSON is one click away. Like the CLI and the MCP server, the web
transport is **read-only**: it reports, it never executes.

## Design trade-offs

| Decision | Why |
|---|---|
| No LLM anywhere in the verdict path | A reviewer that can be steered by wording will eventually be steered by wording. Verdicts must be re-runnable, auditable, and appealable. |
| Trailers only — no style detection | Provenance, not guessing. "Unsigned" is a category, not an accusation: unsigned ≠ human. |
| Stdlib only, zero dependencies | An audit tool must run on the machine it audits. No venv ritual, no supply chain of its own. |
| Read-only MCP server, one tool | The moment a tool can *act*, the risk model changes from "advice" to "supervised decision". `ghostauthor` refuses that category change. |
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
roster**. The builtin roster ships 8 agents; yours is probably not on it:

- `ghostauthor/roster/agents.json` is declarative: `id`, `display_name`,
  `name_patterns` (regexes matched against the trailer name) and
  `email_domains` (matched against the trailer's email domain). The first
  match in file order wins — precedence is data, not magic.
- Rosters are **validated, not trusted**: duplicate ids, broken regexes,
  empty selectors are rejected with a rule id — never silently repaired. A
  silently "fixed" roster would make the verdict unauditable.
- Teams can keep several roster files in one directory (`--roster-dir`):
  files merge in sorted filename order, and a duplicate id *across* files
  is rejected (`roster-dir-dup`) — conflicts are for humans to resolve,
  never for merge order to hide.
- Bring a test: `tests/test_roster.py` is the validator's own spec.

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
- [ ] v1.0 — write path (opt-in trailer signing), with the attestation boundary documented

## Tests

86 tests pin **properties**, not coverage:

- **Reproducibility** — same repo state ⇒ identical report, regardless of input order
- **Trailer-block semantics** — negative cases: signature in the body, signature followed by prose, CRLF messages, missing email, lowercase keys, continuation lines
- **The validator, validated** — malformed rosters that must be rejected, each checked against its rule id
- **The cap** — a multi-agent commit is counted once in totals; per-agent overlap is documented, not hidden
- **Gate boundary** — exactly at the limit is a pass; a breach exits 4
- **Window boundary** — `--since` inclusive, `--until` exclusive; an empty or inverted window is a contract error, not a silently empty report
- **Window-bound parsing** — epoch / date / ISO 8601 resolve to the same instants; naive values are UTC; garbage is rejected
- **Roster-dir merge** — sorted filename order, cross-file duplicate ids rejected by rule id
- **SARIF mapping** — byte-stable; findings → results with rule descriptors; a breached gate is an `error`-level result
- **Framing contract** — the git-output parser is pinned against crafted streams, including rejection of unframed output
- **Ref scoping** — a ref limits the scan to that branch/tag/commit; combining it with `--all` is rejected (`ref-conflict`); a missing path is `not-a-repo`, never a server failure
- **Two-branch diff** — `--base` scans git's `base..target` range; an empty range is `no-commits` (never a silent report); `--base` + `--all` is `ref-conflict`; a `-`-leading base is `ref-invalid`
- **Cross-tab** — per-author rows, per-agent columns: multi-agent commits land in every matched column (the cap invariant), row totals are deduplicated, every cell expands to a commit list
- **Web parity** — the web transport serves byte-identical JSON to the CLI; request-level errors (bad window/gate/roster) are 400, per-entry errors (not-a-repo 404, no-commits 422) carry their documented codes

One click runs the whole matrix (roster → unit suite → 3 deterministic
fixture repos → CLI exit codes → real MCP `initialize`/`tools/list`/
`tools/call` → rendering → time window → roster-dir merge → SARIF → live
web server, byte-stable vs the CLI → two-branch diff + cross-tab), and
prints anything it skips and why:

```bash
python verify.py
```

## License

MIT — see [LICENSE](LICENSE).
