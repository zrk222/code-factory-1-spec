"""specline tasks — atomicity linter. Enforces the double-decomposition
contract so every task is a clean Ralph-Wiggum session."""
from __future__ import annotations
import re
from pathlib import Path, PureWindowsPath


def contained_path(root: Path, value: str) -> Path:
    """Resolve a portable repository-relative path, never a lexical prefix."""
    root = Path(root).resolve()
    portable = value.replace("\\", "/")
    path = Path(portable)
    if (not portable or path.is_absolute() or PureWindowsPath(value).drive
            or ".." in path.parts):
        raise ValueError("path must be repository-relative without traversal")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("resolved path escapes repository")
    return resolved


def task_path_errors(root: Path, task: dict) -> list[str]:
    errors = []
    try:
        scope = contained_path(root, task["slice"])
        if scope == Path(root).resolve():
            raise ValueError("task slice must be narrower than the repository root")
        tests = contained_path(root, "tests")
        skeleton = contained_path(root, "skeleton")
        if scope.is_relative_to(skeleton):
            errors.append(f"E_SKELETON_TASK: {task['id']} targets human-owned skeleton")
        for name in task["files"]:
            path = contained_path(root, name)
            if path.is_relative_to(skeleton):
                errors.append(f"E_SKELETON_TASK: {task['id']} targets human-owned skeleton")
            elif not (path.is_relative_to(scope) or path.is_relative_to(tests)):
                errors.append(f"E_SLICE_ESCAPE: {task['id']} file outside declared slice")
    except (ValueError, OSError, RuntimeError) as exc:
        errors.append(f"E_SLICE_ESCAPE: {task['id']} {exc}")
    return errors

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
        errors.extend(task_path_errors(Path(path).absolute().parent.parent, t))
    return tasks, errors
