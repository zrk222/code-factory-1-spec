"""SpecLine — the spec-driven production line.

Vibe coding fails at the four-file mark; SpecLine replaces it with a
standardized, tool-agnostic operating procedure any coding agent
(Claude Code, Codex, others) can follow:

  PRD -> Spec (EARS) -> Plan -> Atomic Tasks -> Task Packets (token-lean)
      -> Ralph Wiggum execution loop -> Review Gates -> Production

Decision-shaped logic (ordered rules over extracted facts) doesn't go
through agents at all: `specline handoff` emits a Harness Software
Factory workflow spec so it can be compiled ONCE into gated,
deterministic code. Agents write tissue; humans own the skeleton;
the factory owns the decisions.
"""
__version__ = "0.5.4"
