# Roster guide — custom agent matching

The roster is declarative JSON and is the community entry point for
contributions: adding an agent is a data change, not a code change.

## Shape

```json
{
  "version": 1,
  "agents": [
    {
      "id": "myagent",
      "display_name": "My Agent (Acme)",
      "name_patterns": ["^My Agent\\b"],
      "email_domains": ["acme.com"],
      "signing": { "name": "My Agent", "email": "noreply@acme.com" }
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9_-]*$` — the key used in the report |
| `display_name` | yes | human name shown in the report |
| `name_patterns` | ≥1 with domains | regexes matched against the trailer **name** (anchored at start) |
| `email_domains` | optional | matched against the trailer email domain |
| `signing` | optional | the identity the write path uses: `{name, email}` |

At least one name pattern or email domain is required. `signing` is optional —
without it the agent can be matched (read) but not signed (write).

## Precedence: first match wins

Roster **file order is the precedence**. The first rule whose pattern or domain
matches a trailer attributes it. There is no ranking heuristic — order is data,
and reordering changes attribution. Never put a broad pattern (like `.*`)
before a specific one.

## It is validated, never repaired

A malformed roster is rejected with a precise rule id (exit 2): duplicate `id`
(`roster-agent-dup`), bad regex (`roster-agent-pattern`), empty selectors
(`roster-agent-empty`), a `signing` identity that does not match its own agent
(`roster-signing-roundtrip`), a duplicate id across roster files
(`roster-dir-dup`). A silently "fixed" roster would make the verdict
unauditable — so it is refused, never quietly repaired.

## Lint (advisory, exit 0)

`ghostauthor lint-roster` surfaces rosters that are legal but fragile: a
pattern that matches every probe identity (`lint-catch-all`) or a `signing`
identity that roster precedence would attribute to a different agent
(`lint-overlap`). Warnings are advisory; the verdict belongs to a human.
