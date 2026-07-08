"""Agent adapters — one command wires SpecLine into Claude Code, Codex, or
any agent harness. The constitution IS the agent config; packets are the
session protocol; slash commands make the loop one keystroke."""
from __future__ import annotations
import shutil
from pathlib import Path

CLAUDE_NEXT_CMD = """Read context/PROGRESS.md, then run `specline loop next <feature>`.
Open ONLY the packet file it prints and the files the packet lists.
Complete the packet's single task, run its verify command, then run
`specline loop done <feature> <task-id>` and STOP. Do not start another task.
"""

def wire_agent(root: Path, agent: str) -> list[Path]:
    root = Path(root); created = []
    constitution = (root/"AGENTS.md").read_text()
    if agent in {"claude", "claude-code"}:
        dst = root/"CLAUDE.md"
        dst.write_text(constitution + "\n\n## SpecLine protocol\n" + CLAUDE_NEXT_CMD)
        created.append(dst)
        cmd_dir = root/".claude"/"commands"; cmd_dir.mkdir(parents=True, exist_ok=True)
        c = cmd_dir/"next-task.md"; c.write_text(CLAUDE_NEXT_CMD); created.append(c)
    elif agent == "codex":
        # Codex reads AGENTS.md natively; append the protocol if absent
        if "SpecLine protocol" not in constitution:
            (root/"AGENTS.md").write_text(constitution + "\n\n## SpecLine protocol\n" + CLAUDE_NEXT_CMD)
        created.append(root/"AGENTS.md")
    else:
        dst = root/f"{agent.upper()}_AGENT.md"
        dst.write_text(constitution + "\n\n## SpecLine protocol\n" + CLAUDE_NEXT_CMD)
        created.append(dst)
    return created
