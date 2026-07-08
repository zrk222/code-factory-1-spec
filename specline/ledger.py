"""Context Ledger — receipts for token economics. Every packet logs its
estimated context cost vs the naive baseline (re-reading the whole feature
surface each session). `specline status` shows cumulative savings —
the SDD equivalent of HSF's break-even bench."""
from __future__ import annotations
import json, datetime
from pathlib import Path

LEDGER = "receipts/context-ledger.jsonl"

def _naive_baseline_tokens(root: Path, feature: str) -> int:
    total = 0
    for p in [root/"specs"/f"{feature}.md", root/"plans"/f"{feature}.md", root/"AGENTS.md"]:
        if p.exists(): total += len(p.read_text())
    for d in ["context", "slices"]:
        for p in (root/d).rglob("*.*"):
            try: total += len(p.read_text())
            except Exception: pass
    return total // 4

def log_packet(root: Path, feature: str, task_id: str, tokens_est: int, sha: str) -> dict:
    root = Path(root)
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "feature": feature, "task": task_id, "packet_tokens": tokens_est,
             "naive_baseline_tokens": _naive_baseline_tokens(root, feature), "sha": sha}
    lp = root/LEDGER
    lp.parent.mkdir(exist_ok=True)
    with lp.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry

def summarize(root: Path) -> dict:
    lp = Path(root)/LEDGER
    if not lp.exists():
        return {"sessions": 0, "packet_tokens": 0, "naive_tokens": 0, "saved_pct": 0.0}
    rows = [json.loads(l) for l in lp.read_text().splitlines() if l.strip()]
    pt = sum(r["packet_tokens"] for r in rows)
    nt = sum(r["naive_baseline_tokens"] for r in rows)
    return {"sessions": len(rows), "packet_tokens": pt, "naive_tokens": nt,
            "saved_pct": round(100 * (1 - pt / nt), 1) if nt else 0.0}
