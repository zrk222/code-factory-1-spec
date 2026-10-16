import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOOD_SPEC = """# Spec: refunds
Status: approved

## MUST — Functional core
### Description
Customers request refunds; the system screens them.

### User roles
- customer
- support agent

### Requirements (EARS)
- The system shall accept refund requests with an order id and reason text.
- When a request arrives, the system shall extract amount and receipt status.
- If extraction fails, the system shall route the request to a human.
- While a request is pending, the system shall show its status to the customer.

### Acceptance criteria (Gherkin)
```gherkin
Scenario: damaged item auto-approval
  Given a refund request for a damaged item
  When the request is screened
  Then the decision is APPROVED with reason Damaged Item
```

## SHOULD — Technical/structural
- Data model: RefundRequest(order_id, reason, amount, has_receipt, item_damaged)
- Decision outcomes: `APPROVED`, `HUMAN_REVIEW`

## SHOULD NOT — Implementation details

## Decision logic (factory candidates)
| # | if | then |
|---|----|------|
| 1 | item_damaged == true | APPROVED: Damaged Item |
| 2 | has_receipt == true and amount < 100 | APPROVED: Small With Receipt |
| 3 | else | HUMAN_REVIEW: Manual screen |
"""

GOOD_PLAN = """# Plan: refunds
Spec: specs/refunds.md
Architect verdict: PASS

## Logical decomposition (phases)
1. request intake slice

## Tasks (atomic — each independently shippable)
- [ ] T1 | slice=slices/refunds | files=slices/refunds/intake.py,tests/test_intake.py | verify=`python -c "print('ok')"` | Build the intake handler storing RefundRequest records
- [ ] T2 | slice=slices/refunds | files=slices/refunds/status.py,tests/test_status.py | verify=`python -c "print('ok')"` | Expose pending-status view for customers
"""

@pytest.fixture()
def project(tmp_path):
    from specline.scaffold import init_project
    init_project(tmp_path)
    (tmp_path/"specs"/"refunds.md").write_text(GOOD_SPEC)
    (tmp_path/"plans"/"refunds.md").write_text(GOOD_PLAN)
    return tmp_path


# Optional three-tool acceptance wiring fixture; ordinary SpecLine stays standalone.
import hashlib
import json
from datetime import datetime, timezone
from specline.acceptance import catalogue, digest
from specline.gates import gate
from specline.loop import mark_done

def _acceptance_artifact(root, relative, kind="source"):
    return {"path": relative, "sha256": hashlib.sha256((root / relative).read_bytes()).hexdigest(), "kind": kind}

@pytest.fixture
def evidence_project(project):
    pytest.importorskip("factoryline.workflow_audit")
    sources = {
        "slices/refunds/intake.py": "def accept(value):\n    return value\n",
        "slices/refunds/status.py": "def status():\n    return 'pending'\n",
        "tests/test_intake.py": "def test_accept():\n    from slices.refunds.intake import accept\n    assert accept(7) == 7\n",
        "tests/test_status.py": "def test_status():\n    from slices.refunds.status import status\n    assert status() == 'pending'\n",
    }
    for name, text in sources.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    plan = project / "plans/refunds.md"
    text = plan.read_text().replace(
        "python -c \"print('ok')\"",
        "python -c \"from slices.refunds.intake import accept; assert accept(7) == 7\"", 1,
    ).replace(
        "python -c \"print('ok')\"",
        "python -c \"from slices.refunds.status import status; assert status() == 'pending'\"", 1,
    )
    plan.write_text(text)
    gate(project, "plan", "refunds", strict=False)
    assert mark_done(project, "refunds", "T1")["verified"]
    assert mark_done(project, "refunds", "T2")["verified"]
    directory = project / ".factory"
    review = directory / "acceptance-review.md"
    review.write_text("Local wiring fixture: other categories not applicable. Does not measure semantic correctness.")
    candidates = [_acceptance_artifact(project, name) for name in [*sources, "specs/refunds.md", "plans/refunds.md"]]
    categories = ["happy_path", "validation", "failure_recovery", "state_consistency", "security_privacy", "performance", "accessibility", "external_effects"]
    checks = [{"id": item["id"], "requirement": item["text"], "category": "happy_path", "profiles": ["local-python"]} for item in catalogue(project, "refunds")]
    contract = {"schema": "factory.workflow-audit.contract.v1", "project_id": "refunds", "candidate": candidates,
                "applicability": [{"category": category, "applicable": category == "happy_path", "reason": "Local wiring fixture.", **({"review_evidence": _acceptance_artifact(project, ".factory/acceptance-review.md", "review")} if category != "happy_path" else {})} for category in categories], "checks": checks}
    contract_path = directory / "workflow-audit-contract.json"
    contract_path.write_text(json.dumps(contract))
    observations = {"schema": "factory.workflow-audit.observations.v1", "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(), "candidate_sha256": digest(candidates),
                    "observations": [{"id": item["id"], "profile": "local-python", "status": "PASS", "reason": "Recorded task execution; declared fixture link, not semantic proof.", "execution_identity": "local Python subprocess assertions", "observed_at": datetime.now(timezone.utc).isoformat(), "artifacts": [_acceptance_artifact(project, f".factory/specline/refunds/{task}.json", "report") for task in ("T1", "T2")]} for item in checks]}
    (directory / "workflow-audit-observations.json").write_text(json.dumps(observations))
    return project, ".factory/workflow-audit-contract.json", ".factory/workflow-audit-observations.json"
