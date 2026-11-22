# Attribution model — how a commit is classified

The ledger classifies every commit into exactly one of three buckets. The
buckets are exhaustive; a missing count is `0`, never absent.

| Bucket | Meaning |
|---|---|
| `agent` | the trailer block has at least one `Co-Authored-By` that matched a roster rule |
| `cosigned` | has `Co-Authored-By` trailers, none matched the roster |
| `unsigned` | no `Co-Authored-By` trailer in the trailer block |

## The trailer block (git semantics)

Only the **final paragraph** of a commit message that consists solely of
`key: value` lines is the trailer block. A `Co-Authored-By:` line written in
the prose body is not metadata — it is ignored as data and surfaced as a
`trailer-in-body` finding. A trailer without `<email>` yields `email=""`
(missing ≡ empty, never an exception).

## The cap invariant

One commit can carry several agent signatures. Per-agent attribution therefore
**overlaps**, so:

- `Σ per-agent commits ≥ total commits` (documented, not a contradiction)
- Deduplicated bucket totals never double-count: `agent + cosigned + unsigned == total`

The report shows both numbers so the overlap is visible, never hidden.

## Sessions

An agent's commits are grouped into sessions: a run of that agent's commits
where the gap between consecutive `authored_at` values is ≤ the session gap
(default 360 minutes). `sessions` counts runs; `longest_session_commits` is the
longest run.

## Ordering (determinism)

Commits are ordered by `(authored_at, sha)` read from the commits themselves —
never the wall clock, never dict iteration luck. JSON is emitted with
`sort_keys`, so the same repo state always yields the same bytes.

## The three findings that never change the verdict

| rule | severity | meaning |
|---|---|---|
| `cosigned-unknown` | warn | `Co-Authored-By` present but no roster rule matched |
| `trailer-in-body` | warn | a signature-shaped line in the prose body (ignored by design) |
| `multi-agent-commit` | info | several agents signed one commit (overlap is the cap) |
| `trailer-no-email` | info | matched by name only; no `<email>` in the trailer |
