"""The Ralph Wiggum Loop runner. `next` emits one packet; `done` verifies
and seals it with a receipt in PROGRESS.md. Drift guard: if the spec hash
changed since plan approval, the loop refuses until the plan is re-gated."""
from __future__ import annotations
import hashlib, subprocess, datetime
from pathlib import Path
from .plan_lint import lint_plan
from .packets import build_packet
from .ledger import log_packet
from .gates import (atomic_text, plan_identity, read_receipt, receipt_path,
                    require_plan_approval, seal_receipt)

def _progress(root: Path) -> Path:
    return Path(root)/"context"/"PROGRESS.md"

def _append_progress(root: Path, line: str):
    p = _progress(root)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    atomic_text(p, p.read_text() + f"\n- [{ts}] {line}")

def _candidate(root: Path, task: dict) -> dict:
    root = root.resolve()
    files = {}
    for name in task["files"]:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise SystemExit("E_SLICE_ESCAPE: task source escapes repository")
        files[name] = None
        if path.is_file():
            with path.open("rb") as source:
                files[name] = hashlib.file_digest(source, "sha256").hexdigest()
    return files


def _completion(root: Path, feature: str, task: dict, *, refresh: bool = False) -> dict | None:
    record = read_receipt(receipt_path(root, feature, task["id"]))
    if record is None:
        if task["done"]:
            raise SystemExit("E_COMPLETION_MISSING: checked task has no verification receipt")
        return None
    identity = plan_identity(root, feature)
    if (record.get("schema") != "specline.task-completion.v2"
            or record.get("status") != "PASS"
            or record.get("task") != task["id"]
            or record.get("command") != task["verify"]
            or record.get("candidate") != _candidate(root, task)
            or any(record.get(key) != value for key, value in identity.items())):
        if refresh:
            return None
        raise SystemExit("E_COMPLETION_STALE: task verification receipt no longer matches")
    return record


def _project_done(plan: Path, task_id: str):
    original = plan.read_text()
    text = original.replace(f"- [ ] {task_id} |", f"- [x] {task_id} |", 1)
    if text != original:
        atomic_text(plan, text)


def _project_completion(root: Path, feature: str, task_id: str, record: dict):
    _project_done(root / "plans" / f"{feature}.md", task_id)
    event = f"DONE {feature} {task_id} verify_pass=true receipt_seal={record['seal']}"
    if not any(line.partition("] ")[2] == event for line in _progress(root).read_text().splitlines()):
        _append_progress(root, event)

def next_task(root: Path, feature: str) -> dict:
    root = Path(root)
    require_plan_approval(root, feature)
    plan = root/"plans"/f"{feature}.md"
    tasks, errors = lint_plan(plan)
    if errors:
        raise SystemExit("plan not clean:\n" + "\n".join(errors))
    todo = []
    for task in tasks:
        if record := _completion(root, feature, task):
            _project_completion(root, feature, task["id"], record)
        else:
            todo.append(task)
    if not todo:
        return {"status": "all_done"}
    task = todo[0]
    packet, meta = build_packet(root, feature, task)
    entry = log_packet(root, feature, task["id"], meta["tokens_est"], meta["sha"])
    _append_progress(root, f"PACKET {feature} {task['id']} tokens={meta['tokens_est']} sha={meta['sha']}")
    return {"status": "ready", "task": task["id"], "packet": str(packet),
            "tokens_est": meta["tokens_est"], "saved_vs_naive": entry["naive_baseline_tokens"] - meta["tokens_est"]}

def mark_done(root: Path, feature: str, task_id: str, run_verify: bool = True) -> dict:
    root = Path(root)
    require_plan_approval(root, feature)
    plan = root/"plans"/f"{feature}.md"
    tasks, errors = lint_plan(plan)
    if errors:
        raise SystemExit("plan not clean:\n" + "\n".join(errors))
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        raise SystemExit(f"unknown task {task_id}")
    if not run_verify:
        _append_progress(root, f"VERIFY {feature} {task_id} status=NOT_RUN verify_pass=false")
        return {"task": task_id, "verified": False, "status": "NOT_RUN"}
    previous = _completion(root, feature, task, refresh=True)
    if previous:
        _project_completion(root, feature, task_id, previous)
        return {"task": task_id, "verified": True, "status": "PASS", "reused": True}
    identity = plan_identity(root, feature)
    candidate = _candidate(root, task)
    try:
        proc = subprocess.run(task["verify"], shell=True, cwd=root, capture_output=True,
                              text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        _append_progress(root, f"VERIFY {feature} {task_id} status=TIMEOUT verify_pass=false")
        raise SystemExit("VERIFY FAILED: verification timed out") from exc
    if proc.returncode != 0:
        _append_progress(root, f"VERIFY {feature} {task_id} status=FAIL verify_pass=false")
        raise SystemExit(f"VERIFY FAILED for {task_id}: {task['verify']}\n{proc.stderr[-500:]}")
    require_plan_approval(root, feature)
    if plan_identity(root, feature) != identity or _candidate(root, task) != candidate:
        raise SystemExit("E_VERIFY_DRIFT: governing inputs or task source changed during verification")
    completion = seal_receipt(receipt_path(root, feature, task_id), {
        "schema": "specline.task-completion.v2", **identity, "task": task_id,
        "command": task["verify"], "candidate": candidate, "status": "PASS",
        "returncode": proc.returncode,
        "stdout_sha256": hashlib.sha256(proc.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(proc.stderr.encode()).hexdigest(),
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "authority": "local-integrity-only",
    })
    _project_completion(root, feature, task_id, completion)
    return {"task": task_id, "verified": True, "status": "PASS", "reused": False}
