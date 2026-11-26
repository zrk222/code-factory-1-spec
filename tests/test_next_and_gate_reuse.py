"""SL-5b/c: `specline next` walks the seven states from files on disk; `gate plan` reuses a matching approval."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from specline.gates import gate
from specline.loop import mark_done
from specline.next_step import STATES, next_step
from specline.scaffold import init_project, new_feature

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "refund-portal"


def _walk(root: Path) -> list[tuple[str, list | None]]:
    seen = []

    def record():
        step = next_step(root, "refunds")
        seen.append((step["state"], step["next_argv"]))
        return step

    init_project(root)
    record()                                            # no_spec
    new_feature(root, "refunds")
    record()                                            # spec_invalid (template)
    for kind in ("specs", "plans"):
        shutil.copyfile(EXAMPLE / kind / "refunds.md", root / kind / "refunds.md")
    record()                                            # spec_ungated
    gate(root, "spec", "refunds")
    record()                                            # plan_ungated
    gate(root, "plan", "refunds")
    record()                                            # tasks_open
    mark_done(root, "refunds", "T1")
    assert record()["blocker"] == "open: T2"
    mark_done(root, "refunds", "T2")
    record()                                            # evidence_missing or done
    return seen


def test_next_walks_the_states_in_order(tmp_path):
    seen = _walk(tmp_path)
    states = [state for state, _ in seen]
    expected = ["no_spec", "spec_invalid", "spec_ungated", "plan_ungated", "tasks_open", "tasks_open"]
    assert states[:6] == expected
    has_cf = importlib.util.find_spec("factoryline") is not None
    assert states[6] == ("evidence_missing" if has_cf else "done")
    assert seen[0][1] == ["specline", "new", "refunds"] and seen[3][1] == ["specline", "gate", "plan", "refunds"]
    assert set(states) <= set(STATES)


def test_spec_edit_after_gate_goes_back_to_spec_ungated(tmp_path):
    _walk(tmp_path)
    spec = tmp_path / "specs" / "refunds.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace("Fraud scoring", "Fraud scoring of any kind"),
                    encoding="utf-8")
    assert next_step(tmp_path, "refunds")["state"] == "spec_ungated"


def test_next_cli_json(tmp_path, capsys):
    from specline.cli import main
    init_project(tmp_path)
    main(["next", "refunds", "--root", str(tmp_path), "--json"])
    assert json.loads(capsys.readouterr().out)["state"] == "no_spec"


def test_gate_plan_is_idempotent_until_inputs_change(project):
    gate(project, "spec", "refunds")
    first = gate(project, "plan", "refunds")
    again = gate(project, "plan", "refunds")
    assert again["reused"] is True and again["approval_seal"] == first["approval_seal"]
    forced = gate(project, "plan", "refunds", reissue=True)
    assert forced["approval_seal"] != first["approval_seal"] and "reused" not in forced
    plan = project / "plans" / "refunds.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace("Build the intake", "Build the request intake"),
                    encoding="utf-8")
    changed = gate(project, "plan", "refunds")
    assert "reused" not in changed and changed["approval_seal"] != forced["approval_seal"]


def test_reused_approval_keeps_completions_current(project):
    gate(project, "spec", "refunds")
    gate(project, "plan", "refunds")
    assert mark_done(project, "refunds", "T1")["verified"]
    gate(project, "plan", "refunds")  # a resumed pipeline re-runs the gate
    assert next_step(project, "refunds")["blocker"] == "open: T2"


def test_different_approver_is_a_new_approval(project):
    gate(project, "spec", "refunds")
    first = gate(project, "plan", "refunds")
    other = gate(project, "plan", "refunds", approver="lead")
    assert "reused" not in other and other["approval_seal"] != first["approval_seal"]


def test_gates_work_without_init_progress_file(tmp_path):
    """X-5: a project bootstrapped with `specline new` but no `init` no longer crashes at the first seal."""
    for kind in ("specs", "plans"):
        (tmp_path / kind).mkdir()
        shutil.copyfile(EXAMPLE / kind / "refunds.md", tmp_path / kind / "refunds.md")
    gate(tmp_path, "spec", "refunds")
    progress = (tmp_path / "context" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "## Current phase\nplan" in progress and "GATE spec refunds" in progress
    gate(tmp_path, "plan", "refunds")
    assert mark_done(tmp_path, "refunds", "T1")["verified"]
