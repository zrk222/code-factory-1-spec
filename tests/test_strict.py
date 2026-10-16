"""Tests for the strict semantic contract + drift audit — the anti-drift upgrade.
Each test targets one of the five holes that let the AI coder invent parameters.
"""
from pathlib import Path
import pytest
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


VALIDATED = """\
# Spec: validator coverage
Status: approved

## MUST — Functional core
### Description
Processes refund decisions for the `Customer` role.

### User roles
- Customer

### Requirements (EARS)
- When a `PAYMENT_CAPTURED` event arrives, the system shall store `PAYMENT_CAPTURED`.
- When a refund request exceeds `500` dollars, the system shall return `403`.
- The system shall store each approved refund in the `refunds` table.

### Acceptance criteria (Gherkin)
```gherkin
Scenario: validator coverage
  Given a `PAYMENT_CAPTURED` event
  When a refund request exceeds 500 dollars
  Then the system returns 403 and writes `refunds`
```

## Decision logic (factory candidates)
| # | if | then |
|---|----|------|
| 1 | `PAYMENT_CAPTURED` | store `PAYMENT_CAPTURED` |
| 2 | refund exceeds `500` | return `403` |
| 3 | `refunds` | write `refunds` |
"""


def test_validator_mutation_passes_when_each_requirement_is_killed(tmp_path):
    from specline.validator_mutation import verify_validators

    rep = verify_validators(_spec(tmp_path, VALIDATED))
    assert rep.ok, [str(block) for block in rep.blocks]
    attr = rep.attribution()
    assert attr.n_checked == 3
    assert attr.n_passed == 3


def test_challenge_writes_standard_receipt(tmp_path, capsys):
    from specline.cli import main
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "validator.md").write_text(VALIDATED)
    out = tmp_path / "challenge.json"
    main(["challenge", "validator", "--root", str(tmp_path), "--out", str(out)])
    import json
    payload = json.loads(out.read_text())
    assert payload["schema"] == "factory.challenge.v1"
    assert payload["passed"] is True
    assert payload["mutants_killed"] == payload["mutants_total"] == 3


def test_validator_mutation_flags_hollow_requirement(tmp_path):
    from specline.validator_mutation import verify_validators

    hollow = VALIDATED.replace(
        "### Acceptance criteria (Gherkin)",
        "- The system shall emit `EMAIL_RECEIPT` for approved refunds.\n\n"
        "### Acceptance criteria (Gherkin)",
    )
    rep = verify_validators(_spec(tmp_path, hollow))
    assert not rep.ok
    assert any(block.code == "S_HOLLOW_VALIDATOR" for block in rep.blocks)
    attr = rep.attribution()
    assert attr.n_checked == 4
    assert attr.n_passed == 3
    assert attr.units[3].failure_class.value == "hollow_validator"


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


def test_audit_ast_ignores_string_numbers_and_subscripts(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text('encoding = "utf-8"\nitem = values[3]\n')
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert not any(b.code == "A_INVENTED_PARAM" for b in rep.blocks)


def test_audit_baseline_checks_only_added_or_changed_parameter_lines(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = 6\n", encoding="utf-8")
    baseline = {str(code): b"timeout = 6\n"}
    same = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert not any(b.code == "A_INVENTED_PARAM" for b in same.blocks)
    code.write_text("timeout = 7\n", encoding="utf-8")
    changed = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert any(b.code == "A_INVENTED_PARAM" for b in changed.blocks)


def test_audit_baseline_new_file_and_todo_still_block(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("retries = 7\n# TODO: finish\n", encoding="utf-8")
    rep = audit_code_against_spec(spec, [code], baseline_files={str(code): None})
    assert {b.code for b in rep.blocks} >= {"A_INVENTED_PARAM", "A_STUB_LEFT"}


def test_audit_rejects_invalid_utf8_baseline(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("retries = 7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="UTF-8"):
        audit_code_against_spec(spec, [code], baseline_files={str(code): b"\xff"})
    with pytest.raises(ValueError, match="missing an explicit entry"):
        audit_code_against_spec(spec, [code], baseline_files={})


def test_audit_handles_annotation_without_value_and_attribute_config(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("MAX_RETRIES: int\nself.timeout = 7\n", encoding="utf-8")
    rep = audit_code_against_spec(spec, [code])
    assert sum(b.code == "A_INVENTED_PARAM" for b in rep.blocks) == 1
    assert any("timeout" in b.message for b in rep.blocks)


def test_audit_toml_ignores_quoted_dependency_version_but_flags_numeric_timeout(tmp_path):
    spec = _spec(tmp_path, GOOD)
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\ndependencies = ["sample>=7"]\n', encoding="utf-8")
    safe = audit_code_against_spec(spec, [config])
    assert not any(b.code == "A_INVENTED_PARAM" for b in safe.blocks)
    config.write_text('[project]\ndependencies = ["sample>=7"]\n[tool.service]\ntimeout = 7\n',
                      encoding="utf-8")
    changed = audit_code_against_spec(spec, [config])
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message for b in changed.blocks)


def test_audit_javascript_config_ignores_strings_and_comments_but_flags_constant(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "settings.js"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text('const note = "MAX_RETRIES = 8"; // timeout = 9\nconst MAX_RETRIES = 7;\n',
                    encoding="utf-8")
    rep = audit_code_against_spec(spec, [code])
    assert sum(b.code == "A_INVENTED_PARAM" for b in rep.blocks) == 1
    assert any("MAX_RETRIES" in b.message for b in rep.blocks)


def test_audit_multiline_python_value_checks_changed_literal_line(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("MAX_RETRIES = (\n    7\n)\n", encoding="utf-8")
    baseline = {str(code): b"MAX_RETRIES = (\n    500\n)\n"}
    rep = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert any(b.code == "A_INVENTED_PARAM" and b.line == 2 for b in rep.blocks)


def test_audit_baseline_mode_rejects_current_and_baseline_over_one_megabyte(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    oversized = b"x" * 1_000_001
    code.write_bytes(oversized)
    with pytest.raises(ValueError, match="current file exceeds"):
        audit_code_against_spec(spec, [code], baseline_files={str(code): b""})
    code.write_text("retries = 7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline file exceeds"):
        audit_code_against_spec(spec, [code], baseline_files={str(code): oversized})


def test_audit_cli_accepts_explicit_full_base_commit(tmp_path, capsys):
    import subprocess

    from specline.cli import main

    _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("retries = 6\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "specs/feat.md", "refund/logic.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
                          capture_output=True, text=True).stdout.strip()
    code.write_text("retries = 7\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="AUDIT FAILED"):
        main(["audit", "feat", "--root", str(tmp_path), "--files", str(code),
              "--base-commit", base, "--json"])
    assert "A_INVENTED_PARAM" in capsys.readouterr().out


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
    code.write_text("def send():\n    retries = 7\n    return retries\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    attr = rep.attribution([code])
    assert attr.n_checked == 1 and attr.n_passed == 0
    assert attr.units[0].unit.endswith(":send")
    assert attr.units[0].failure_class.value == "invented_param"


def test_drift_attribution_separates_functions(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        "def clean():\n    return 500\n\n"
        "def drifting():\n    retries = 7\n    return retries\n"
    )
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    attr = rep.attribution([code])
    assert attr.n_checked == 2 and attr.n_passed == 1
    by_name = {unit.unit.rsplit(":", 1)[-1]: unit for unit in attr.units}
    assert by_name["clean"].passed is True
    assert by_name["drifting"].failure_class.value == "invented_param"
