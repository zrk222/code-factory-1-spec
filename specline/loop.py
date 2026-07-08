"""The Ralph Wiggum Loop runner. `next` emits one packet; `done` verifies
and seals it with a receipt in PROGRESS.md. Drift guard: if the spec hash
changed since plan approval, the loop refuses until the plan is re-gated."""
from __future__ import annotations
import hashlib, subprocess, datetime
from pathlib import Path
from .plan_lint import parse_tasks, lint_plan
from .packets import build_packet
from .ledger import log_packet

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

def _progress(root: Path) -> Path:
    return Path(root)/"context"/"PROGRESS.md"

def _append_progress(root: Path, line: str):
    p = _progress(root)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    p.write_text(p.read_text() + f"\n- [{ts}] {line}")

def _approved_spec_sha(root: Path, feature: str) -> str | None:
    for line in _progress(root).read_text().splitlines():
        if f"GATE plan {feature}" in line and "sha=" in line:
            return line.split("sha=")[-1].strip()
    return None

def next_task(root: Path, feature: str) -> dict:
    root = Path(root)
    plan = root/"plans"/f"{feature}.md"
    tasks, errors = lint_plan(plan)
    if errors:
        raise SystemExit("plan not clean:\n" + "\n".join(errors))
    locked = _approved_spec_sha(root, feature)
    current = _sha(root/"specs"/f"{feature}.md")
    if locked is None:
        raise SystemExit("plan gate missing: run `specline gate plan " + feature + "` first")
    if locked != current:
        raise SystemExit(f"E_INTENT_DRIFT: spec changed since plan gate (locked {locked}, now {current}). Re-gate the plan.")
    todo = [t for t in tasks if not t["done"]]
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
    plan = root/"plans"/f"{feature}.md"
    tasks = parse_tasks(plan)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        raise SystemExit(f"unknown task {task_id}")
    verified = True
    if run_verify:
        proc = subprocess.run(task["verify"], shell=True, cwd=root, capture_output=True, text=True)
        verified = proc.returncode == 0
        if not verified:
            raise SystemExit(f"VERIFY FAILED for {task_id}: {task['verify']}\n{proc.stderr[-500:]}")
    text = plan.read_text().replace(f"- [ ] {task_id} |", f"- [x] {task_id} |", 1)
    plan.write_text(text)
    _append_progress(root, f"DONE {feature} {task_id} verify_pass=true  # context resets now")
    return {"task": task_id, "verified": verified}
