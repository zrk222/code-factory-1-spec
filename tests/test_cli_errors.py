"""SL-1: ordinary mistakes print an E_ code (never a traceback); the plan template cannot pass; `gate code`
needs its prerequisites; `--version` is fast."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _specline(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "specline.cli", *argv], cwd=cwd, env=env, capture_output=True,
                          text=True)


@pytest.mark.parametrize("argv", [
    ("validate", "nosuch"), ("strict", "nosuch"), ("tasks", "nosuch"), ("gate", "spec", "nosuch"),
    ("handoff", "nosuch"), ("agent", "claude")])
def test_missing_inputs_report_codes_not_tracebacks(tmp_path, argv):
    done = _specline(*argv, cwd=tmp_path)
    assert done.returncode != 0 and done.stderr.startswith("E_") and "Traceback" not in done.stderr


def test_new_twice_reports_e_exists(tmp_path):
    assert _specline("init", cwd=tmp_path).returncode == 0
    assert _specline("new", "refunds", cwd=tmp_path).returncode == 0
    again = _specline("new", "refunds", cwd=tmp_path)
    assert again.returncode != 0 and again.stderr.startswith("E_EXISTS") and "Traceback" not in again.stderr


def test_hints_point_at_the_right_command(tmp_path):
    assert "specline new" in _specline("validate", "nosuch", cwd=tmp_path).stderr
    assert "specline init" in _specline("agent", "claude", cwd=tmp_path).stderr


def test_untouched_plan_template_fails_lint(tmp_path):
    from specline.plan_lint import lint_plan
    from specline.scaffold import init_project, new_feature
    init_project(tmp_path)
    _, plan = new_feature(tmp_path, "refunds")
    _, errors = lint_plan(plan)
    codes = {error.split(":")[0] for error in errors}
    assert {"E_NO_VERDICT", "E_PLAN_PLACEHOLDER"} <= codes


def test_good_plan_still_passes(project):
    from specline.plan_lint import lint_plan
    _, errors = lint_plan(project / "plans" / "refunds.md")
    assert errors == []


def test_code_gate_requires_an_approved_plan_and_completed_tasks(project):
    from specline.gates import gate
    with pytest.raises(SystemExit):
        gate(project, "code", "refunds")
    gate(project, "spec", "refunds")
    gate(project, "plan", "refunds")
    with pytest.raises(SystemExit, match="E_CODE_GATE_PREREQ"):
        gate(project, "code", "refunds")


def test_verify_validators_says_strict_failed_first(tmp_path):
    from specline.scaffold import init_project, new_feature
    init_project(tmp_path)
    new_feature(tmp_path, "draft")
    done = _specline("verify-validators", "draft", "--root", str(tmp_path), cwd=tmp_path)
    assert done.returncode != 0 and "STRICT FAILED FIRST" in done.stderr and "hollow" not in done.stderr


def test_plain_version_is_the_package_version(tmp_path):
    from specline import __version__
    assert _specline("--version", cwd=tmp_path).stdout.strip() == f"specline {__version__}"
