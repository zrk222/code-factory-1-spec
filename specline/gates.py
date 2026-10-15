"""Human review gates with receipts. A gate signoff writes a hash-sealed
line into PROGRESS.md; the loop and drift guard consume those seals."""
from __future__ import annotations
import hashlib, datetime, json, os, re, tempfile
from pathlib import Path
from .spec_lint import validate_spec
from .plan_lint import lint_plan
from .strict_lint import strict_errors


def atomic_text(path: Path, text: str, encoding: str | None = None):
    """Replace one local receipt/projection without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".specline-")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def receipt_path(root: Path, feature: str, name: str = "approval") -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", feature):
        raise SystemExit("E_FEATURE_ID: invalid feature identity")
    return Path(root) / ".factory" / "specline" / feature / f"{name}.json"


def seal_receipt(path: Path, value: dict) -> dict:
    value = dict(value)
    value["seal"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    atomic_text(path, json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
    return value


def read_receipt(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        seal = value.pop("seal")
        expected = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if seal != expected:
            raise ValueError("seal mismatch")
        return value | {"seal": seal}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SystemExit("E_RECEIPT_INVALID: corrupt local receipt") from exc


def plan_identity(root: Path, feature: str) -> dict:
    receipt_path(root, feature)
    root = Path(root).resolve()
    spec = root / "specs" / f"{feature}.md"
    plan = (root / "plans" / f"{feature}.md").read_text()
    # Completion checkboxes are a projection, not a change in task authority.
    plan = re.sub(r"(?m)^- \[[ x]\] (T\d+ \|)", r"- [ ] \1", plan)
    return {"repository": str(root), "feature": feature,
            "spec_sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest()}


def require_plan_approval(root: Path, feature: str) -> dict:
    approval = read_receipt(receipt_path(root, feature))
    if approval is None:
        raise SystemExit("plan gate missing: re-gate the plan to create a bound approval receipt")
    identity = plan_identity(root, feature)
    if approval.get("schema") != "specline.plan-approval.v2" or any(
        approval.get(key) != value for key, value in identity.items()
    ):
        raise SystemExit("E_INTENT_DRIFT: spec, plan or repository changed. Re-gate the plan.")
    return approval

def _seal(root: Path, line: str):
    p = Path(root)/"context"/"PROGRESS.md"
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    atomic_text(p, p.read_text() + f"\n- [{ts}] {line}")

def gate(root: Path, phase: str, feature: str, approver: str = "human", strict: bool = True) -> dict:
    root = Path(root)
    receipt_path(root, feature)
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
        approval = seal_receipt(receipt_path(root, feature), {
            "schema": "specline.plan-approval.v2", **plan_identity(root, feature),
            "approver": approver, "strict": strict,
            "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "authority": "local-integrity-only",
        })
        _seal(root, f"GATE plan {feature} approver={approver} tasks={len(tasks)} sha={sha}")
        return {"gate": "plan", "tasks": len(tasks), "sha": sha, "approval_seal": approval["seal"]}
    if phase == "code":
        _seal(root, f"GATE code {feature} approver={approver} reviewer=personas/reviewer.md auditor=personas/security_auditor.md")
        return {"gate": "code"}
    raise SystemExit(f"unknown gate {phase}")
