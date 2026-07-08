from __future__ import annotations
from pathlib import Path

def templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "templates"

def project_dirs(root: Path) -> dict:
    return {"specs": root/"specs", "plans": root/"plans", "context": root/"context",
            "adr": root/"adr", "packets": root/"packets", "slices": root/"slices",
            "skeleton": root/"skeleton", "handoff": root/"handoff", "receipts": root/"receipts"}
