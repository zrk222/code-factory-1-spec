"""Tests for the strict semantic contract + drift audit — the anti-drift upgrade.
Each test targets one of the five holes that let the AI coder invent parameters.
"""
from pathlib import Path
import textwrap
from specline.strict_lint import strict_validate
from specline.drift_audit import audit_code_against_spec


def _spec(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "specs"; p.mkdir(parents=True, exist_ok=True)
    f = p / "feat.md"; f.write_text(textwrap.dedent(body)); return f


GOOD = """\
# Spec: refund
Status: draft
SpecFactor-target: 0.75

## MUST — Functional core
### Description
Processes refund requests for the `Customer` role against an `Order`.

### User roles
- Customer

### Requirements (EARS)
- When a refund request exceeds `500` dollars, the system shall reject the request and return `403`.
- If the `Order` is older than `90` days, the system shall reject the refund and return `403`.
- The system shall store each approved refund in the `refunds` table.

### Acceptance criteria (Gherkin)
```gherkin
Scenario: oversized refund
  Given a Customer with an Order
  When a refund request exceeds 500 dollars
  Then the system returns 403
```

## Decision logic (factory candidates)
| # | if | then |
|---|----|------|
| 1 | refund exceeds `500` | return `403` |
| 2 | `Order` older than `90` days | return `403` |
"""


def test_good_spec_passes_strict(tmp_path):
    rep = strict_validate(_spec(tmp_path, GOOD))
    assert rep.ok, f"expected clean, got: {[str(b) for b in rep.blocks]}"


def test_vague_handle_is_blocked(tmp_path):
    spec = GOOD.replace(
        "- The system shall store each approved refund in the `refunds` table.",
        "- The system shall handle refunds appropriately.")
    rep = strict_validate(_spec(tmp_path, spec))
    codes = {b.code for b in rep.blocks}
    assert "S_VAGUE_TERM" in codes or "S_NO_OUTCOME_VERB" in codes


def test_placeholder_blocked(tmp_path):
    spec = GOOD.replace("older than `90` days", "older than <N> days")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_PLACEHOLDER" for b in rep.blocks)


def test_unquantified_bound_blocked(tmp_path):
    spec = GOOD.replace(
        "- When a refund request exceeds `500` dollars, the system shall reject the request and return `403`.",
        "- The system shall enforce a refund timeout and return `403`.")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_UNQUANTIFIED_BOUND" for b in rep.blocks)


def test_untraceable_gherkin_blocked(tmp_path):
    spec = GOOD.replace("Then the system returns 403",
                        "Then the system applies a `PLATINUM_DISCOUNT`")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_UNTRACEABLE_STEP" for b in rep.blocks)


def test_nondeterministic_rule_blocked(tmp_path):
    spec = GOOD.replace("| 1 | refund exceeds `500` | return `403` |",
                        "| 1 | refund exceeds `500` | return `403` or maybe `402` |")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_RULE_NONDETERMINISTIC" for b in rep.blocks)


def test_rule_undefined_fact_blocked(tmp_path):
    spec = GOOD.replace("| 2 | `Order` older than `90` days | return `403` |",
                        "| 2 | customer vibes are off | return `403` |")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_RULE_UNDEFINED_FACT" for b in rep.blocks)


def test_approved_while_ambiguous_blocked(tmp_path):
    spec = GOOD.replace("Status: draft", "Status: approved").replace(
        "older than `90` days", "older than <N> days")
    rep = strict_validate(_spec(tmp_path, spec))
    assert any(b.code == "S_APPROVED_BUT_AMBIGUOUS" for b in rep.blocks)


# ---- Drift audit ----

def test_audit_flags_invented_param(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("MAX_RETRIES = 7\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_INVENTED_PARAM" for b in rep.blocks)


def test_audit_accepts_authorized_number(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("REFUND_LIMIT = 500\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert not any(b.code == "A_INVENTED_PARAM" for b in rep.blocks)


def test_audit_flags_scope_escape(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "billing" / "other.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("X = 500\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_SCOPE_ESCAPE" for b in rep.blocks)


def test_audit_flags_stub(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("def refund():\n    raise NotImplementedError\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_STUB_LEFT" for b in rep.blocks)


def test_strict_attribution_localizes_requirement_verbatim(tmp_path):
    phrase = "The system shall handle refunds appropriately."
    spec = _spec(tmp_path, GOOD.replace(
        "- The system shall store each approved refund in the `refunds` table.",
        f"- {phrase}"))
    rep = strict_validate(spec)
    attr = rep.attribution(spec.read_text())
    assert attr.n_checked == 3
    assert attr.rate == 2 / 3
    assert phrase in attr.units[2].evidence
    assert attr.units[2].failure_class.value == "ambiguous_requirement"


def test_drift_attribution_classifies_invented_param(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("MAX_RETRIES = 7\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    attr = rep.attribution([code])
    assert attr.n_checked == 1 and attr.n_passed == 0
    assert attr.units[0].failure_class.value == "invented_param"
