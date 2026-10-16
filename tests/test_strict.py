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

### Authorized parameters
- REFUND_LIMIT = 500

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


def test_audit_does_not_cross_authorize_unrelated_spec_numbers(tmp_path):
    spec = _spec(tmp_path, GOOD + "\nAcceptance note: 30 seconds is enough.\n")
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = 500\n", encoding="utf-8")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_INVENTED_PARAM" for b in rep.blocks)


def test_audit_maps_nested_tuple_values_by_position_and_blocks_dynamic_shapes(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout, other = 5, 99\nconfig, (MAX_RETRIES, rest) = 1, (7, 8)\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    invented = [b.message for b in rep.blocks if b.code == "A_INVENTED_PARAM"]
    assert any("timeout" in message and "5" in message for message in invented)
    assert not any("timeout to 99" in message for message in invented)
    assert any("MAX_RETRIES" in message and "7" in message for message in invented)
    code.write_text("timeout, other = dynamic_values\n")
    unknown = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_INVENTED_PARAM" and "unknown" in b.message for b in unknown.blocks)


def test_audit_parameter_rename_with_same_literal_is_new_drift(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = 500\n", encoding="utf-8")
    baseline = {str(code): b"legacy_value = 500\n"}
    rep = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message for b in rep.blocks)


def test_audit_parameter_rename_detects_multiline_literal_on_unchanged_line(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = (\n    500\n)\n", encoding="utf-8")
    baseline = {str(code): b"legacy_value = (\n    500\n)\n"}
    rep = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message for b in rep.blocks)


def test_audit_flags_parameter_fact_moved_between_function_scopes(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("def new_policy():\n    timeout = 5\n", encoding="utf-8")
    baseline = {str(code): b"def old_policy():\n    timeout = 5\n"}
    rep = audit_code_against_spec(spec, [code], baseline_files=baseline)
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message for b in rep.blocks)


def test_repetitive_baseline_line_comparison_is_bounded_linear_work(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    baseline = ("# repeated\n" * 50_000) + "legacy_value = 500\n"
    current = ("# repeated\n" * 50_000) + "timeout = 500\n"
    code.write_text(current, encoding="utf-8")
    rep = audit_code_against_spec(
        spec, [code], baseline_files={str(code): baseline.encode("utf-8")}
    )
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message for b in rep.blocks)


def test_audit_rejects_invalid_python_baseline_explicitly(tmp_path):
    import pytest

    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = 500\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline Python file cannot be parsed"):
        audit_code_against_spec(spec, [code], baseline_files={str(code): b"x = (\n"})


def test_audit_maps_attribute_targets_in_tuple_destructuring(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("settings.timeout, other = 5, 99\n", encoding="utf-8")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert any(b.code == "A_INVENTED_PARAM" and "timeout" in b.message and "5" in b.message
               for b in rep.blocks)
    assert not any("timeout to 99" in b.message for b in rep.blocks)


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


def test_audit_stub_detection_ignores_strings_and_interface_contracts(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        '"""TODO: historical note"""\n# fixed TODO in this explanation\n'
        'LABEL = "NotImplementedError"\n'
        'from abc import abstractmethod\n'
        'from typing import Protocol\n'
        'class Contract(Protocol):\n'
        '    def execute(self):\n'
        '        raise NotImplementedError\n'
        'class Base:\n'
        '    @abstractmethod\n'
        '    def run(self):\n'
        '        raise NotImplementedError\n'
        'from abc import ABC\n'
        'class ConcreteABC(ABC):\n'
        '    def execute(self):\n'
        '        raise NotImplementedError\n'
    )
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert sum(b.code == "A_STUB_LEFT" for b in rep.blocks) == 1
    assert any("execute" in b.message for b in rep.blocks)


def test_audit_does_not_trust_unbound_abstractmethod_or_protocol_names(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        'class Protocol:\n'
        '    def execute(self):\n'
        '        raise NotImplementedError\n'
        'def abstractmethod(fn):\n'
        '    return fn\n'
        '@abstractmethod\n'
        'def concrete():\n'
        '    raise NotImplementedError\n',
        encoding="utf-8",
    )
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert sum(b.code == "A_STUB_LEFT" for b in rep.blocks) == 2


def test_audit_stub_contract_resolution_handles_nested_and_shadowed_names(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        'from abc import abstractmethod\n'
        'from typing import Protocol\n'
        'def abstractmethod(fn):\n'
        '    return fn\n'
        'class Protocol:\n'
        '    pass\n'
        '@abstractmethod\n'
        'def unfinished():\n'
        '    raise NotImplementedError\n'
        'class Nested(pkg.interfaces.Base):\n'
        '    def execute(self):\n'
        '        raise NotImplementedError\n',
        encoding="utf-8",
    )
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert sum(b.code == "A_STUB_LEFT" for b in rep.blocks) == 2


def test_audit_still_flags_ordinary_not_implemented_raise_and_todo_comment(tmp_path):
    spec = _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("# TODO: finish this\ndef execute():\n    raise NotImplementedError\n")
    rep = audit_code_against_spec(spec, [code], slice_prefix="refund")
    assert sum(b.code == "A_STUB_LEFT" for b in rep.blocks) == 2


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


def test_git_baseline_read_has_deadline_and_aggregate_file_cap(tmp_path, monkeypatch):
    from specline import drift_audit

    def timeout(*args, **kwargs):
        raise drift_audit.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(drift_audit.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="inventory exceeded its time limit"):
        drift_audit._read_baseline_blob(tmp_path, "a" * 40, "x.py")

    monkeypatch.undo()
    too_many = [tmp_path / f"file-{index}.py" for index in range(257)]
    with pytest.raises(ValueError, match="file count exceeds"):
        drift_audit.load_git_baselines(tmp_path, too_many, "a" * 40)


def test_git_baseline_uses_literal_path_and_two_bounded_processes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from specline import drift_audit

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["shell"] is False
        assert kwargs["timeout"] <= drift_audit._GIT_TIMEOUT_SECONDS
        if "ls-tree" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=b"100644 blob " + b"a" * 40 + b" 1\tglob[*].py\0",
            )
        return SimpleNamespace(returncode=0, stdout=b"x")

    monkeypatch.setattr(drift_audit.subprocess, "run", run)
    data = drift_audit._read_baseline_blob(tmp_path, "a" * 40, "glob[*].py")
    assert data == b"x"
    assert len(calls) == 2
    assert calls[0][0][:2] == ["git", "--literal-pathspecs"]


def test_git_baseline_passes_remaining_aggregate_budget_to_each_child(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from specline import drift_audit

    calls = []
    times = iter([1.0, 3.0])
    monkeypatch.setattr(drift_audit.time, "monotonic", lambda: next(times))

    def run(argv, **kwargs):
        calls.append((argv, kwargs["timeout"]))
        if "ls-tree" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=b"100644 blob " + b"a" * 40 + b" 1\tfile.py\0",
            )
        return SimpleNamespace(returncode=0, stdout=b"x")

    monkeypatch.setattr(drift_audit.subprocess, "run", run)
    data = drift_audit._read_baseline_blob(
        tmp_path, "a" * 40, "file.py", deadline=5.0
    )
    assert data == b"x"
    assert [timeout for _argv, timeout in calls] == [4.0, 2.0]


def test_git_baseline_rejects_explicit_zero_deadline(tmp_path, monkeypatch):
    from specline import drift_audit

    monkeypatch.setattr(drift_audit.time, "monotonic", lambda: 1.0)
    with pytest.raises(ValueError, match="aggregate time limit"):
        drift_audit._read_baseline_blob(tmp_path, "a" * 40, "file.py", deadline=0.0)


def test_git_baseline_forward_slash_api_keys_resolve_for_windows_paths(tmp_path):
    import subprocess
    from specline.drift_audit import audit_code_against_spec, load_git_baselines

    _spec(tmp_path, GOOD)
    code = tmp_path / "refund" / "logic.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("timeout = 7\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "specs/feat.md", "refund/logic.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
                          capture_output=True, text=True).stdout.strip()
    code.write_text("timeout = 8\n", encoding="utf-8")
    baselines = load_git_baselines(tmp_path, ["refund/logic.py"], base)
    assert str(code.resolve()) in baselines
    rep = audit_code_against_spec(
        tmp_path / "specs/feat.md", [code], slice_prefix="refund", baseline_files=baselines
    )
    assert any(b.code == "A_INVENTED_PARAM" for b in rep.blocks)


