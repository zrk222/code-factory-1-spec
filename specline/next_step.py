"""`specline next <feature>`: where a feature stands and the one command that moves it forward, from files on disk.

States, in order: no_spec -> spec_invalid -> spec_ungated -> plan_ungated -> tasks_open -> evidence_missing -> done.
Mirrors `forge status` so an orchestrator can drive SpecLine with the same loop."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

STATES = ("no_spec", "spec_invalid", "spec_ungated", "plan_ungated", "tasks_open", "evidence_missing", "done")


def _state(state: str, argv: list[str] | None, blocker: str | None = None) -> dict:
    return {"state": state, "next_argv": argv, "blocker": blocker}


def _spec_errors(spec: Path) -> list[str]:
    from .spec_lint import validate_spec
    from .strict_lint import strict_errors
    return validate_spec(spec) + strict_errors(spec)


def _approval(root: Path, feature: str) -> tuple[dict | None, str | None]:
    from .gates import _approved_plan_snapshot
    try:
        return _approved_plan_snapshot(root, feature)[1], None
    except SystemExit as exc:
        return None, str(exc)


def _open_tasks(root: Path, feature: str) -> list[str]:
    from .loop import _completion
    from .plan_lint import parse_tasks
    tasks = parse_tasks(root / "plans" / f"{feature}.md")
    return [task["id"] for task in tasks if not _completion(root, feature, task, refresh=True)]


def _evidence_state(root: Path, feature: str, approval: dict) -> dict | None:
    if importlib.util.find_spec("factoryline") is None:
        return None  # acceptance evidence is optional without Code Factory installed
    envelope = root / "envelopes" / f"{feature}.json"
    try:
        current = json.loads(envelope.read_text(encoding="utf-8"))["acceptance_evidence"]["approval_seal"]
    except (OSError, ValueError, KeyError, TypeError):
        current = None
    if current == approval["seal"]:
        return None
    contract = root / ".factory" / "workflow-audit-contract.json"
    if not contract.exists():
        return _state("evidence_missing", ["specline", "evidence", feature, "--init"],
                      "no acceptance contract: draft one, then fill it in")
    return _state("evidence_missing", ["specline", "evidence", feature],
                  "envelope missing or stale for the current approval" if envelope.exists() else "envelope not exported")


def next_step(root: Path, feature: str) -> dict:
    from .gates import spec_gate_current
    root = Path(root)
    spec = root / "specs" / f"{feature}.md"
    if not spec.exists():
        return _state("no_spec", ["specline", "new", feature], f"specs/{feature}.md does not exist")
    errors = _spec_errors(spec)
    if errors:
        return _state("spec_invalid", ["specline", "strict", feature], errors[0])
    if not spec_gate_current(root, feature):
        return _state("spec_ungated", ["specline", "gate", "spec", feature], "no spec gate for the current spec")
    approval, problem = _approval(root, feature)
    if approval is None:
        from .plan_lint import lint_plan
        plan = root / "plans" / f"{feature}.md"
        lint = lint_plan(plan)[1] if plan.exists() else [f"plans/{feature}.md does not exist"]
        return _state("plan_ungated", ["specline", "gate", "plan", feature], (lint or [problem])[0])
    open_tasks = _open_tasks(root, feature)
    if open_tasks:
        return _state("tasks_open", ["specline", "loop", "next", feature], "open: " + ", ".join(open_tasks))
    return _evidence_state(root, feature, approval) or _state("done", None)
