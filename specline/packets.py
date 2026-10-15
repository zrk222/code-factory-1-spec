"""Task Packets — the token-lean session brief. This is C_t = γ·R_f·T_d
made executable: each packet contains ONLY the task, the spec excerpt that
governs it, the exact file list, a constitution digest, and the verify
command — under a hard token budget. One packet == one agent session."""
from __future__ import annotations
import hashlib, re
from pathlib import Path
from .plan_lint import contained_path, task_path_errors

TOKEN_BUDGET = 2200   # ~ chars/4; hard cap for a packet
CHARS_PER_TOKEN = 4

def _est_tokens(s: str) -> int:
    return (len(s) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN

def _relevant_spec_excerpt(spec_text: str, task_text: str, max_chars: int | None = None) -> str:
    """Preserve all governing context; lexical overlap cannot prove relevance."""
    if max_chars is not None and len(spec_text) > max_chars:
        raise SystemExit("E_PACKET_BUDGET: split the governing spec; never truncate obligations")
    return spec_text

CONSTITUTION_DIGEST = (
    "CONSTITUTION DIGEST: one task only; read ONLY listed files; tests ship with code; "
    "never touch skeleton/; never add deps without ADR; never leave stubs; "
    "decision logic goes to the factory, not inline; stop and ask on ambiguity."
)

def build_packet(root: Path, feature: str, task: dict) -> tuple[Path, dict]:
    root = Path(root).resolve()
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", feature)
            or not re.fullmatch(r"T\d+", task["id"])):
        raise SystemExit("E_PACKET_ID: invalid feature or task identity")
    errors = task_path_errors(root, task)
    if errors:
        raise SystemExit("\n".join(errors))
    try:
        spec = contained_path(root, f"specs/{feature}.md")
        packet_path = root / "packets" / f"{feature}-{task['id']}.md"
        out = contained_path(root, f"packets/{feature}-{task['id']}.md")
        if (root / "packets").resolve() != root / "packets" or out != packet_path:
            raise ValueError("packet output redirects through a link")
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit("E_PACKET_PATH: unsafe specification or packet path") from exc
    spec_text = spec.read_text()
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

## Complete governing specification
{excerpt}

## {CONSTITUTION_DIGEST}

## Definition of done
Run: `{task['verify']}` — must pass. Then STOP and report the diff summary.
"""
    est = _est_tokens(body)
    if est > TOKEN_BUDGET:
        raise SystemExit(
            f"E_PACKET_BUDGET: packet estimates {est} tokens (limit {TOKEN_BUDGET}); "
            "split the feature/spec and re-approve its plan; no requirements were truncated"
        )
    out.parent.mkdir(exist_ok=True)
    out.write_text(body)
    meta = {"packet": str(out), "tokens_est": est,
            "sha": hashlib.sha256(body.encode()).hexdigest()[:16]}
    return out, meta
