"""Deterministic PRD optimizer.

The optimizer does not rewrite a product requirement document by magic. It
turns senior-review habits into a scored, auditable checklist so the next agent
or engineer knows exactly what must be tightened before implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


AMBIGUOUS = {
    "fast", "quick", "simple", "easy", "robust", "scalable", "nice",
    "intuitive", "seamless", "soon", "later", "etc", "maybe", "support",
}
PROOF_TERMS = ("test", "golden", "validator", "receipt", "metric", "assert", "acceptance")
RISK_TERMS = ("risk", "failure", "security", "privacy", "rollback", "dependency", "cost")
SCOPE_TERMS = ("non-goal", "out of scope", "not included", "will not")
USER_TERMS = ("user", "customer", "operator", "admin", "engineer", "clinician", "reviewer")


@dataclass(frozen=True)
class PRDScore:
    path: str
    score: int
    grade: str
    passed: bool
    findings: list[dict]
    recommendations: list[str]
    requirement_count: int
    acceptance_count: int
    ambiguity_count: int

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": self.score,
            "grade": self.grade,
            "passed": self.passed,
            "requirement_count": self.requirement_count,
            "acceptance_count": self.acceptance_count,
            "ambiguity_count": self.ambiguity_count,
            "findings": self.findings,
            "recommendations": self.recommendations,
        }


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _requirements(lines: list[str]) -> list[str]:
    reqs = []
    for line in lines:
        low = line.lower()
        if re.search(r"\b(shall|must|should|given|when|then)\b", low):
            reqs.append(line)
        elif re.match(r"^[-*]\s+\[[ x]\]", low):
            reqs.append(line)
    return reqs


def _count_terms(text: str, terms: tuple[str, ...] | set[str]) -> int:
    low = text.lower()
    return sum(1 for term in terms if term in low)


def _ambiguous_hits(text: str) -> list[str]:
    words = set(re.findall(r"[a-zA-Z][a-zA-Z-]+", text.lower()))
    return sorted(words & AMBIGUOUS)


def optimize_prd(path: Path) -> PRDScore:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    reqs = _requirements(lines)
    acceptance = [line for line in lines if re.search(r"\b(given|when|then|acceptance|assert|expected)\b", line.lower())]
    ambiguities = _ambiguous_hits(text)
    checks = [
        ("outcome", bool(_count_terms(text, USER_TERMS)), "Name the target user and the outcome they need."),
        ("requirements", len(reqs) >= 2, "Add at least two explicit shall/must/Gherkin requirements."),
        ("acceptance", len(acceptance) >= 1, "Add executable acceptance criteria or Gherkin examples."),
        ("proof", _count_terms(text, PROOF_TERMS) >= 1, "Name the test, validator, receipt, or metric that proves success."),
        ("scope", _count_terms(text, SCOPE_TERMS) >= 1, "Add non-goals so agents know what not to build."),
        ("risk", _count_terms(text, RISK_TERMS) >= 1, "Add a risk/failure/rollback section before coding."),
        ("ambiguity", len(ambiguities) == 0, "Replace vague terms with measurable thresholds."),
    ]
    passed_checks = sum(1 for _, ok, _ in checks if ok)
    score = round((passed_checks / len(checks)) * 100)
    findings = [
        {"criterion": name, "passed": ok, "message": "OK" if ok else message}
        for name, ok, message in checks
    ]
    if ambiguities:
        findings.append({"criterion": "ambiguity_terms", "passed": False, "message": ", ".join(ambiguities)})
    recommendations = [message for _, ok, message in checks if not ok]
    if reqs and not any("test" in req.lower() or "validator" in req.lower() for req in reqs):
        recommendations.append("Map every requirement to at least one test or validator row.")
    return PRDScore(
        path=str(path),
        score=score,
        grade=_grade(score),
        passed=score >= 80 and not ambiguities,
        findings=findings,
        recommendations=recommendations,
        requirement_count=len(reqs),
        acceptance_count=len(acceptance),
        ambiguity_count=len(ambiguities),
    )


def render_prd_score(report: PRDScore) -> str:
    lines = [
        "PRD OPTIMIZATION REPORT",
        "-" * 52,
        f"path                 : {report.path}",
        f"score                : {report.score}/100 (grade {report.grade})",
        f"ready                : {report.passed}",
        f"requirements         : {report.requirement_count}",
        f"acceptance criteria  : {report.acceptance_count}",
        f"ambiguity terms      : {report.ambiguity_count}",
        "",
        "FINDINGS",
        "-" * 52,
    ]
    for finding in report.findings:
        mark = "OK" if finding["passed"] else "BLOCK"
        lines.append(f"{mark:<6} {finding['criterion']}: {finding['message']}")
    if report.recommendations:
        lines.extend(["", "RECOMMENDED PRD EDITS", "-" * 52])
        lines.extend(f"- {rec}" for rec in report.recommendations)
    lines.extend(["", "NEXT", "-" * 52, "Run strict validation and validator mutation before implementation."])
    return "\n".join(lines)
