# Attestation boundary — detection vs. proof

**One line: detection (read) answers "who signed"; attestation (write)
answers "who claims authorship". The tool makes claims easy to make and
impossible to hide — it does not, and cannot, verify them as true.**

This document is the boundary that `ghostauthor sign` (v1.0) and the
commit-msg hook (v1.1) cross, written down *before* the features, because
crossing the line is the point of the releases — not a side effect of
them.

## The two halves

### Detection (read side: CLI / MCP / Web / CI)

- Reads what agents already sign: the `Co-Authored-By:` trailer block,
  per git semantics.
- Deterministic, reproducible, no trust model: same repo state ⇒ same
  report, byte for byte. No LLM, no network, no judgment.
- `unsigned` is a category, not an accusation: **unsigned ≠ human**. It
  means "no signature was found", nothing more.

### Attestation (write side: `ghostauthor sign`)

- A person **or an agent** adding a trailer is a *claim* — "I claim this
  commit is mine". The claim's credibility comes from the **process**
  around it (who ran the command, under what policy, in review of what),
  never from the tool itself.
- The tool's job on the write side is narrow: make the claim
  well-formed, traceable, and consistent with the roster the same
  repository is audited with.

## The line that was crossed

The read side is **advice**: it can run unsupervised, because a wrong
report cannot change anything by itself.

The write side produces a *statement that may be consumed by the CI exit
4 gate* — a decision artifact. Per the architecture rule this tool has
always observed:

> The moment a tool can act, the risk model changes from "advice" to
> "decision that must be supervised".

So the category change is not hidden. It is **agreed** (opt-in, explicit
flags), **auditable** (every signature is just a trailer the read side
already reports), and **revertible** (the subcommand and the roster
`signing` fields are purely additive; deleting both restores v0.5
behavior exactly — the read side never looks at `signing`).

### How the write path is supervised

| Mechanism | What it enforces |
|---|---|
| opt-in, no default hook | The command only runs when explicitly invoked; the hook only exists after an explicit `install-hook` (repo-level, marker-protected). Nothing in any pipeline signs by default |
| dry-run by default | `--commit` is the consent; without it, the command prints and stops |
| exactly one commit at a time | `sign` amends HEAD; the v1.1 hook signs the message of the commit *being born* (before it exists — no amend, no sha rewrite). No history rewriting, no batch signing, ever |
| idempotency | A second sign of the same commit is a no-op, never a duplicate trailer — including at commit time: a presigned message keeps exactly one trailer |
| no-merge on conflict (`sign-conflict`) | A commit already signed by another roster agent is refused; conflicts are for humans. At commit time the refusal is stronger: the hook exits non-zero and **git aborts the commit** — the conflict cannot be committed into history |
| documented escape hatch | `git commit --no-verify` bypasses the hook; the bypass is not hidden — the audit reports the commit as `unsigned` |
| marker-protected hook files | `install-hook` never overwrites a foreign `commit-msg` (`hook-exists`); `uninstall-hook` never removes one it did not install (`hook-not-ours`). A hook the tool cannot identify is a human's file |
| single source of truth | The signing identity lives in the roster's `signing` section — the same roster the audit uses; no env vars, no free-text identities. The generated hook pins the roster path used at install time |
| round-trip constraint (load-bearing) | The roster rejects a `signing` identity that does not match its own agent's rules; the sign command additionally refuses (`sign-ambiguous`) an identity that roster precedence would attribute to a *different* agent. A signature that cannot be read back is refused, not written — and `install-hook` refuses to install under such a roster, and self-tests the generated command before writing the hook |
| identity from roster only | No inference from commit content or environment: `sign --agent` / `install-hook --agent` must name a roster id that exists and carries `signing` |

## The honest abuse surface

What the tool **cannot** prevent — only make visible through audit and
process:

- **An agent inflating its own share**: signing its own commits (legit or
  not) or a human's commits. The read side reports what is signed; it
  cannot know the truth behind it.
- **A human adding trailers by hand**: legitimate (that is the point of
  the format) — but also how a mis-sign or a malicious one happens.
- **The roster itself being edited**: whoever adds an agent (or a broad
  `name_patterns` such as `.*`) changes what the audit counts. Roster
  changes must therefore be **human-reviewed and visible** — that is why
  the roster is data in the repository, subject to review, and why
  `sign-ambiguous` refuses to sign under a precedence that would
  misattribute.

## What the tool does, and what it refuses to do

**Does** (defenses it can actually hold):

- every number expands to its commit list (the audit trail);
- per-commit evidence (v1.2): every attribution expands to the exact
  roster selector that matched — same code as the audit, so the two can
  never disagree about a commit;
- the cap invariant: per-agent overlap is documented, deduplicated totals
  never double-count;
- round-trip: a signature is either readable back to its agent or it was
  never written;
- conflict refusal: two roster agents on one commit is a human decision;
- a single source of truth for identity, under human review.

**Refuses** (the honest limit):

- It does not stop a deliberate actor from lying.
- It does not certify any claim as true. "Declared as true" belongs to
  **process and people** — code review, org policy, sign-off — not to the
  tool.
- The tool's responsibility is to make lies **visible**: auditable,
  expandable, reproducible.

## Invariants the write path adds (6–10)

1–5 are the read-side invariants (README). The write path adds:

6. **Round-trip (load-bearing):** sign with a roster agent ⇒ the audit
   attributes the commit to that agent — never to `cosigned`, never to
   `unsigned`.
7. **Idempotency:** signing an already-signed commit is a no-op; the
   second run's diff is empty.
8. **Opt-in & consent:** off by default; any landing change requires
   `--commit`, and the default is a dry-run that only displays.
9. **No-merge on conflict:** another roster agent's signature on the
   commit ⇒ refusal. Never merged, never overwritten.
10. **Single source of truth:** matching (read) and signing (write) share
    one roster; the signing identity is part of that agent's roster
    record.
