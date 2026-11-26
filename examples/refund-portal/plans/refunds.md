# Plan: refunds
Spec: specs/refunds.md
Architect verdict: PASS

## Logical decomposition (phases)
1. screening slice

## Tasks (atomic — each independently shippable)
- [ ] T1 | slice=slices/refunds | files=slices/refunds/screen.py,tests/test_screen.py | verify=`python -c "print('ok')"` | Screen a refund request against the decision table
- [ ] T2 | slice=slices/refunds | files=slices/refunds/fallback.py,tests/test_fallback.py | verify=`python -c "print('ok')"` | Route unmatched requests to Manual screen
