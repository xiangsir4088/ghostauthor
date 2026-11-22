# Detection vs. attestation — the boundary

**One line: detection (read) answers "who signed"; attestation (write) answers
"who claims authorship". The tool makes claims easy to make and impossible to
hide — it does not, and cannot, verify them as true.**

## Read side (detection) — CLI / MCP / Web / CI

- Reads what agents already sign: the `Co-Authored-By:` trailer block.
- Deterministic, reproducible, no trust model: same repo state ⇒ same report,
  byte for byte. No LLM, no network, no judgment.
- `unsigned` is a category, not an accusation: **unsigned ≠ human**. It means
  "no signature was found", nothing more.

## Write side (attestation) — `ghostauthor sign` / commit-msg hook

- Adding a trailer is a **claim** ("I claim this commit is mine"). Its
  credibility comes from the **process** around it (who ran the command, under
  what policy, in review of what) — never from the tool.
- The tool's job is narrow: make the claim well-formed, traceable, and
  consistent with the roster the same repository is audited with.

## Why the two are deliberately separated

The read side is **advice**: it can run unsupervised, because a wrong report
cannot change anything by itself. The write side produces a statement that may
be consumed by the CI exit-4 gate — a decision artifact. So the category change
is not hidden; it is **agreed** (opt-in), **auditable** (every signature is
just a trailer the read side already reports), and **revertible**.

## What the write path enforces (supervision)

| Mechanism | What it enforces |
|---|---|
| opt-in, no default hook | nothing signs by default |
| dry-run by default | `--commit` is the consent |
| one commit at a time | `sign` amends HEAD; the hook signs the commit being born (no sha rewrite) |
| idempotency | a second sign is a no-op, never a duplicate trailer |
| no-merge on conflict | another roster agent's signature is refused (`sign-conflict`); at commit time the hook exits non-zero and git aborts the commit |
| documented escape hatch | `git commit --no-verify` bypasses the hook; the bypass is not hidden — the audit reports it as `unsigned` |
| marker-protected hook | never overwrites/removes a foreign `commit-msg` (`hook-exists` / `hook-not-ours`) |
| single source of truth | signing identity comes from the roster's `signing` section, never env or free text |
| round-trip constraint | a signing identity that does not match its own agent's rules is refused; `install-hook` self-tests before writing |

## The honest limit

The tool **cannot** prevent a deliberate actor from lying, and it does not
certify any claim as true — that belongs to process and people (code review,
org policy, sign-off). Its job is to make lies **visible**: auditable,
expandable, reproducible.
