# PROJECT CONSTITUTION (AGENTS.md / CLAUDE.md)
<!-- Injected into every agent session. The three-tier boundary system. -->

## ALWAYS DO
- Read `context/PROGRESS.md` first, then ONLY the files named in your task packet.
- Work on exactly ONE task per session. Finish, verify, stop.
- Write tests in the same task as the code they verify.
- Follow `context/CODE_STANDARDS.md` naming and directory patterns exactly.
- Keep all code for a feature inside its vertical slice directory.

## ASK FIRST
- Any change to database schemas or public API contracts.
- Adding ANY new third-party dependency (requires an ADR in `adr/`).
- Touching files outside the slice named in your task packet.
- Modifying anything under `skeleton/` (human-owned invariants).

## NEVER DO
- Never commit secrets, API keys, or hard-coded credentials.
- Never implement decision logic (ordered business rules) inline — flag it
  for `specline handoff` so the factory compiles it deterministically.
- Never leave TODO/stub logic in a task marked complete.
- Never work from memory of previous sessions: the disk is the truth.
