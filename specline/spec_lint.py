"""specline validate — EARS/Gherkin/leak lint. A spec that fails here never
reaches an agent, so ambiguity dies before it costs tokens."""
from __future__ import annotations
import re
from pathlib import Path

EARS = re.compile(r"^\s*-\s+(The system shall|When\b|While\b|If\b|Where\b)", re.M)
REQ_LINE = re.compile(r"^\s*-\s+\S", re.M)


def validate_spec(path: Path) -> list[str]:
    text = Path(path).read_text()
    errors = []
    if "## MUST" not in text:
        errors.append("E_NO_MUST: missing '## MUST — Functional core' section")
    must = text.split("## SHOULD")[0]
    reqs_section = must.split("### Requirements")[-1] if "### Requirements" in must else ""
    req_lines = [l for l in REQ_LINE.findall(reqs_section)]
    ears_hits = EARS.findall(reqs_section)
    if not ears_hits:
        errors.append("E_NO_EARS: no requirement uses an EARS keyword (shall/When/While/If/Where)")
    non_ears = max(len(req_lines) - len(ears_hits), 0)
    if req_lines and non_ears > len(req_lines) // 2:
        errors.append(f"E_EARS_RATIO: {non_ears}/{len(req_lines)} requirement lines lack EARS keywords")
    if "```gherkin" not in text:
        errors.append("E_NO_GHERKIN: no acceptance criteria scenario found")
    elif not re.search(r"Given .+", text) or not re.search(r"Then .+", text):
        errors.append("E_GHERKIN_INCOMPLETE: scenario missing Given/Then")
    fence_langs = re.findall(r"```(\w+)", must)
    if any(lang != "gherkin" for lang in fence_langs):
        errors.append("E_IMPL_LEAK: implementation code fence inside MUST section (belongs in plan)")
    if re.search(r"## MUST.*?\b(def |class |function\(|=>)", must, re.S):
        errors.append("E_IMPL_LEAK: code-level detail in MUST section")
    if "Status: approved" in text and errors:
        errors.append("E_APPROVED_BUT_INVALID: spec marked approved while failing lint")
    return errors

def decision_rows(path: Path) -> list[dict]:
    """Parse the 'Decision logic (factory candidates)' table -> rules."""
    text = Path(path).read_text()
    if "## Decision logic" not in text:
        return []
    section = text.split("## Decision logic")[1]
    rows = []
    for line in section.splitlines():
        m = re.match(r"\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", line)
        if m:
            rows.append({"n": int(m.group(1)), "if": m.group(2), "then": m.group(3)})
    return rows
