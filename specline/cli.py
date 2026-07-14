"""specline CLI — the production line, end to end:
init -> new -> (write spec) -> validate -> gate spec -> (write plan) ->
tasks -> gate plan -> loop next/done ... -> gate code -> handoff -> status
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path


def _emit_version(as_json: bool) -> None:
    from .provenance import provenance
    payload = provenance()
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else f"specline {payload['version']}")

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--version":
        _emit_version("--json" in argv)
        return
    p = argparse.ArgumentParser(prog="specline", description="Spec-driven production line for AI coding agents")
    sub = p.add_subparsers(required=True, dest="cmd")

    s = sub.add_parser("init", help="scaffold constitution + six-file context system")
    s.add_argument("--root", default=".")

    s = sub.add_parser("new", help="create spec+plan skeleton for a feature")
    s.add_argument("feature"); s.add_argument("--root", default=".")

    s = sub.add_parser("validate", help="lint a spec (EARS/Gherkin/leaks)")
    s.add_argument("feature"); s.add_argument("--root", default=".")

    s = sub.add_parser("strict", help="STRICT semantic input contract — reject ambiguity before the coder")
    s.add_argument("feature"); s.add_argument("--root", default=".")
    s.add_argument("--warn", action="store_true", help="also show WARN-level smells")
    s.add_argument("--json", action="store_true", help="emit machine-readable attribution")

    s = sub.add_parser("verify-validators", help="mutate requirements and prove strict validators catch drift")
    s.add_argument("feature"); s.add_argument("--root", default=".")
    s.add_argument("--json", action="store_true", help="emit machine-readable attribution")

    s = sub.add_parser("challenge", help="write a standard proof-by-sabotage validator receipt")
    s.add_argument("feature"); s.add_argument("--root", default=".")
    s.add_argument("--out", default=None)

    s = sub.add_parser("audit", help="post-code drift audit (invented params / scope escape / stubs)")
    s.add_argument("feature"); s.add_argument("--root", default=".")
    s.add_argument("--files", nargs="+", required=True, help="changed files to audit")
    s.add_argument("--slice", default=None, help="authorized slice prefix")
    s.add_argument("--json", action="store_true", help="emit machine-readable attribution")

    s = sub.add_parser("tasks", help="lint plan task atomicity")
    s.add_argument("feature"); s.add_argument("--root", default=".")

    s = sub.add_parser("gate", help="record a human gate signoff (spec|plan|code)")
    s.add_argument("phase", choices=["spec", "plan", "code"]); s.add_argument("feature")
    s.add_argument("--approver", default="human"); s.add_argument("--root", default=".")

    s = sub.add_parser("loop", help="Ralph Wiggum loop: next | done")
    s.add_argument("action", choices=["next", "done"]); s.add_argument("feature")
    s.add_argument("task_id", nargs="?"); s.add_argument("--no-verify", action="store_true")
    s.add_argument("--root", default=".")

    s = sub.add_parser("handoff", help="emit HSF workflow spec from decision table")
    s.add_argument("feature"); s.add_argument("--root", default=".")

    s = sub.add_parser("agent", help="wire into an agent harness (claude|codex|<name>)")
    s.add_argument("name"); s.add_argument("--root", default=".")

    s = sub.add_parser("specfactor", help="spec/code ratio gauge")
    s.add_argument("--root", default=".")

    s = sub.add_parser("status", help="progress + token-savings receipt")
    s.add_argument("--root", default=".")

    s = sub.add_parser("optimize-prd", help="score a PRD before implementation")
    s.add_argument("path", help="PRD/spec markdown path")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("version", help="show package provenance")
    s.add_argument("--json", action="store_true")

    a = p.parse_args(argv)
    root = Path(getattr(a, "root", "."))

    if a.cmd == "version":
        _emit_version(a.json)
    elif a.cmd == "init":
        from .scaffold import init_project
        created = init_project(root)
        print("created:\n  " + "\n  ".join(str(c) for c in created))
        print("next: specline new <feature>")
    elif a.cmd == "new":
        from .scaffold import new_feature
        spec, plan = new_feature(root, a.feature)
        print(f"spec: {spec}\nplan: {plan}\nnext: fill the spec, then `specline validate {a.feature}`")
    elif a.cmd == "validate":
        from .spec_lint import validate_spec
        errs = validate_spec(root/"specs"/f"{a.feature}.md")
        if errs: raise SystemExit("INVALID:\n" + "\n".join(errs))
        print("spec OK — next: specline strict " + a.feature)
    elif a.cmd == "strict":
        from .strict_lint import strict_validate
        spec_path = root/"specs"/f"{a.feature}.md"
        rep = strict_validate(spec_path)
        if a.json:
            print(json.dumps({"passed": rep.ok,
                              "attribution": rep.attribution(spec_path.read_text()).to_dict()},
                             indent=2))
        for f in (rep.findings if a.warn else rep.blocks):
            print(str(f))
        if rep.blocks:
            raise SystemExit(f"\nSTRICT FAILED: {len(rep.blocks)} blocking ambiguity(ies) — "
                             f"the coder would guess these. Fix before gating.")
        print(f"STRICT OK — spec is unambiguous ({len(rep.warns)} warns). "
              f"next: specline gate spec {a.feature}")
    elif a.cmd == "verify-validators":
        from .validator_mutation import verify_validators
        spec_path = root/"specs"/f"{a.feature}.md"
        rep = verify_validators(spec_path)
        if a.json:
            print(json.dumps({"passed": rep.ok,
                              "attribution": rep.attribution().to_dict(),
                              "findings": [str(f) for f in rep.blocks]},
                             indent=2))
        for f in rep.blocks:
            print(str(f))
        if rep.blocks:
            raise SystemExit(f"\nVALIDATOR MUTATION FAILED: {len(rep.blocks)} hollow validator(s) detected.")
        print(f"VALIDATORS OK - all {len(rep.requirements)} requirement mutation(s) were killed.")
    elif a.cmd == "challenge":
        from .validator_mutation import verify_validators
        rep = verify_validators(root / "specs" / f"{a.feature}.md")
        attr = rep.attribution().to_dict()
        payload = {
            "schema": "factory.challenge.v1",
            "brick": "specline",
            "feature": a.feature,
            "stage": "validator_mutation",
            "passed": rep.ok and attr["n_checked"] > 0,
            "mutants_total": attr["n_checked"],
            "mutants_killed": attr["n_passed"],
            "attribution": attr,
        }
        out = Path(a.out) if a.out else root / ".specline" / a.feature / "challenge.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(payload | {"receipt_path": str(out)}, indent=2))
        if not payload["passed"]:
            raise SystemExit(1)
    elif a.cmd == "audit":
        from .drift_audit import audit_code_against_spec, audit_report_lines
        rep = audit_code_against_spec(root/"specs"/f"{a.feature}.md",
                                      [Path(f) for f in a.files], slice_prefix=a.slice)
        if a.json:
            print(json.dumps({"passed": rep.ok,
                              "attribution": rep.attribution([Path(f) for f in a.files]).to_dict()},
                             indent=2))
        for line in audit_report_lines(rep):
            print(line)
        if rep.blocks:
            raise SystemExit(f"\nAUDIT FAILED: {len(rep.blocks)} drift(s) detected.")
        print(f"AUDIT OK — no drift ({len(rep.warns)} warns).")
    elif a.cmd == "tasks":
        from .plan_lint import lint_plan
        tasks, errs = lint_plan(root/"plans"/f"{a.feature}.md")
        if errs: raise SystemExit("PLAN INVALID:\n" + "\n".join(errs))
        print(f"{len(tasks)} atomic tasks OK — next: specline gate plan " + a.feature)
    elif a.cmd == "gate":
        from .gates import gate
        print(json.dumps(gate(root, a.phase, a.feature, a.approver)))
    elif a.cmd == "loop":
        from .loop import next_task, mark_done
        if a.action == "next":
            print(json.dumps(next_task(root, a.feature), indent=2))
        else:
            if not a.task_id: raise SystemExit("task_id required for done")
            print(json.dumps(mark_done(root, a.feature, a.task_id, run_verify=not a.no_verify)))
    elif a.cmd == "handoff":
        from .handoff import handoff_to_hsf
        out = handoff_to_hsf(root, a.feature)
        print(f"HSF spec: {out}\ncompile with: hsf compile {out}")
    elif a.cmd == "agent":
        from .adapters import wire_agent
        created = wire_agent(root, a.name)
        print("wired:\n  " + "\n  ".join(str(c) for c in created))
    elif a.cmd == "specfactor":
        from .specfactor import specfactor
        print(json.dumps(specfactor(root), indent=2))
    elif a.cmd == "status":
        from .ledger import summarize
        s = summarize(root)
        print(json.dumps(s, indent=2))
        if s["sessions"]:
            print(f"packet estimate: {s['packet_tokens']:,} tokens; modeled baseline: "
                  f"{s['naive_tokens']:,}; modeled difference: {s['saved_pct']}%")
    elif a.cmd == "optimize-prd":
        from .prd_optimizer import optimize_prd, render_prd_score
        rep = optimize_prd(Path(a.path))
        print(json.dumps(rep.to_dict(), indent=2) if a.json else render_prd_score(rep))
        if not rep.passed:
            raise SystemExit(1)

if __name__ == "__main__":
    main()
