import json, re
from pathlib import Path
import pytest

def test_init_creates_six_file_context(tmp_path):
    from specline.scaffold import init_project
    created = init_project(tmp_path)
    names = {p.name for p in (tmp_path/"context").glob("*.md")}
    assert names == {"PROJECT_OVERVIEW.md","ARCHITECTURE.md","CODE_STANDARDS.md",
                     "AI_WORKFLOW_RULES.md","UI_CONTEXT.md","PROGRESS.md"}
    assert (tmp_path/"AGENTS.md").exists()

def test_spec_lint_catches_vibe_specs(project):
    from specline.spec_lint import validate_spec
    assert validate_spec(project/"specs"/"refunds.md") == []
    bad = project/"specs"/"bad.md"
    bad.write_text("# Spec: bad\n## MUST — Functional core\n### Requirements\n- make it work nicely\n")
    errs = validate_spec(bad)
    assert any("E_NO_EARS" in e for e in errs) and any("E_NO_GHERKIN" in e for e in errs)

def test_impl_leak_detected(project):
    from specline.spec_lint import validate_spec
    leaky = project/"specs"/"leaky.md"
    leaky.write_text("# S\n## MUST — Functional core\n### Requirements\n- The system shall work\n```python\ndef x(): pass\n```\n")
    assert any("E_IMPL_LEAK" in e for e in validate_spec(leaky))

def test_plan_atomicity_rules(project):
    from specline.plan_lint import lint_plan
    tasks, errs = lint_plan(project/"plans"/"refunds.md")
    assert errs == [] and len(tasks) == 2
    wide = project/"plans"/"wide.md"
    wide.write_text("Architect verdict: PASS\n- [ ] T1 | slice=slices/x | files=a,b,c,d,e | verify=`ok` | too wide\n"
                    "- [ ] T2 | slice=skeleton/core | files=skeleton/core/a.py | verify=`ok` | forbidden\n"
                    "- [ ] T3 | slice=slices/x | files=slices/y/f.py | verify=`ok` | escape\n")
    _, errs = lint_plan(wide)
    codes = " ".join(errs)
    assert "E_TASK_TOO_WIDE" in codes and "E_SKELETON_TASK" in codes and "E_SLICE_ESCAPE" in codes

def test_gates_seal_progress_with_hash(project):
    from specline.gates import gate
    r = gate(project, "spec", "refunds")
    assert len(r["sha"]) == 16
    progress = (project/"context"/"PROGRESS.md").read_text()
    assert f"GATE spec refunds" in progress and r["sha"] in progress

def test_loop_requires_plan_gate_and_emits_budgeted_packet(project):
    from specline.gates import gate
    from specline.loop import next_task
    with pytest.raises(SystemExit, match="plan gate missing"):
        next_task(project, "refunds")
    gate(project, "plan", "refunds")
    out = next_task(project, "refunds")
    assert out["status"] == "ready" and out["task"] == "T1"
    packet = Path(out["packet"]).read_text()
    assert "TASK PACKET T1" in packet and "CONSTITUTION DIGEST" in packet
    assert out["tokens_est"] <= 2200          # hard budget honored
    assert out["saved_vs_naive"] > 0          # packet beats naive context

def test_intent_drift_guard_blocks_stale_plans(project):
    from specline.gates import gate
    from specline.loop import next_task
    gate(project, "plan", "refunds")
    spec = project/"specs"/"refunds.md"
    spec.write_text(spec.read_text() + "\n- The system shall also email receipts.\n")
    with pytest.raises(SystemExit, match="E_INTENT_DRIFT"):
        next_task(project, "refunds")

def test_loop_done_verifies_and_seals(project):
    from specline.gates import gate
    from specline.loop import next_task, mark_done
    gate(project, "plan", "refunds")
    next_task(project, "refunds")
    r = mark_done(project, "refunds", "T1")
    assert r["verified"]
    assert "- [x] T1 |" in (project/"plans"/"refunds.md").read_text()
    assert "DONE refunds T1" in (project/"context"/"PROGRESS.md").read_text()
    out2 = next_task(project, "refunds")
    assert out2["task"] == "T2"               # loop advances only after seal

