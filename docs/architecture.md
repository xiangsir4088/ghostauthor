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
| `options.py` | ONE definition of scope / window / roster / session gap / gate, shared by the CLI, the MCP server and the web transport | any git call, any rendering, any judgement about a commit |
| `roster.py` | load + validate the declarative agent roster | silently "fixing" bad input |
| `gitparse.py` | git log extraction (HEAD / all refs / one ref / one sha), trailer-block parsing, `iter_coauthor_lines` (every Co-Authored-By line, wherever it is) | any judgment, any matching |
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

### Why `options.py` exists

The CLI, the MCP server and the web transport all accept the same
request-shaped options: a scope (`--all` / `--ref` / `--base`), a time
window, a roster, a session gap, a gate. When each surface parsed those
independently, the copies drifted — and two of the differences were not
cosmetic:

* the web audit endpoint **ignored `base`**, so a two-branch diff request
  silently returned the whole history — a wrong answer that looks right;
* the web audit endpoint **collapsed every option error** into a generic
  `value-error`, so the CLI's code (`window-empty`, `ref-invalid`, …) was
  lost on the way out.

The third copy of the same logic is the bug waiting to happen. So the
semantics live in `options.py` and the surfaces *call* it. This is the
same move the project already made twice — the window-bound parser lives
in `models.py`, the evidence selection in `evidence.select_evidence_commits`
— and the reason those two were moved in the first place.

The rule that follows: **a boundary is defined once.** If two surfaces
can interpret the same request differently, that is a bug, not a
configuration.

The same rule governs "does this commit carry a signature?": the read
side (`gitparse.parse_trailer_block` — git's semantics, the final
paragraph only) and the write side (`gitparse.iter_coauthor_lines` — every
line, wherever it is) use **one** helper for the question, so a signature
cannot be visible to one and invisible to the other. When they drifted,
the signer could add a second agent's signature to a commit that already
carried one — the exact state the write path claims never to create.

### Where each boundary lives now

The consolidation was finished in two rounds, and the placement follows one
rule: a boundary that **decides** something lives with the thing that
decides; a boundary that **parses** a request lives in `options.py`.

| Boundary | Defined in | Shared by |
|---|---|---|
| scope (`--all` / `--ref` / `--base`) | `options.Scope` / `options.resolve_scope` | CLI, MCP, web |
| window **parsing** (`--since` / `--until`) | `options.resolve_window` → `models.parse_window_value` | CLI, MCP, web |
| window **filtering** + its codes | `options.apply_window` | engine, coverage, evidence |
| roster (load + validate) | `options.resolve_roster` → `roster.py` | CLI, MCP, web |
| session gap | `options.resolve_session_gap` | CLI, MCP, web |
| gate limit (validation) | `options.resolve_gate` | CLI, MCP, web |
| gate **verdict** (`ratio > limit`) | `engine.build_gate` | single-repo report, portfolio |
| the ratio itself | `engine.compute_ratio` | single-repo report, portfolio |
| finding rule ids + descriptions | `models.RULE_INFO` | engine (emits), report (describes) |

Two of these were the last copies standing, and both had teeth:

* the window **filter** was written out three times — in the engine, in
  coverage and in evidence — with the same predicate and the same two
  error codes. `options.py` owned only the *parsing*, so the window was
  half consolidated. It is now one function.
* the gate **verdict** was copied, not inherited, and the copy compared a
  ROUNDED ratio while the report compared the exact one. The same
  boundary could therefore give two different verdicts: a ratio of 1/3
  against a limit of `0.333333` breached in the single-repo report and did
  not breach in the portfolio. One function now decides both, and the
  number a document reports is the number the verdict was made on.

The ratio is one more case of the same rule. The JSON carries the **exact**
float; rounding belongs to the renderers. A report that carried `ai_ratio`
and `gate.ratio` as two differently-rounded copies of one number was a
document that could not explain itself — and a renderer that recomputed the
ratio from the buckets instead of reading `report.ai_ratio` was
re-deriving a number the model already held.

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

### Byte stability has a version premise (and only the report JSON is exempt)

