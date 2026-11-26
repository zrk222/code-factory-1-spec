"""SL-2: every YAML `specline handoff` emits is one HSF accepts, or the hand-off fails with an E_HANDOFF_ code."""
from __future__ import annotations

import pytest
import yaml

from specline.handoff import HandoffError, build_spec, handoff_to_hsf, workflow_id


def _rows(*pairs):
    return [{"if": cond, "then": then} for cond, then in pairs]


def _load(spec: dict, tmp_path):
    hsf_loader = pytest.importorskip("hsf.spec.loader")
    path = tmp_path / "x.yaml"
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return hsf_loader.load_spec(path)


def test_hyphenated_feature_gets_an_id_hsf_accepts(tmp_path):
    assert workflow_id("refund-portal") == "refund_portal_decisions"
    assert workflow_id("2fast") == "f_2fast_decisions"
    spec = build_spec(_rows(("item_damaged == true", "APPROVED: Damaged"), ("else", "DENIED: No")), "refund-portal")
    loaded, _ = _load(spec, tmp_path)
    assert loaded.workflow_spec == "refund_portal_decisions"


def test_string_comparisons_become_one_string_field(tmp_path):
    spec = build_spec(_rows(('tier == "gold"', "APPROVED: Gold"), ("else", "DENIED: No")), "tiers")
    schema = spec["steps"][0]["schema"]
    assert schema == {"tier": "string"}
    _load(spec, tmp_path)


def test_mixed_case_field_is_rejected_with_a_code():
    with pytest.raises(HandoffError, match="E_HANDOFF_EXPR"):
        build_spec(_rows(("Amount >= 100", "APPROVED: Big")), "f")


def test_unparseable_condition_is_rejected_with_a_code():
    with pytest.raises(HandoffError, match="E_HANDOFF_EXPR: rule #1"):
        build_spec(_rows(("amount >>= 1", "APPROVED: x")), "f")


def test_status_enum_comes_from_the_table(tmp_path):
    spec = build_spec(_rows(("score > 5", "ESCALATE: Senior review"), ("else", "APPROVED: Fine")), "esc")
    assert "ESCALATE" in spec["outputs"]["AuthResult"]["status"]
    _load(spec, tmp_path)
    with pytest.raises(HandoffError, match="E_HANDOFF_STATUS"):
        build_spec(_rows(("score > 5", "escalate-now: x"),), "esc")


def test_zero_amount_reaches_the_rules(tmp_path):
    spec = build_spec(_rows(("amount < 100", "APPROVED: Small"), ("else", "DENIED: Big")), "amounts")
    field = spec["steps"][0]["schema"]["amount"]
    assert field["min"] < 0 or field.get("min_inclusive") is True
    _load(spec, tmp_path)


def test_handoff_refuses_an_ungated_spec_unless_draft(project):
    with pytest.raises(HandoffError, match="E_HANDOFF_UNGATED"):
        handoff_to_hsf(project, "refunds")
    out = handoff_to_hsf(project, "refunds", draft=True)
    assert out.name == "refunds_decisions.yaml"


def test_every_emitted_spec_round_trips_through_hsf(project, tmp_path):
    hsf_loader = pytest.importorskip("hsf.spec.loader")
    from specline.gates import gate
    gate(project, "spec", "refunds")
    out = handoff_to_hsf(project, "refunds")
    hsf_loader.load_spec(out)
