# SpecLine ðŸ­

**A spec-driven production line for AI coding agents.** PRD â†’ spec â†’ plan â†’
atomic task packets â†’ gated code â†’ production, with token-lean context
hygiene enforced by tooling instead of discipline, and a compiled-decision
handoff to [Harness Software Factory](../harness-factory) for the logic that
should never be improvised twice.

Works with **Claude Code, Codex, and any agent harness** â€” one command wires it in.

```
PRD â”€â”€> Spec (EARS+Gherkin) â”€â”€> Gate â”€â”€> Plan (atomic tasks) â”€â”€> Gate
                                                        â”‚
              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
              â–¼
   â”Œâ”€â”€ Ralph Wiggum Loop â”€â”€â”        Decision tables in the spec
   â”‚ specline loop next    â”‚        â”€â”€> specline handoff
   â”‚  â†’ token-budgeted     â”‚        â”€â”€> HSF compiles them ONCE into
   â”‚    TASK PACKET        â”‚            gated, deterministic code
   â”‚ agent does ONE task   â”‚            (zero tokens per decision, forever)
   â”‚ specline loop done    â”‚
   â”‚  â†’ verify + seal      â”‚
   â””â”€â”€â”€â”€ context reset â”€â”€â”€â”€â”˜ â”€â”€> Gate â”€â”€> ship
```

## Why

Vibe coding hits the wall around four files: context pollution, intent
drift, API hallucinations. The fixes are known â€” specs as source of truth,
constitutions, vertical slices, context resets â€” but they live in blog
posts as *discipline*. SpecLine turns them into *tooling*: linted, gated,
hash-sealed, and receipt-audited, so the discipline holds at 2am too.

## Quickstart (5 minutes, no API keys)

```bash
pip install -e ".[dev]"
specline init                      # constitution + six-file context system
specline new refunds               # spec + plan skeletons
# ... you + your agent fill the spec ...
specline validate refunds          # EARS/Gherkin/leak lint â€” ambiguity dies here
specline verify-validators refunds # mutate requirements; catch hollow validators
specline optimize-prd specs/refunds.md # score PRD clarity before implementation
specline gate spec refunds        # hash-sealed human signoff
specline tasks refunds             # atomicity lint: â‰¤4 files, one slice, verify cmd
specline gate plan refunds        # locks the spec hash (drift guard arms)
specline loop next refunds         # emits a token-budgeted TASK PACKET
# ... agent session does exactly one packet ...
specline loop done refunds T1      # runs verify command, seals receipt, advances
specline handoff refunds           # decision table -> HSF workflow spec
specline agent claude              # wires CLAUDE.md + /next-task command
specline status                    # token-savings receipt
pytest -q                          # 30 tests
```

## PRD optimizer

Run `specline optimize-prd <PRD.md>` before handing a feature to an agent. It
scores the document for outcome clarity, executable requirements, acceptance
criteria, proof, non-goals, risk language, and ambiguity. A weak PRD exits
nonzero and prints the exact edits that would make the work reviewable.

This complements `specline strict` and `specline verify-validators`: the
optimizer makes the PRD stronger for humans, strict lint removes ambiguity for
agents, and validator mutation proves the checks are not hollow.

## The mechanisms (what's actually enforced)

| Blog-post advice | SpecLine enforcement |
|---|---|
| "Write clear specs" | EARS keyword lint, Gherkin required, implementation-leak detection (`E_IMPL_LEAK`) |
| "Keep tasks small" | Atomicity linter: â‰¤4 files, one vertical slice, explicit verify command, no skeleton edits |
| "Reset agent context" | The loop emits self-contained **task packets** under a hard ~2.2k-token budget; one packet = one session |
| "Minimize context (C_t=Î³Â·R_fÂ·T_d)" | Packets list the exact R_f file set; excerpt only spec lines relevant to the task; deterministic prune over budget |
| "Prevent intent drift" | Plan gate seals the spec hash; if the spec changes, the loop **refuses** (`E_INTENT_DRIFT`) until re-gated |
| "Human review gates" | `specline gate spec|plan|code` writes hash-sealed signoff receipts to the progress tracker |
| "Don't let agents improvise business rules" | Decision tables compile through HSF: one-time generation, four gates, zero tokens per decision |
| "Measure the process" | SpecFactor gauge (Goldilocks 0.75â€“2.5) + a **context ledger**: packet tokens vs naive baseline, % saved |

## Agent integration

- **Claude Code:** `specline agent claude` â†’ writes `CLAUDE.md` (constitution +
  protocol) and `.claude/commands/next-task.md`. The whole loop is one slash command.
- **Codex:** `specline agent codex` â†’ appends the protocol to `AGENTS.md`
  (Codex reads it natively).
