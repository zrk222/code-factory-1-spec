"""specline tasks — atomicity linter. Enforces the double-decomposition
contract so every task is a clean Ralph-Wiggum session."""
from __future__ import annotations
import re
from pathlib import Path

TASK = re.compile(r"^- \[( |x)\] (T\d+) \| slice=(\S+) \| files=([^|]+) \| verify=`([^`]+)` \| (.+)$")

def parse_tasks(path: Path) -> list[dict]:
    tasks = []
    for line in Path(path).read_text().splitlines():
        m = TASK.match(line.strip())
        if m:
            files = [f.strip() for f in m.group(4).split(",") if f.strip() and "<=" not in f]
            tasks.append({"done": m.group(1) == "x", "id": m.group(2), "slice": m.group(3),
                          "files": files, "verify": m.group(5), "text": m.group(6)})
    return tasks

def lint_plan(path: Path) -> tuple[list[dict], list[str]]:
    text = Path(path).read_text()
    tasks = parse_tasks(path)
    errors = []
    if not tasks:
        errors.append("E_NO_TASKS: no parseable task lines (format: - [ ] T1 | slice=x | files=a,b | verify=`cmd` | text)")
    if "Architect verdict: PASS" not in text:
        errors.append("E_NO_VERDICT: plan lacks 'Architect verdict: PASS'")
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        errors.append("E_DUP_TASK_ID")
    for t in tasks:
        if len(t["files"]) > 4:
            errors.append(f"E_TASK_TOO_WIDE: {t['id']} touches {len(t['files'])} files (max 4)")
        if t["slice"].startswith("skeleton"):
            errors.append(f"E_SKELETON_TASK: {t['id']} targets human-owned skeleton")
        for f in t["files"]:
            if f and not (f.startswith(t["slice"]) or f.startswith("tests")):
                errors.append(f"E_SLICE_ESCAPE: {t['id']} file {f!r} outside slice {t['slice']!r}")
    return tasks, errors
