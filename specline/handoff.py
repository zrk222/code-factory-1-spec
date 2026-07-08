"""The HSF calibration bridge: decision tables in specs become Harness
Software Factory workflow specs — compiled once, gated, deterministic —
instead of agent-improvised if-statements scattered through tissue code."""
from __future__ import annotations
import re
from pathlib import Path
import yaml
from .spec_lint import decision_rows

def handoff_to_hsf(root: Path, feature: str, owner: str = "specline") -> Path:
    root = Path(root)
    rows = decision_rows(root/"specs"/f"{feature}.md")
    if not rows:
        raise SystemExit("no decision table found in spec (## Decision logic section)")
    fields: set[str] = set()
    rules = []
    for r in rows:
        cond = r["if"].strip()
        is_else = cond.lower() in {"else", "otherwise", "*"}
        if not is_else:
            for name in re.findall(r"[a-z_][a-z0-9_]*", cond):
                if name not in {"and", "or", "not", "true", "false"}:
                    fields.add(name)
        m = re.match(r"(\w+)\s*[:=]\s*(.+)", r["then"])
        status, reason = (m.group(1).upper(), m.group(2)) if m else ("APPROVED", r["then"])
        if is_else:
            rules.append({"else": {"status": status, "reason": reason}})
        else:
            rules.append({"if": cond, "then": {"status": status, "reason": reason}})
    if not any("else" in r for r in rules):
        rules.append({"else": {"status": "HUMAN_REVIEW", "reason": "No rule matched"}})
    schema = {f: "boolean" if re.search(rf"{f}\s*==\s*(true|false)", " ".join(r['if'] for r in rows if 'if' in r)) else {"type": "float", "min": 0.0, "max": 1000000.0} for f in sorted(fields)}
    spec = {"workflow_spec": f"{feature}_decisions", "version": 1,
            "metadata": {"owner": owner, "compliance": []},
            "inputs": {"input_data": {"text": "string"}},
            "steps": [
                {"id": "extract_facts", "type": "bounded_invocation",
                 "schema": schema, "on_out_of_bounds": "human_review"},
                {"id": "decide", "type": "branch", "rules": rules}],
            "outputs": {"AuthResult": {"status": "enum[APPROVED, DENIED, HUMAN_REVIEW]", "reason": "string"}}}
    out = root/"handoff"/f"{feature}_decisions.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(yaml.safe_dump(spec, sort_keys=False))
    return out
