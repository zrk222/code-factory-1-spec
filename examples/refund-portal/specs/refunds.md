# Spec: refunds
Status: approved

## MUST — Functional core
### Description
Screens refund requests for the Customer role.

### User roles
- Customer

### Requirements (EARS)
- When `item_damaged` is true, the system shall return `Damaged Item`.
- When `has_receipt` is true and `amount` is below 100 dollars, the system shall return `Receipt Under Limit`.
- If no rule matches, the system shall return `Manual screen`.

### Acceptance criteria (Gherkin)
```gherkin
Scenario: damaged item auto-approval
  Given a refund request where `item_damaged` is true
  When the request is screened
  Then the system returns `Damaged Item`

Scenario: refund with receipt under the limit
  Given a refund request where `has_receipt` is true and `amount` is 40 dollars
  When the request is screened
  Then the system returns `Receipt Under Limit`

Scenario: fallback
  Given a refund request matching no rule
  When the request is screened
  Then the system returns `Manual screen`
```

## SHOULD NOT — Implementation details

## Non-goals
- Issuing the refund payment; this feature only screens the request.
- Fraud scoring beyond the receipt and damage facts.

## Risks and rollback
- Risk: the 100 dollar limit is set too high and approves refunds that need review.
- Rollback: revert the decision table so every request goes to a human reviewer.

## Decision logic (factory candidates)
| # | if | then |
|---|----|------|
| 1 | item_damaged == true | APPROVED: Damaged Item |
| 2 | has_receipt == true and amount < 100 | APPROVED: Receipt Under Limit |
| 3 | else | HUMAN_REVIEW: Manual screen |
