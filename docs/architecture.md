# Architecture

## The two halves

The system is split exactly where determinism must start:

| Stage | Who does it | Why |
|---|---|---|
| Signature extraction | git (data) | commits are ground truth |
| **Matching / verdict** | **deterministic code** | must be reproducible, auditable, appealable |
| Explanation / prose | (out of scope) | not in v0.1 — no LLM anywhere in the verdict path |

There is no LLM in this project at all. Not in extraction, not in matching,
not in the gate. That is the design, not a limitation: a reviewer that can
be steered by wording will eventually be steered by wording.

## Modules

| File | Responsibility | Forbidden here |
|---|---|---|
| `models.py` | data model + bucket constants + shared window-bound parser | any IO, any git call |
| `roster.py` | load + validate the declarative agent roster | silently "fixing" bad input |
| `gitparse.py` | git log extraction (HEAD / all refs / one ref / one sha), trailer-block parsing | any judgment, any matching |
| `engine.py` | normalize → match → sessions → aggregate | any rendering, any file IO |
| `report.py` | render text / json / markdown / sarif | any recomputation |
| `evidence.py` | per-commit evidence entries + bundle assembly (pure: no IO, no wall clock; consumes `engine.attribute_commit`) | any re-attribution (that is `engine`'s job), any rendering of the frozen report (that is `report.py`'s job) |
| `cli.py` | argparse + file IO + exit codes | any logic |
| `signer.py` | the ONLY mutator: sign HEAD (`--amend`) or a commit message file (hook); install / remove the opt-in repo-level `commit-msg` hook | any rendering; touching a commit other than the one being signed; overwriting or removing a foreign hook; writing anything but the target message / hook file |
| `lint.py` | static roster lint: warnings only (catch-all, overlap), deterministic | rejecting input (that is `roster.py`'s job), any IO |
| `mcp_server.py` | read-only stdio JSON-RPC | network, writes, execution |
| `web.py` | read-only HTTP transport (stdlib `http.server`) + routing | any judgment, matching, recomputation |

Data flows one way: `git → gitparse → engine → report → cli/mcp/web`.
Nothing downstream recomputes what upstream decided. The write path is
the deliberate exception: `signer.py` reuses `roster.match` (the same
precedence the audit uses — that is what makes round-trip hold) but never
feeds the read pipeline.

## Invariants (the parts that keep the tool honest for years)

1. **Provenance, not style.** Only the git trailer block is read. Unsigned
   ≠ human — it is "unsigned". Missing ≡ empty, never an exception.
2. **Every number is traceable.** Every bucket/agent/session count carries
   its commit list in the JSON report. A verdict you cannot expand into
   evidence is a verdict you cannot defend in a review.
3. **The cap.** Per-agent attribution may overlap (one commit, two agents),
   but deduplicated totals never do: `Σ per-agent commits ≥ total`, and the
   report shows both, so "per-agent sums to more than the total" is a
   documented fact, not a contradiction.
4. **Same repo state ⇒ same report.** Ordering is by `(authored_at, sha)`
   read from the commits — never the wall clock, never dict iteration luck.
   JSON output is `sort_keys` — byte-stable, diffable, CI-friendly.
5. **Boundary semantics are pinned by tests.** A ratio exactly at
   `--max-ai-ratio` is a PASS (`>` not `>=`). Whoever changes the
   semantics must change the test on purpose.

## Exit codes (the CI contract)

| Code | Meaning |
|---|---|
| 0 | report produced; gate (if any) not breached |
| 2 | environment/usage error: not a repo, no commits, bad roster, bad args |
| 4 | gate breached: agent-signed ratio above `--max-ai-ratio` |

## MCP contract

Read-only. One tool: `repo_authorship_report`. No network, no writes, no
process execution beyond the read-only `git log` inside the repository it is
pointed at. **It never auto-executes anything.** The moment a tool can act,
the risk model changes from "advice" to "decision that must be supervised" —
this tool refuses that change of category.

## Web transport

A local dashboard over the same pipeline. The zero-dependency invariant
extends to it: `http.server` from the stdlib, one self-contained HTML page,
no framework, no build step, no CDN. Like the MCP server, the web transport
is pure wiring — it parses the request, runs the same
`iter_commits → build_report → render_json`, and returns the result. No
judgment, no matching, no recomputation, nothing executed beyond the
read-only `git log` inside the repositories it is pointed at.

The report it serves is **byte-identical to the CLI's `--format json`** for
the same repo state (pinned by `tests/test_web.py` and by verify), so the
reproducibility invariant holds on every transport. Error codes are the HTTP
translation of the exit-code contract: request-level option errors are 400,
a missing repository is 404, an empty repository is 422 — and a breached
gate is still a 200, because a breach is a verdict, not a failure. It binds
`127.0.0.1` by default: the audit tool runs on the machine it audits.

## What is deliberately NOT here

- No style-based AI detection. That is a probabilistic classifier, and it
  answers a different question ("does this look like AI?") than the one this
  tool answers ("who signed this?").
- No hosting integration. It reads local git; GitHub/GitLab/AtomGit are all
  just checkouts.
- No signed-claim semantics. A human can add a trailer by hand; that is an
  attestation problem, not a detection problem. The report tells you what
  is signed, and the git history tells you when.
- No default hooks, and no global hooks. The v1.1 `commit-msg` hook
  exists only after an explicit `install-hook`, only in that one
  repository's `.git/hooks/`, and is removable at any time. No
  `init.templateDir`, no global config — a repository's default behavior
  is never changed by this tool.
