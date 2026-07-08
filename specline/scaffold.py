"""specline init — stand up the six-file context system + constitution."""
from __future__ import annotations
import shutil
from pathlib import Path
from .paths import templates_dir, project_dirs

def init_project(root: Path) -> list[Path]:
    root = Path(root); created = []
    for d in project_dirs(root).values():
        d.mkdir(parents=True, exist_ok=True)
    t = templates_dir()
    for name in ["PROJECT_OVERVIEW.md","ARCHITECTURE.md","CODE_STANDARDS.md",
                 "AI_WORKFLOW_RULES.md","UI_CONTEXT.md","PROGRESS.md"]:
        dst = root/"context"/name
        if not dst.exists():
            shutil.copy(t/"context"/name, dst); created.append(dst)
    for src, dst in [(t/"AGENTS.md", root/"AGENTS.md")]:
        if not dst.exists():
            shutil.copy(src, dst); created.append(dst)
    pd = root/"templates"
    pd.mkdir(exist_ok=True)
    for name in ["SPEC_TEMPLATE.md","PLAN_TEMPLATE.md"]:
        if not (pd/name).exists():
            shutil.copy(t/name, pd/name); created.append(pd/name)
    per = root/"personas"; per.mkdir(exist_ok=True)
    for p in (t/"personas").glob("*.md"):
        if not (per/p.name).exists():
            shutil.copy(p, per/p.name); created.append(per/p.name)
    return created

def new_feature(root: Path, name: str) -> tuple[Path, Path]:
    root = Path(root)
    spec = root/"specs"/f"{name}.md"; plan = root/"plans"/f"{name}.md"
    if spec.exists():
        raise FileExistsError(spec)
    spec.parent.mkdir(parents=True, exist_ok=True); plan.parent.mkdir(parents=True, exist_ok=True)
    t = templates_dir()
    spec.write_text((t/"SPEC_TEMPLATE.md").read_text().replace("{name}", name))
    plan.write_text((t/"PLAN_TEMPLATE.md").read_text().replace("{name}", name))
    return spec, plan
