"""Task Packets — the token-lean session brief. This is C_t = γ·R_f·T_d
made executable: each packet contains ONLY the task, the spec excerpt that
governs it, the exact file list, a constitution digest, and the verify
command — under a hard token budget. One packet == one agent session."""
from __future__ import annotations
import hashlib, re
from pathlib import Path

TOKEN_BUDGET = 2200   # ~ chars/4; hard cap for a packet
CHARS_PER_TOKEN = 4

def _est_tokens(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN

def _relevant_spec_excerpt(spec_text: str, task_text: str, max_chars: int = 2600) -> str:
    """Requirement-scoped excerpt (anti-drift).

    The old version matched individual LINES by shared vocabulary, which could
    hand the agent a half-requirement (condition stripped off), forcing it to
    improvise the rest. This version selects WHOLE requirement blocks and always
    ships the complete acceptance scenario intact. The agent never receives a
    partial rule.
    """
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", task_text)}
    lines = spec_text.splitlines()

    gherkin_block, in_g = [], False
    for line in lines:
        if line.strip().startswith("```gherkin"):
            in_g = True
        if in_g:
            gherkin_block.append(line)
        if in_g and line.strip() == "```" and not line.strip().startswith("```g"):
            in_g = False

    req_block, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("- "):
            block = [line]; j = i + 1
            while j < len(lines) and lines[j].startswith(("  ", "\t")) and not lines[j].lstrip().startswith("- "):
                block.append(lines[j]); j += 1
            bw = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", " ".join(block))}
            if words & bw:
                req_block.extend(block)
            i = j
        else:
            i += 1

    parts = []
    if req_block:
        parts.append("### Governing requirements (complete blocks)\n" + "\n".join(req_block))
    if gherkin_block:
        parts.append("### Acceptance (verbatim — do not reinterpret)\n" + "\n".join(gherkin_block))
    out = "\n\n".join(parts) if parts else "\n".join(l for l in lines if l.lstrip().startswith("- "))
    if len(out) > max_chars:
        kept, total = [], 0
        for para in out.split("\n\n"):
            if total + len(para) > max_chars:
                break
            kept.append(para); total += len(para) + 2
        out = "\n\n".join(kept) if kept else out[:max_chars]
    return out

CONSTITUTION_DIGEST = (
    "CONSTITUTION DIGEST: one task only; read ONLY listed files; tests ship with code; "
    "never touch skeleton/; never add deps without ADR; never leave stubs; "
    "decision logic goes to the factory, not inline; stop and ask on ambiguity."
)

def build_packet(root: Path, feature: str, task: dict) -> tuple[Path, dict]:
    root = Path(root)
    spec_text = (root/"specs"/f"{feature}.md").read_text()
    excerpt = _relevant_spec_excerpt(spec_text, task["text"])
    files_block = "\n".join(f"- {f}" for f in task["files"]) or "- (new files in slice)"
    body = f"""# TASK PACKET {task['id']} — {feature}
<!-- One session. Finish, verify, stop. Context resets after this. -->

## Your single task
{task['text']}

## Slice (you may only create/modify files here + tests)
{task['slice']}

## Files in scope (R_f — read nothing else except context/PROGRESS.md)
{files_block}

## Governing spec excerpt
{excerpt}

## {CONSTITUTION_DIGEST}

## Definition of done
Run: `{task['verify']}` — must pass. Then STOP and report the diff summary.
"""
    est = _est_tokens(body)
    if est > TOKEN_BUDGET:
        # deterministic prune: shrink excerpt first (same discipline as HSF context assembler)
        overflow = (est - TOKEN_BUDGET) * CHARS_PER_TOKEN
        body = body.replace(excerpt, excerpt[: max(len(excerpt) - overflow, 400)])
        est = _est_tokens(body)
    out = root/"packets"/f"{feature}-{task['id']}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(body)
    meta = {"packet": str(out), "tokens_est": est,
            "sha": hashlib.sha256(body.encode()).hexdigest()[:16]}
    return out, meta
