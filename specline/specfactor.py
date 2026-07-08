"""SpecFactor gauge = spec lines / code lines, with the Goldilocks bands."""
from __future__ import annotations
from pathlib import Path

def specfactor(root: Path) -> dict:
    root = Path(root)
    spec_lines = sum(len(p.read_text().splitlines()) for p in (root/"specs").glob("*.md")) if (root/"specs").exists() else 0
    plan_lines = sum(len(p.read_text().splitlines()) for p in (root/"plans").glob("*.md")) if (root/"plans").exists() else 0
    code_lines = 0
    for d in ["slices", "skeleton"]:
        if (root/d).exists():
            for p in (root/d).rglob("*.*"):
                try: code_lines += len(p.read_text().splitlines())
                except Exception: pass
    ratio = round((spec_lines + plan_lines) / code_lines, 2) if code_lines else float("inf")
    if code_lines == 0: verdict = "no code yet"
    elif ratio < 0.5: verdict = "UNDER-SPECIFIED: sliding toward vibe coding — expect hallucinations"
    elif ratio <= 2.5: verdict = "Goldilocks zone"
    else: verdict = "OVER-SPECIFIED: review bottleneck risk (>2.5)"
    return {"spec_lines": spec_lines + plan_lines, "code_lines": code_lines,
            "specfactor": ratio, "verdict": verdict}
