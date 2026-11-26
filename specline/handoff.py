"""The HSF calibration bridge: decision tables in specs become Harness Software Factory workflow specs
(compiled once, gated, deterministic) instead of agent-improvised if-statements in tissue code.

The emitted YAML is checked against HSF's contract before it is written: a workflow id HSF accepts, fields
typed from the values they are compared with, a status enum built from the statuses the table uses, and,
when HSF is installed, a round-trip through `hsf.spec.loader.load_spec`.
"""
from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import yaml

from .spec_lint import decision_rows

ELSE_WORDS = {"else", "otherwise", "*"}
_KEYWORDS = {"and", "or", "not", "true", "false", "True", "False", "null", "None"}


class HandoffError(SystemExit):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def workflow_id(feature: str) -> str:
    """HSF requires `[a-z][a-z0-9_]*`; SpecLine features may contain capitals, `-` and `.`."""
    slug = re.sub(r"[^a-z0-9_]", "_", feature.lower()).strip("_") or "feature"
    return (slug if slug[0].isalpha() else f"f_{slug}") + "_decisions"


def _python_condition(cond: str) -> str:
    return re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", cond))


def _field_types(cond: str, index: int) -> dict[str, str]:
    """Field name -> HSF type, from the constants each field is compared with."""
    try:
        tree = ast.parse(_python_condition(cond), mode="eval")
    except SyntaxError as exc:
        raise HandoffError("E_HANDOFF_EXPR", f"rule #{index}: cannot parse condition {cond!r} ({exc.msg})") from None
    types: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant):
                    value = comparator.value
                    kind = "boolean" if isinstance(value, bool) else "string" if isinstance(value, str) else "float"
                    types[node.left.id] = kind
        if isinstance(node, ast.Name) and node.id not in _KEYWORDS:
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", node.id):
                raise HandoffError("E_HANDOFF_EXPR", f"rule #{index}: field {node.id!r} must be snake_case "
                                   "(HSF field names are lower-case)")
            types.setdefault(node.id, "float")
    return types


def _outcome(then: str) -> dict:
    match = re.match(r"([^\s:=]+)\s*[:=]\s*(.+)", then.strip())
    status, reason = (match.group(1).upper(), match.group(2).strip()) if match else ("APPROVED", then.strip())
    return {"status": status, "reason": reason}


def _hsf_has_inclusive_bounds() -> bool:
    try:
        from hsf.spec.models import FieldSpec
    except ImportError:
        return False
    return "min_inclusive" in getattr(FieldSpec, "model_fields", {})


def _schema(types: dict[str, str]) -> dict:
    """Numbers are bounded to [0, 1e6]. HSF bounds are exclusive unless `min_inclusive` exists, so older HSF
    gets a minimum just below zero; either way an amount of exactly 0 reaches the rules."""
    low = {"min": 0.0, "min_inclusive": True} if _hsf_has_inclusive_bounds() else {"min": -0.000001}
    out = {}
    for name in sorted(types):
        kind = types[name]
        out[name] = {"type": "float", **low, "max": 1000000.0} if kind == "float" else kind
    return out


def build_spec(rows: list[dict], feature: str, owner: str = "specline") -> dict:
    types: dict[str, str] = {}
    rules = []
    for index, row in enumerate(rows, 1):
        cond = row["if"].strip()
        outcome = _outcome(row["then"])
        if cond.lower() in ELSE_WORDS:
            rules.append({"else": outcome})
            continue
        types.update(_field_types(cond, index))
        rules.append({"if": cond, "then": outcome})
    if not any("else" in rule for rule in rules):
        rules.append({"else": {"status": "HUMAN_REVIEW", "reason": "No rule matched"}})
    # HUMAN_REVIEW and DENIED are what HSF's bounds guards return, so they are always declared.
    statuses = sorted({(rule.get("then") or rule.get("else"))["status"] for rule in rules} | {"HUMAN_REVIEW", "DENIED"})
    malformed = [status for status in statuses if not re.fullmatch(r"[A-Z][A-Z0-9_]*", status)]
    if malformed:
        raise HandoffError("E_HANDOFF_STATUS", f"statuses {malformed} must be UPPER_SNAKE identifiers")
    return {"workflow_spec": workflow_id(feature), "version": 1,
            "metadata": {"owner": owner, "compliance": []},
            "inputs": {"input_data": {"text": "string"}},
            "steps": [{"id": "extract_facts", "type": "bounded_invocation", "schema": _schema(types),
                       "on_out_of_bounds": "human_review"},
                      {"id": "decide", "type": "branch", "rules": rules}],
            "outputs": {"AuthResult": {"status": f"enum[{', '.join(statuses)}]", "reason": "string"}}}


def validate_with_hsf(text: str) -> str:
    """'validated', or 'not validated: install code-factory-3-compile'. Raises E_HANDOFF_INVALID on rejection."""
    try:
        from hsf.spec.loader import SpecError, load_spec
    except ImportError:
        return "not validated: install code-factory-3-compile"
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / "handoff.yaml"
        staged.write_text(text, encoding="utf-8")
        try:
            load_spec(staged)
        except (SpecError, ValueError) as exc:
            raise HandoffError("E_HANDOFF_INVALID", str(exc)) from None
    return "validated"


def _require_spec_gate(root: Path, feature: str) -> None:
    spec = root / "specs" / f"{feature}.md"
    progress = root / "context" / "PROGRESS.md"
    import hashlib
    sha = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
    text = progress.read_text(encoding="utf-8", errors="replace") if progress.exists() else ""
    if f"GATE spec {feature} " not in text or f"sha={sha}" not in text:
        raise HandoffError("E_HANDOFF_UNGATED", f"spec {feature!r} has no current spec gate; run "
                           f"`specline gate spec {feature}` (or pass --draft)")


def handoff_to_hsf(root: Path, feature: str, owner: str = "specline", *, draft: bool = False) -> Path:
    root = Path(root)
    rows = decision_rows(root / "specs" / f"{feature}.md")
    if not rows:
        raise HandoffError("E_HANDOFF_NO_TABLE", "no decision table found in spec (## Decision logic section)")
    if not draft:
        _require_spec_gate(root, feature)
    spec = build_spec(rows, feature, owner)
    text = yaml.safe_dump(spec, sort_keys=False)
    status = validate_with_hsf(text)
    out = root / "handoff" / f"{spec['workflow_spec']}.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    handoff_to_hsf.last_status = status  # type: ignore[attr-defined]
    return out


def next_steps(out: Path) -> str:
    spec_id = out.stem
    return (f"HSF spec: {out} ({getattr(handoff_to_hsf, 'last_status', 'validated')})\n"
            f"next: write reviewed golden cases to goldens/{spec_id}/cases.jsonl, one JSON object per line:\n"
            '  {"case_id": "c00", "input_text": "...", "extracted": {<fields>}, '
            '"expected": {"status": "...", "reason": "..."}}\n'
            f"  (`hsf goldens-draft {out}` drafts the inputs for review)\n"
            f"then: hsf compile {out}")
