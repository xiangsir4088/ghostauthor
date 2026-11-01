# Writing a roster

The roster is the project's community entry point: **adding an agent is a
data change, not a code change.** A roster is declarative JSON that tells
the tool *which Co-Authored-By trailers belong to which agent* — and,
optionally, what identity that agent signs with on the write path.

Rosters are **validated, never trusted**: a malformed roster is rejected
with a precise rule id (below), never silently repaired. A silently
"fixed" roster would make the verdict unauditable.

## File shape

```json
{
  "version": 1,
  "agents": [
    {
      "id": "claude",
      "display_name": "Claude (Anthropic)",
      "name_patterns": ["^Claude\\b", "^OpenAI Claude\\b"],
      "email_domains": ["anthropic.com"],
      "signing": { "name": "Claude", "email": "noreply@anthropic.com" }
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | Positive integer. The merged roster reports the maximum of its files. |
| `agents` | yes | Non-empty list, **file order is the precedence order** (see below). |
| `agents[].id` | yes | `^[a-z0-9][a-z0-9_-]*$`; unique within a file *and* across a `--roster-dir`. Used by `sign --agent`, reports, sessions. |
| `agents[].display_name` | yes | Human-readable label for text/markdown output. |
| `agents[].name_patterns` | one of the two selectors | Regexes matched **against the trailer's name** (the part before `<email>`). |
| `agents[].email_domains` | one of the two selectors | Lowercased domains, compared to the trailer email's domain. |
| `agents[].signing` | no | Opt-in write-path identity: the exact `name <email>` a `ghostauthor sign` / installed hook will put in the trailer. |

At least one of `name_patterns` / `email_domains` is required per agent
(an agent with no selectors matches nothing and is useless — rejected,
not allowed to rot).

## Matching semantics — read this before writing patterns

The precedence is data, not magic:

1. **Across agents:** file order wins. The first agent in the file with
   *any* matching selector claims the trailer. (Merged
   `--roster-dir` rosters keep sorted-filename file order.)
2. **Within an agent:** `name_patterns` are tried first, in list order;
   then `email_domains`.
3. **Patterns are `re.match` — prefix matches, not full matches.**
   `"Claude"` matches `"Claude"` and `"Claude Code"` and
   `"Claude, on vacation"`. Anchor deliberately:
   `"^Claude\\b"` matches names *starting* with Claude followed by a
   non-word boundary. If you mean the whole name, use
   `"^Claude$"`.
4. **The email is optional in a trailer.** A trailer without `<email>`
   can only match via `name_patterns`; the domain check never fires.
5. **First match stops.** A trailer is attributed to exactly one agent
   (or none). A commit, however, may carry trailers for *several* agents
   — that is the documented overlap (the cap invariant), surfaced as a
   `multi-agent-commit` finding.

`ghostauthor evidence` shows which selector actually fired
(`name pattern '^Claude\\b'` / `email domain 'anthropic.com'`) — use it
to verify a roster behaves the way you intended.

## The round-trip rule (signing)

If an agent carries a `signing` section, the validator enforces
**`roster-signing-roundtrip`**: the identity must match *that agent's
own* rules — `signing.email`'s domain is in its `email_domains`, or
`signing.name` hits one of its `name_patterns`. Otherwise a signed commit
could not be read back to its agent by the read side, and the write path
is useless. The sign command adds a second guard
(**`sign-ambiguous`**): if roster *precedence* would attribute the
identity to a **different** agent (a broader rule earlier in file
order), signing is refused — even though the round-trip rule passed.

## Validation rule ids

Rejection, with these ids, never repair:

| Rule id | Trigger |
|---|---|
| `roster-file-missing` / `roster-dir-missing` | the path does not exist |
| `roster-json-invalid` | not parseable JSON (per file, named) |
| `roster-dir-empty` | a `--roster-dir` without `*.json` files |
| `roster-top-level` | the document is not an object |
| `roster-version` | `version` is not a positive integer |
| `roster-agents` | `agents` missing or empty |
| `roster-agent-type` | an agent entry is not an object |
| `roster-agent-id` | `id` missing or not matching `^[a-z0-9][a-z0-9_-]*$` |
| `roster-agent-dup` | duplicate `id` within one file |
| `roster-agent-display` | `display_name` missing or blank |
| `roster-agent-patterns` / `roster-agent-pattern` | `name_patterns` not a list / an entry not a non-empty string or a broken regex |
| `roster-agent-domains` / `roster-agent-domain` | `email_domains` not a list / an entry with whitespace or not a string |
| `roster-agent-empty` | neither selector present |
| `roster-dir-dup` | the same `id` in two files of a `--roster-dir` (conflicts are for humans) |
| `roster-signing-type` / `roster-signing-name` / `roster-signing-email` | `signing` not an object / blank name / blank or non-email `email` |
| `roster-signing-roundtrip` | the identity does not match the agent's own rules |

## Lint: warnings, not rejections

`ghostauthor lint-roster` (exit 0 always) surfaces fragility that
validation deliberately does not reject — a legal roster can still be a
fragile one:

| Rule id | Warning | Fix |
|---|---|---|
| `lint-catch-all` | a `name_patterns` entry that swallows almost everything (tested against 5 probes) | narrow the pattern; catch-all precedence silently swallows later agents |
| `lint-overlap` | an agent's `signing` identity would be attributed, under roster precedence, to a *different* agent | move the specific rule earlier, or narrow the earlier rule — `sign --agent` for that identity will be refused (`sign-ambiguous`) |

Run lint in CI (it is byte-stable JSON and advisory, so it cannot block
a build — it is for humans reviewing roster changes).

## A directory of rosters (`--roster-dir`)

Teams can keep several files in one directory. Files merge in **sorted
filename order**; each file is validated on its own; a duplicate id
*across* files is `roster-dir-dup` — resolved by a human, never hidden by
merge order. Precedence = sorted-filename file order, then in-file order.

## Best practice

- **Specific patterns, explicit anchors.** `^Claude\\b` over `Claude`;
  `^OpenAI Codex\\b` over `Codex` (which would also match "Codex-style
  refactoring").
- **Order specific before general.** If you must keep a broad rule, put
  the narrow ones *before* it — file order is precedence.
- **One canonical identity per agent, in `signing`** — no free-text
  identities anywhere else.
- **Rosters are data in git.** Review them like code; run
  `lint-roster` on changes; an evidence bundle ships the exact roster
  snapshot used, so any historical verdict can be re-checked.
- **Test your rules.** `ghostauthor evidence <repo> <sha>` shows the
  fired selector; the demo fixtures (`python -m examples.make_fixtures`)
  give you a deterministic playground.