def test_git_baseline_rejects_multiple_literal_path_results(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from specline import drift_audit

    monkeypatch.setattr(
        drift_audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                b"100644 blob " + b"a" * 40 + b" 1\tfile.py\0"
                b"100644 blob " + b"b" * 40 + b" 1\tfile.py\0"
            ),
        ),
    )
    with pytest.raises(ValueError, match="multiple or malformed"):
        drift_audit._read_baseline_blob(tmp_path, "a" * 40, "file.py")


def test_git_baseline_total_bytes_are_bounded(tmp_path, monkeypatch):
    from specline import drift_audit

    files = []
    for index in range(17):
        path = tmp_path / f"file-{index}.py"
        path.write_text("x", encoding="utf-8")
        files.append(path)
    monkeypatch.setattr(
        drift_audit, "_read_baseline_blob",
        lambda *_args: b"x" * drift_audit._MAX_BASELINE_BYTES,
    )
    with pytest.raises(ValueError, match="aggregate size limit"):
        drift_audit.load_git_baselines(tmp_path, files, "a" * 40)


def test_git_baseline_reads_have_one_aggregate_deadline(tmp_path, monkeypatch):
    import pytest
    from specline import drift_audit

    files = []
    for index in range(2):
        path = tmp_path / f"file-{index}.py"
        path.write_text("x", encoding="utf-8")
        files.append(path)
    ticks = iter([0.0, 15.0, 30.0])
    monkeypatch.setattr(drift_audit.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(drift_audit, "_read_baseline_blob", lambda *_args: b"x")
    with pytest.raises(ValueError, match="aggregate time limit"):
        drift_audit.load_git_baselines(tmp_path, files, "a" * 40)


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
