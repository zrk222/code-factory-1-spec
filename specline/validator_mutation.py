"""Reverse-classical mutation checks for SpecLine validators.

Strict lint proves the original spec is well-shaped. This layer mutates one
requirement at a time and requires strict lint to notice. If deleting or
inverting a requirement still passes, that requirement has no observable
validator and the gate itself is hollow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from .strict_lint import Finding, _requirement_lines, strict_validate_text


_STRUCTURAL_ONLY_CODES = {"S_NO_REQS", "S_APPROVED_BUT_AMBIGUOUS"}


@dataclass(frozen=True)
class RequirementMutation:
    index: int
    line: int
    phrase: str
    passed: bool
    evidence: str


@dataclass
class ValidatorMutationReport:
    requirements: list[RequirementMutation] = field(default_factory=list)
    base_findings: list[Finding] = field(default_factory=list)

    @property
    def blocks(self) -> list[Finding]:
        findings = list(self.base_findings)
        for result in self.requirements:
            if not result.passed:
                findings.append(Finding(
                    "S_HOLLOW_VALIDATOR",
                    "BLOCK",
                    result.evidence,
                    result.line,
                ))
        return findings

    @property
    def ok(self) -> bool:
        return not self.blocks

    def attribution(self):
        from .attribution import Attribution, FailureClass, UnitResult

        units = [
            UnitResult(
                unit=f"R{result.index}",
                stage="validator_mutation",
                passed=result.passed,
                evidence=result.evidence,
                failure_class=None if result.passed else FailureClass.HOLLOW_VALIDATOR,
            )
            for result in self.requirements
        ]
        return Attribution("validator_mutation", len(units), sum(u.passed for u in units), units)


def _remove_line(text: str, line_number: int) -> str:
    lines = text.splitlines()
    del lines[line_number - 1]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _replace_line(text: str, line_number: int, replacement: str) -> str:
    lines = text.splitlines()
    lines[line_number - 1] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _invert_requirement(line: str) -> str | None:
    literal = re.search(r"`([^`]+)`", line)
    if literal:
        value = literal.group(1)
        mutated = str(int(value) + 1) if value.isdigit() else f"{value}_MUTANT"
        return line.replace(f"`{value}`", f"`{mutated}`")
    quoted = re.search(r"(['\"])([^'\"]+)\1", line)
    if quoted:
        value = quoted.group(2)
        mutated = str(int(value) + 1) if value.isdigit() else f"{value}_MUTANT"
        quote = quoted.group(1)
        return line.replace(f"{quote}{value}{quote}", f"{quote}{mutated}{quote}")
    match = re.search(r"\b(\d+)\b", line)
    if match:
        value = str(int(match.group(1)) + 1)
        return line[:match.start(1)] + value + line[match.end(1):]
    if re.search(r"\btrue\b", line, re.I):
        return re.sub(r"\btrue\b", "false", line, count=1, flags=re.I)
    if re.search(r"\bfalse\b", line, re.I):
        return re.sub(r"\bfalse\b", "true", line, count=1, flags=re.I)
    if re.search(r"\bshall\s+not\b", line, re.I):
        return re.sub(r"\bshall\s+not\s+", "shall ", line, count=1, flags=re.I)
    if re.search(r"\bshall\s+[a-z]", line, re.I):
        return re.sub(r"\bshall\s+", "shall not ", line, count=1, flags=re.I)
    return None


def _killed_by_validator(report) -> bool:
    return any(f.code not in _STRUCTURAL_ONLY_CODES for f in report.blocks)


def _summarize(report) -> str:
    codes = [f.code for f in report.blocks if f.code not in _STRUCTURAL_ONLY_CODES]
    return ", ".join(codes[:3]) if codes else "no validator-specific block"


def verify_validators(path: Path) -> ValidatorMutationReport:
    text = Path(path).read_text()
    base = strict_validate_text(text)
    if base.blocks:
        return ValidatorMutationReport(base_findings=base.blocks)

    results: list[RequirementMutation] = []
    for index, (line, phrase) in enumerate(_requirement_lines(text), 1):
        deletion_report = strict_validate_text(_remove_line(text, line))
        deletion_killed = _killed_by_validator(deletion_report)

        inverted = _invert_requirement(phrase)
        inversion_killed = True
        inversion_summary = "not invertible"
        if inverted is not None:
            inversion_report = strict_validate_text(_replace_line(text, line, inverted))
            inversion_killed = _killed_by_validator(inversion_report)
            inversion_summary = _summarize(inversion_report)

        passed = deletion_killed and inversion_killed
        if passed:
            evidence = (
                f"deletion killed by {_summarize(deletion_report)}; "
                f"inversion killed by {inversion_summary}"
            )
        else:
            failures = []
            if not deletion_killed:
                failures.append("deletion mutant survived strict")
            if not inversion_killed:
                failures.append("inversion mutant survived strict")
            evidence = f"{phrase.strip()} :: " + "; ".join(failures)
        results.append(RequirementMutation(index, line, phrase, passed, evidence))

    return ValidatorMutationReport(results)
