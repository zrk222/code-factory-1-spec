# Example: refund-portal

A worked spec and plan that pass every step of the README quickstart, including `strict`,
`verify-validators`, `optimize-prd`, `challenge` and HSF's own loader on the handoff output.

```bash
bash examples/refund-portal/walkthrough.sh          # fresh temp dir
bash examples/refund-portal/walkthrough.sh ./demo   # or a directory you choose
```

Design notes worth copying:

- Every requirement quotes the exact outcome it returns (`Damaged Item`), and each scenario traces to one.
- There is no `Data model:` line repeating the requirement terms. If the terms are declared twice, deleting a
  requirement leaves nothing untraceable and `verify-validators` reports every validator as hollow.
- `## Non-goals` and `## Risks and rollback` sit after the MUST section and keep requirement literals out, for
  the same reason.
