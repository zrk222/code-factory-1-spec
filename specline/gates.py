"""Human review gates with receipts. A gate signoff writes a hash-sealed
line into PROGRESS.md; the loop and drift guard consume those seals."""
from __future__ import annotations
import hashlib, datetime
from pathlib import Path
from .spec_lint import validate_spec
from .plan_lint import lint_plan
from .strict_lint import strict_errors

def _seal(root: Path, line: str):
    p = Path(root)/"context"/"PROGRESS.md"
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    p.write_text(p.read_text() + f"\n- [{ts}] {line}")

def gate(root: Path, phase: str, feature: str, approver: str = "human", strict: bool = True) -> dict:
    root = Path(root)
    spec = root/"specs"/f"{feature}.md"
    if phase == "spec":
        errs = validate_spec(spec)
        if strict:
            errs = errs + strict_errors(spec)
        if errs:
            raise SystemExit("spec gate FAILED:\n" + "\n".join(errs))
        sha = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
        _seal(root, f"GATE spec {feature} approver={approver} strict={strict} sha={sha}")
        return {"gate": "spec", "sha": sha}
    if phase == "plan":
        errsS = validate_spec(spec)
        if strict:
            errsS = errsS + strict_errors(spec)
        tasks, errsP = lint_plan(root/"plans"/f"{feature}.md")
        if errsS or errsP:
            raise SystemExit("plan gate FAILED:\n" + "\n".join(errsS + errsP))
        sha = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
        _seal(root, f"GATE plan {feature} approver={approver} tasks={len(tasks)} sha={sha}")
        return {"gate": "plan", "tasks": len(tasks), "sha": sha}
    if phase == "code":
        _seal(root, f"GATE code {feature} approver={approver} reviewer=personas/reviewer.md auditor=personas/security_auditor.md")
        return {"gate": "code"}
    raise SystemExit(f"unknown gate {phase}")
