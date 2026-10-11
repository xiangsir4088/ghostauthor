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
| `models.py` | data model + bucket constants | any IO, any git call |
| `roster.py` | load + validate the declarative agent roster | silently "fixing" bad input |
| `gitparse.py` | git log extraction, trailer-block parsing | any judgment, any matching |
| `engine.py` | normalize → match → sessions → aggregate | any rendering, any file IO |
| `report.py` | render text / json / markdown | any recomputation |
| `cli.py` | argparse + file IO + exit codes | any logic |
| `mcp_server.py` | read-only stdio JSON-RPC | network, writes, execution |

Data flows one way: `git → gitparse → engine → report → cli/mcp`.
Nothing downstream recomputes what upstream decided.

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

## What is deliberately NOT here

- No style-based AI detection. That is a probabilistic classifier, and it
  answers a different question ("does this look like AI?") than the one this
  tool answers ("who signed this?").
- No hosting integration. It reads local git; GitHub/GitLab/AtomGit are all
  just checkouts.
- No signed-claim semantics. A human can add a trailer by hand; that is an
  attestation problem, not a detection problem. The report tells you what
  is signed, and the git history tells you when.