"Same repo state ⇒ same bytes" is true **within one tool version**. It is
not a promise that two releases produce identical files, and the documents
that carry a version say so:

| Artifact | Embeds the version? | Byte-stable across releases? |
|---|---|---|
| the report JSON (`--format json`) | no | **yes** — the golden snapshot is version-independent |
| SARIF (`--format sarif`) | yes (`tool.driver.version`) | no |
| the HTML brief (`--format html`) | yes (`Tool: ghostauthor <version>`) | no |
| the evidence bundle manifest | yes (`tool: ghostauthor <version>`) | no |

This split is deliberate. The report JSON is the **frozen, machine-checked
contract** — its golden snapshot must survive a release that changes
nothing but the version number, so it carries no version at all. The
others are human-facing deliverables whose whole point is to name the tool
that produced them; a brief that does not say which version produced it is
not an auditable document.

The consequence is a rule for anyone diffing artifacts across releases:
**diff the report JSON, not the brief.** A SARIF or HTML difference
between two versions is usually just the version string, and treating it
as a finding wastes a review cycle.

## What ships where

The wheel on PyPI and the skill pack in the marketplace are **two
different distributions of two different things**, and confusing them
makes an install fail in a way that looks like a bug:

| Channel | What it carries | How it is installed |
|---|---|---|
| PyPI (`pip install ghostauthor`) | the `ghostauthor` Python package: the CLI, the MCP server, the web transport, the builtin roster | `pip install ghostauthor` |
| the skill pack (`ghostauthor-ai-code-audit/`) | a `SKILL.md` — instructions for an agent that drives the CLI/MCP, plus its assets | copied into an agent's skill directory, or installed from a skill marketplace |

`ghostauthor-ai-code-audit/` is **not** in the wheel, and that is not an
oversight: it has no `__init__.py`, so it is not a Python package, and
`[tool.setuptools.packages.find] include = ["ghostauthor*"]` only matches
packages. Shipping it inside the wheel would put a directory of
instructions where an importable module belongs.

So the skill pack has its own versioning and its own release channel: it
is versioned with the tool it drives (`SKILL.md` states the tool version
it was written for) and published as a skill artifact. A `pip install
ghostauthor` therefore does **not** install the skill — that is a
separate, deliberate step, and the README says which version each
channel is on.

## Exit codes (the CI contract)

| Code | Meaning |
|---|---|
| 0 | report produced; gate (if any) not breached |
| 2 | environment/usage error: not a repo, no commits, bad roster, bad args |
| 4 | gate breached: agent-signed ratio above `--max-ai-ratio` |

## MCP contract

Read-only. Three tools: `repo_authorship_report` (the full audit),
`commit_evidence` (per-commit evidence) and `adoption_diagnosis` (why the
ratio is low). No network, no writes, no process execution beyond the
read-only `git log` inside the repository it is pointed at. **It never
auto-executes anything.** The moment a tool can act, the risk model changes
from "advice" to "decision that must be supervised" — this tool refuses
that change of category.

All three tools return the CLI's **exact bytes** for the same request, and the
server answers a malformed request with a JSON-RPC error rather than
dying: one bad line must not take the session — and every request after
it — down with it.

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
reproducibility invariant holds on every transport. It is served as the
CLI's **byte string**, not as a re-serialized document: parsing the
report back into a dict and dumping it again would compact the output and
drop the canonical key order — the document would still *parse* to the
same thing while no longer being the CLI's artifact. The dashboard parses
that string for rendering and offers the same string for download, so the
"download .json" button delivers the CLI's bytes.

Error codes are the HTTP translation of the exit-code contract: request-level option errors are 400,
a missing repository is 404, an empty repository is 422 — and a breached
gate is still a 200, because a breach is a verdict, not a failure. It binds
`127.0.0.1` by default: the audit tool runs on the machine it audits.

The request path and the roster path are **request-controlled**: a client
that can reach the port can make the server read any directory as a
repository and any JSON file as a roster. The default bind is loopback,
which is why it is the default; `--host 0.0.0.0` is one flag away and
exposes that to the network.

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