- **Anything else:** `specline agent <name>` â†’ portable constitution file.
  The protocol is plain text; any harness that can read a file can follow it.

## The factory calibration (the part that saves real money)

Most business logic in AI-built apps is *decision-shaped*: ordered rules over
extracted facts. Letting agents re-implement those rules inline is how you get
inconsistent behavior and burned tokens. SpecLine specs carry a
`## Decision logic` table; `specline handoff` converts it to a Harness
Software Factory spec, and HSF compiles it once into deterministic, gated,
signed code â€” verified end-to-end in this repo's test suite against a real
HSF install. App code flows through the line; decisions flow through the
factory; nothing is improvised twice.

## Receipts culture

Every gate signoff, packet emission, and task completion writes a hash-sealed
line to `context/PROGRESS.md`, and the context ledger accumulates the token
economics (`specline status` â€” the walkthrough example shows ~75% saved vs
naive full-context sessions, and the gap widens as the repo grows). Claims
trace to receipts, never to vibes. That's the whole point.

MIT licensed.

---

## v0.2 â€” Strict Input Contract & Drift Audit

The base linter checks that a spec *looks* right (EARS keywords present, valid task
format). That's necessary but not sufficient: it lets **ambiguity** through, and the
AI coder then *invents* the missing parameters â€” which is drift. v0.2 closes that gap
with two new stages that bracket the coder.

### `specline strict <feature>` â€” reject ambiguity *before* the coder runs

Treats the spec as a **contract the coder must execute with zero invention**. Every
finding is a BLOCK with an exact line and fix. It catches the five drift sources:

1. **Incomplete requirements** â€” an EARS keyword isn't enough. Each requirement must
   have a concrete outcome verb (`return`/`reject`/`store`/â€¦), not `handle`/`support`/
   `manage`. `The system shall handle it appropriately` is rejected.
2. **Surviving placeholders** â€” `<trigger>`, `<N>`, `TBD` can't reach an approved spec.
3. **Unquantified bounds** â€” a requirement that implies a timeout/limit/retry/size must
   state a number+unit.
4. **Untraceable acceptance** â€” every value in a Given/When/Then must be defined in a
   requirement or the data model. A Gherkin step can't introduce a fact the coder would
   have to invent.
5. **Non-deterministic decisions** â€” each rule's `if` references a declared fact and its
   `then` is exactly one outcome. No `maybe`/`or`/`etc`; no duplicate conditions.
   (`else`/`default` catch-all rows are allowed.)

An `approved` spec that still fails strict raises `S_APPROVED_BUT_AMBIGUOUS` â€” approval
is a lie until the blocks are resolved.

Strict is **on by default** in `specline gate spec|plan`. Pass `strict=False` to the
gate API only for legacy specs.

### `specline verify-validators <feature>` - prove strict is not hollow

Strict lint checks the original spec. Validator mutation checks the instrument:
it deletes or inverts one requirement at a time and requires strict lint to
notice. If a mutant still passes, that requirement reports `HOLLOW_VALIDATOR`.

This is reverse-classical validation for specs. A hollow test passes against an
empty implementation; a hollow validator passes against a mutilated spec.

### `specline audit <feature> --files â€¦ --slice â€¦` â€” catch drift *after* the coder runs

Compares what shipped against what the contract authorized:

- **`A_INVENTED_PARAM`** â€” a config value (`TIMEOUT = 45`) whose number the spec never
  authorized. The coder guessed; the audit fails the build.
- **`A_SCOPE_ESCAPE`** â€” a file outside the task's authorized slice.
- **`A_UNAUTHORIZED_FILE`** â€” a file not in the packet's list.
- **`A_STUB_LEFT`** â€” a `TODO`/`NotImplementedError` left behind.

### Requirement-scoped packets

The packet excerpt no longer bag-of-words-matches individual lines (which could hand the
agent half a requirement). It now ships **whole requirement blocks** and the **complete
acceptance scenario intact** â€” the agent never receives a partial rule to improvise around.

### Flow

```
new â†’ write spec â†’ validate â†’ strict â†’ gate spec â†’ write plan â†’ tasks â†’ gate plan
    â†’ loop (build) â†’ audit â†’ gate code â†’ handoff
```

Deterministic by design: same spec text â†’ same findings, every run. No LLM, no clock.
Validator mutation runs between `strict` and `gate spec` in the hardened flow.

## Failure attribution

SpecLine 0.3.1 reports strict-lint results per requirement and drift-audit results
per Python function. Failed units include a stable class such as
`ambiguous_requirement`, `untyped_input`, `invented_param`, or `scope_escape`,
plus the offending source phrase or code location. Existing pass/fail rules do
not change.

For machine-readable output:

```bash
specline strict my_feature --json
specline audit my_feature --files slices/my_feature/logic.py --json
```

