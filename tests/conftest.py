import sys, subprocess
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
