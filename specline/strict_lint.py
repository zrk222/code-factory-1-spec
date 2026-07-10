"""specline strict — the semantic input contract.

The base linter (spec_lint) checks that a spec *looks* right: EARS keywords
present, Gherkin fences present, task format valid. Necessary but not sufficient.
It lets ambiguity through, and the AI coder then *invents* the missing
parameters — which is drift.

This module closes the gap. It treats the spec as a CONTRACT the coder must
execute with ZERO invention: every requirement logically complete (actor +
measurable outcome + defined condition), every noun declared, every acceptance
value traceable to a requirement, every decision cell deterministic over
declared facts.

Deterministic, addressable, severity-tiered (BLOCK vs WARN). No LLM, no clock.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str          # "BLOCK" | "WARN"
    message: str
    line: int = 0

    def __str__(self) -> str:
        loc = f" (L{self.line})" if self.line else ""
        return f"[{self.severity}] {self.code}{loc}: {self.message}"


@dataclass
class StrictReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocks(self): return [f for f in self.findings if f.severity == "BLOCK"]
    @property
    def warns(self): return [f for f in self.findings if f.severity == "WARN"]
    @property
    def ok(self) -> bool: return not self.blocks

    def add(self, code, severity, message, line=0):
        self.findings.append(Finding(code, severity, message, line))

    def attribution(self, text: str):
        from .attribution import Attribution, FailureClass, UnitResult
        requirements = _requirement_lines(text)
        units = []
        for index, (line, phrase) in enumerate(requirements, 1):
            related = [f for f in self.blocks if f.line == line]
            failure_class = None
            if related:
                code = related[0].code
                failure_class = (
                    FailureClass.UNTYPED_INPUT if "TYPE" in code
                    else FailureClass.AMBIGUOUS_REQUIREMENT
                )
            units.append(UnitResult(
                unit=f"R{index}",
                stage="strict_lint",
                passed=not related,
                evidence="requirement passed strict lint" if not related else
                         f"{phrase.strip()} :: {related[0].message}",
                failure_class=failure_class,
            ))
        return Attribution("strict_lint", len(units), sum(u.passed for u in units), units)


_PLACEHOLDER = re.compile(
    r"<[^>]+>|\bTBD\b|\bTODO\b|\bFIXME\b|\bXXX\b|\b(?:foo|bar|baz|lorem|ipsum)\b", re.I)

_VAGUE_TERMS = {
    "appropriate", "appropriately", "reasonable", "reasonably", "properly",
    "correctly", "etc", "and so on", "and/or", "some", "several", "various",
    "fast", "quickly", "slow", "efficient", "efficiently", "performant",
    "large", "small", "big", "many", "few", "soon", "later", "roughly",
    "approximately", "about", "around", "handle", "handles", "handled",
    "handling", "manage", "manages", "process", "processes", "support",
    "supports", "deal with", "as needed", "if necessary", "where applicable",
    "user-friendly", "intuitive", "seamless", "robust", "scalable",
    "flexible", "simply", "just",
}

_DEICTIC = re.compile(r"\b(it|this|that|those|these|them|the thing|stuff)\b", re.I)

_OUTCOME_VERBS = {
    "return", "returns", "reject", "rejects", "store", "stores", "persist",
    "persists", "emit", "emits", "write", "writes", "read", "reads",
    "validate", "validates", "compute", "computes", "create", "creates",
    "delete", "deletes", "update", "updates", "respond", "responds",
    "raise", "raises", "log", "logs", "record", "records", "block",
    "blocks", "allow", "allows", "deny", "denies", "display", "displays",
    "send", "sends", "retry", "retries", "expire", "expires", "hash",
    "hashes", "sign", "signs", "encrypt", "encrypts", "redact", "redacts",
    "increment", "decrement", "queue", "enqueue", "dequeue", "cache",
    "invalidate", "authenticate", "authorize", "route", "map", "maps",
    "set", "sets", "assign", "assigns", "append", "prepend", "insert",
    "remove", "removes", "filter", "filters", "sort", "sorts", "merge",
    "split", "convert", "converts", "transform", "transforms", "call",
    "calls", "invoke", "invokes", "throw", "throws", "abort", "aborts",
    "commit", "commits", "rollback", "flush", "close", "open", "lock",
    "unlock", "acquire", "release", "notify", "notifies", "publish",
    "subscribe", "acknowledge", "fail", "fails", "succeed", "count",
    "accept", "accepts", "extract", "extracts", "show", "shows", "screen",
    "screens", "approve", "approves", "flag", "flags", "match", "matches",
    "check", "checks", "verify", "verifies", "generate", "generates",
    "parse", "parses", "render", "renders", "load", "loads", "save", "saves",
    "add", "adds", "list", "lists", "fetch", "fetches", "get", "gets",
    "put", "puts", "post", "posts", "patch", "delete", "escalate", "escalates",
}

_MEASURABLE = re.compile(
    r"\b\d+(\.\d+)?\s*(ms|s|sec|seconds|m|min|minutes|h|hours|days?|"
    r"bytes?|kb|mb|gb|chars?|characters?|tokens?|rows?|items?|requests?|"
    r"%|percent|px|em|dollars?)\b"
    r"|[<>]=?\s*\d|\bexactly\b|\bat most\b|\bat least\b|\bno more than\b"
    r"|\bno fewer than\b|\bwithin\b|\bequal to\b|\bbetween\s+\d"
    r"|\b(?:true|false|null|empty|non-empty|HTTP\s*\d{3}|\d{3}\s+status)\b", re.I)

_EARS_HEAD = re.compile(r"^\s*-\s+(The system shall|When\b|While\b|If\b|Where\b)", re.I)
_REQ_LINE = re.compile(r"^\s*-\s+\S")
_LITERAL = re.compile(r"[\"'`]([^\"'`]+)[\"'`]|(\b[A-Z][A-Z0-9_]{2,}\b)|(\b[A-Z][a-z]+[A-Z][A-Za-z]+\b)")


def _lines_with_numbers(text: str):
    return [(i + 1, ln) for i, ln in enumerate(text.splitlines())]


def _section(text: str, start: str, *stops: str) -> str:
    if start not in text:
        return ""
    body = text.split(start, 1)[1]
    for stop in stops:
        if stop in body:
            body = body.split(stop, 1)[0]
    return body


def _requirement_lines(text: str):
    must = _section(text, "## MUST", "## SHOULD", "## Decision logic")
    reqs = must.split("### Requirements")[-1] if "### Requirements" in must else must
    out, offset = [], text.find(reqs)
    consumed = text[:offset].count("\n") if offset >= 0 else 0
    for i, ln in enumerate(reqs.splitlines()):
        if _REQ_LINE.match(ln):
            out.append((consumed + i + 1, ln.strip()))
    return out


def _declared_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for m in _LITERAL.finditer(text):
        val = next(g for g in m.groups() if g)
        terms.add(val.strip().lower())
    for model in re.finditer(r"Data model:\s*[^\n(]*\(([^)]*)\)", text, flags=re.I):
        for field in model.group(1).split(","):
            name = field.strip().split(":", 1)[0].strip()
            if name:
                terms.add(name.lower())
    roles = _section(text, "### User roles", "###", "##")
    for ln in roles.splitlines():
        ln = ln.strip("- ").strip()
        if ln:
            terms.add(ln.lower())
    return terms


def _declaration_text(text: str) -> str:
    """Text that is allowed to declare facts for validators and rules."""
    declarations = text.split("## Decision logic", 1)[0]
    return re.sub(r"```gherkin.*?```", "", declarations, flags=re.S)


def _check_placeholders(text, rep):
    for lineno, ln in _lines_with_numbers(text):
        if ln.lstrip().startswith("<!--"):
            continue
        for m in _PLACEHOLDER.finditer(ln):
            rep.add("S_PLACEHOLDER", "BLOCK",
                    f"unfilled placeholder {m.group(0)!r} — the coder would invent this. "
                    f"Replace with a concrete value.", lineno)


def _check_requirement_completeness(text, rep):
    reqs = _requirement_lines(text)
    if not reqs:
        rep.add("S_NO_REQS", "BLOCK", "no requirement bullets found in MUST/Requirements.")
        return
    for lineno, ln in reqs:
        body = ln.lstrip("- ").strip()
        low = body.lower()
        if not _EARS_HEAD.match(ln):
            rep.add("S_REQ_NOT_EARS", "BLOCK",
                    f"requirement not EARS-shaped (start with The system shall / When / While / "
                    f"If / Where): {body!r}", lineno)
        toks = set(re.findall(r"[a-z']+", low))
        if not (toks & _OUTCOME_VERBS):
            rep.add("S_NO_OUTCOME_VERB", "BLOCK",
                    f"no concrete outcome verb (return/reject/store/emit/…). "
                    f"'shall support/handle X' is not testable: {body!r}", lineno)
        for vt in sorted(_VAGUE_TERMS):
            if re.search(rf"\b{re.escape(vt)}\b", low):
                rep.add("S_VAGUE_TERM", "BLOCK",
                        f"vague term {vt!r} — the coder must guess its meaning. State the exact "
                        f"behaviour/threshold: {body!r}", lineno)
                break
        if _DEICTIC.search(low) and not _LITERAL.search(body):
            rep.add("S_DANGLING_REF", "BLOCK",
                    f"underspecified referent (it/this/that) with no named entity: {body!r}", lineno)
        if re.match(r"^\s*-?\s*(When|If|While|Where)\b", body, re.I):
            if "shall" not in low and "then" not in low:
                rep.add("S_COND_NO_RESPONSE", "BLOCK",
                        f"conditional names a trigger but no response ('…, the system shall …'): "
                        f"{body!r}", lineno)
        if any(w in low for w in ("timeout", "limit", "retry", "expire", "within",
                                  "rate", "size", "length", "latency", "duration")):
            if not _MEASURABLE.search(body):
                rep.add("S_UNQUANTIFIED_BOUND", "BLOCK",
                        f"implies a bound (timeout/limit/retry/size) but states no number+unit: "
                        f"{body!r}", lineno)


def _check_gherkin_traceability(text, rep):
    # Declared terms must come from requirements/data-model/roles — NOT from the
    # acceptance block itself, or a value could self-declare by appearing only in
    # Gherkin. Decision rows are validators, not declarations, so strip them too.
    declared = _declared_terms(_declaration_text(text))
    if "```gherkin" not in text:
        rep.add("S_NO_GHERKIN", "BLOCK", "no Gherkin acceptance scenario.")
        return
    has_given = has_then = False
    for lineno, ln in _lines_with_numbers(text):
        s = ln.strip()
        if re.match(r"^(Given|When|Then|And|But)\b", s):
            if s.startswith("Given"): has_given = True
            if s.startswith("Then"):  has_then = True
            for m in _LITERAL.finditer(s):
                val = next(g for g in m.groups() if g).strip().lower()
                if len(val) < 3:
                    continue
                if val not in declared and not _MEASURABLE.search(val):
                    rep.add("S_UNTRACEABLE_STEP", "BLOCK",
                            f"acceptance step references {val!r}, not defined in any requirement "
                            f"or data model — declare it upstream or remove it.", lineno)
    if not (has_given and has_then):
        rep.add("S_GHERKIN_SHAPE", "BLOCK",
                "Gherkin scenario must have at least one Given and one Then.")


def _check_decision_determinism(text, rep):
    if "## Decision logic" not in text:
        return
    declared = _declared_terms(_declaration_text(text))
    section = text.split("## Decision logic", 1)[1]
    base_line = text[:text.find("## Decision logic")].count("\n") + 1
    seen = []
    for i, ln in enumerate(section.splitlines()):
        m = re.match(r"\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", ln)
        if not m:
            continue
        lineno = base_line + i
        n, cond, outcome = m.group(1), m.group(2).strip(), m.group(3).strip()
        clow, olow = cond.lower(), outcome.lower()
        if cond in ("if", "----", "---") or set(cond) <= {"-"}:
            continue
        # A catch-all default row (else/otherwise/default/any) is legitimate and
        # references no specific fact by design — it's the deterministic fallback.
        if clow.strip("` ") in ("else", "otherwise", "default", "any", "*"):
            seen.append(re.sub(r"\s+", " ", clow))
            continue
        cond_terms = {next(g for g in mm.groups() if g).strip().lower()
                      for mm in _LITERAL.finditer(cond)}
        if not cond_terms & declared and not _MEASURABLE.search(cond):
            rep.add("S_RULE_UNDEFINED_FACT", "BLOCK",
                    f"rule #{n} condition {cond!r} references no declared fact. Rules operate over "
                    f"declared facts, not new nouns.", lineno)
        if re.search(r"\b(maybe|might|could|should probably|or|and/or|etc)\b", olow):
            rep.add("S_RULE_NONDETERMINISTIC", "BLOCK",
                    f"rule #{n} outcome {outcome!r} is non-deterministic (maybe/or/etc). One "
                    f"condition → exactly one outcome.", lineno)
        # Vague terms in an outcome only count as prose, not as part of a
        # Capitalized:Label (e.g. 'APPROVED: Small With Receipt' — 'Small' is a
        # label token, not a vague quantifier). Strip label-cased words first.
        outcome_prose = re.sub(r"\b[A-Z][A-Za-z]*\b", "", outcome).lower()
        if any(re.search(rf"\b{re.escape(vt)}\b", outcome_prose) for vt in _VAGUE_TERMS):
            rep.add("S_RULE_VAGUE_OUTCOME", "BLOCK",
                    f"rule #{n} outcome {outcome!r} is vague. State the exact action.", lineno)
        norm = re.sub(r"\s+", " ", clow)
        if norm in seen:
            rep.add("S_RULE_DUP_CONDITION", "BLOCK",
                    f"rule #{n} duplicates condition {cond!r} — ambiguous dispatch. Conditions "
                    f"must be mutually exclusive.", lineno)
        seen.append(norm)


def _check_approved_consistency(text, rep):
    if re.search(r"^Status:\s*approved", text, re.M) and rep.blocks:
        rep.add("S_APPROVED_BUT_AMBIGUOUS", "BLOCK",
                "spec marked approved while failing strict checks — approval is a lie until the "
                "blocks above are resolved.")


def strict_validate_text(text: str) -> StrictReport:
    rep = StrictReport()
    _check_placeholders(text, rep)
    _check_requirement_completeness(text, rep)
    _check_gherkin_traceability(text, rep)
    _check_decision_determinism(text, rep)
    _check_approved_consistency(text, rep)
    return rep


def strict_validate(path: Path) -> StrictReport:
    return strict_validate_text(Path(path).read_text())


def strict_errors(path: Path) -> list[str]:
    return [str(f) for f in strict_validate(path).blocks]