def test_verify_failure_blocks_done(project):
    from specline.gates import gate
    from specline.loop import next_task, mark_done
    plan = project/"plans"/"refunds.md"
    plan.write_text(plan.read_text().replace('python -c "print(\'ok\')"', "false", 1))
    gate(project, "plan", "refunds")
    next_task(project, "refunds")
    with pytest.raises(SystemExit, match="VERIFY FAILED"):
        mark_done(project, "refunds", "T1")

def test_handoff_emits_valid_hsf_spec(project):
    from specline.handoff import handoff_to_hsf
    import yaml
    out = handoff_to_hsf(project, "refunds")
    spec = yaml.safe_load(out.read_text())
    assert spec["workflow_spec"] == "refunds_decisions"
    steps = {s["id"]: s for s in spec["steps"]}
    assert steps["extract_facts"]["type"] == "bounded_invocation"
    rules = steps["decide"]["rules"]
    assert rules[0]["if"] == "item_damaged == true"
    assert any("else" in r for r in rules)
    assert "item_damaged" in steps["extract_facts"]["schema"]

def test_agent_adapters_wire_claude_and_codex(project):
    from specline.adapters import wire_agent
    files = wire_agent(project, "claude")
    assert (project/"CLAUDE.md").exists() and (project/".claude"/"commands"/"next-task.md").exists()
    wire_agent(project, "codex")
    assert "SpecLine protocol" in (project/"AGENTS.md").read_text()

def test_specfactor_bands(project):
    from specline.specfactor import specfactor
    (project/"slices"/"refunds").mkdir(parents=True, exist_ok=True)
    (project/"slices"/"refunds"/"intake.py").write_text("\n".join(["x = 1"] * 60))
    r = specfactor(project)
    assert r["code_lines"] == 60 and r["specfactor"] > 0
    assert "Goldilocks" in r["verdict"] or "OVER" in r["verdict"]

def test_ledger_summarizes_savings(project):
    from specline.gates import gate
    from specline.loop import next_task
    from specline.ledger import summarize
    gate(project, "plan", "refunds")
    next_task(project, "refunds")
    s = summarize(project)
    assert s["sessions"] == 1 and s["saved_pct"] > 50


def test_optimize_prd_scores_ready_prd(tmp_path):
    from specline.prd_optimizer import optimize_prd
    prd = tmp_path / "prd.md"
    prd.write_text(
        "# PRD\n"
        "User: engineer reviewing a pull request.\n"
        "Non-goal: this will not auto-merge.\n"
        "Risk: security changes require rollback and human approval.\n"
        "- The system shall generate a reviewer evidence packet.\n"
        "- The system must record validator and test receipt paths.\n"
        "Acceptance: Given a feature trace, when optimize runs, then PR_EVIDENCE.md exists.\n"
        "Proof: pytest asserts the packet and receipt fields.\n",
        encoding="utf-8",
    )
    report = optimize_prd(prd)
    assert report.passed
    assert report.grade in {"A", "B"}


def test_optimize_prd_blocks_vague_prd(tmp_path):
    from specline.prd_optimizer import optimize_prd
    prd = tmp_path / "bad.md"
    prd.write_text("Make it simple and nice soon.", encoding="utf-8")
    report = optimize_prd(prd)
    assert not report.passed
    assert report.ambiguity_count >= 2


def test_cli_optimize_prd_json(tmp_path, capsys):
    from specline.cli import main
    prd = tmp_path / "prd.md"
    prd.write_text(
        "User: reviewer.\n"
        "Non-goal: no auto-merge.\n"
        "Risk: rollback on failure.\n"
        "- The system shall write evidence.\n"
        "- The system must record receipts.\n"
        "Acceptance: Given a trace, when run, then output exists.\n"
        "Proof: pytest validates output.\n",
        encoding="utf-8",
    )
    main(["optimize-prd", str(prd), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
