import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from specline.acceptance import (
    acceptance_envelope, catalogue, digest, export_acceptance_envelope,
    validate_acceptance_envelope,
)
from specline.gates import gate






def test_export_and_all_three_consumers_share_current_evidence(evidence_project):
    root, contract, observations = evidence_project
    envelope = export_acceptance_envelope(root, "refunds", contract, observations)
    assert validate_acceptance_envelope(root, "refunds", envelope)["state"] == "PASS"
    from forgeline.intent_thread import verify_against_intent
    from forgeline.orchestrator import Orchestrator
    from forgeline.states import State
    from factoryline.assembly import _forge_intent_trace
    trace = verify_against_intent(root, "refunds", root)
    assert trace.traceable and trace.evidence_verified
    assert trace.obligations_met == len(envelope["obligations"])
    orchestrator = Orchestrator(root, "refunds")
    orchestrator.store.set_state(State.SMOKED)
    result = orchestrator._record_ship_success(trace, {})
    assert result["shipped"] and result["intent_evidence"] == envelope
    receipt = _forge_intent_trace(root, "refunds", json.dumps(result))
    assert receipt and receipt["intent_traceable"]
    assert not receipt["authority"]["approval"] and not receipt["authority"]["publication"]


def test_stale_source_is_rejected_by_all_consumers(evidence_project):
    root, contract, observations = evidence_project
    envelope = export_acceptance_envelope(root, "refunds", contract, observations)
    (root / "slices/refunds/intake.py").write_text("def accept(value):\n    return None\n")
    with pytest.raises(SystemExit, match="E_COMPLETION_STALE"):
        validate_acceptance_envelope(root, "refunds", envelope)
    from forgeline.intent_thread import verify_against_intent
    from factoryline.assembly import _validated_forge_acceptance
    assert not verify_against_intent(root, "refunds", root).traceable
    assert not _validated_forge_acceptance(root, "refunds", envelope, envelope["sealed_hash"])


def test_resealed_forged_evidence_is_not_accepted(evidence_project):
    root, contract, observations = evidence_project
    envelope = acceptance_envelope(root, "refunds", contract, observations)
    envelope["acceptance_evidence"]["approval_seal"] = "forged"
    envelope.pop("sealed_hash")
    envelope["sealed_hash"] = digest(envelope)
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_STALE"):
        validate_acceptance_envelope(root, "refunds", envelope)


def test_copied_feature_receipts_cannot_authorize_another_repository(evidence_project, tmp_path):
    root, contract, observations = evidence_project
    envelope = acceptance_envelope(root, "refunds", contract, observations)
    copied = tmp_path.parent / (tmp_path.name + "-copied")
    shutil.copytree(root, copied)
    with pytest.raises(SystemExit, match="E_INTENT_DRIFT"):
        validate_acceptance_envelope(copied, "refunds", envelope)


def test_missing_exact_obligation_link_rejects_even_passing_workflow(evidence_project):
    root, contract_path, observations_path = evidence_project
    path = root / contract_path
    contract = json.loads(path.read_text())
    removed = contract["checks"].pop()
    path.write_text(json.dumps(contract))
    path = root / observations_path
    observations = json.loads(path.read_text())
    observations["contract_sha256"] = hashlib.sha256((root / contract_path).read_bytes()).hexdigest()
    observations["observations"] = [item for item in observations["observations"] if item["id"] != removed["id"]]
    path.write_text(json.dumps(observations))
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_LINK"):
        acceptance_envelope(root, "refunds", contract_path, observations_path)


def test_stale_acceptance_cannot_advance_forge_promotion(evidence_project):
    root, contract, observations = evidence_project
    export_acceptance_envelope(root, "refunds", contract, observations)
    from forgeline.intent_thread import verify_against_intent
    from forgeline.orchestrator import Orchestrator
    from forgeline.states import State
    trace = verify_against_intent(root, "refunds", root)
    assert trace.traceable
    orchestrator = Orchestrator(root, "refunds")
    orchestrator.store.set_state(State.SMOKED)
    (root / "slices/refunds/intake.py").write_text("def accept(value):\n    return None\n")
    assert orchestrator._record_ship_success(trace, {})["shipped"] is False
    assert orchestrator.store.state == State.SMOKED


def test_actual_specline_export_and_cf_workflow_cli(evidence_project):
    root, contract, observations = evidence_project
    commands = [
        [sys.executable, "-m", "specline.cli", "evidence", "refunds", "--root", str(root), "--catalog"],
        [sys.executable, "-m", "factoryline.cli", "audit", "workflows", "--root", str(root), "--contract", contract, "--observations", observations, "--json"],
        [sys.executable, "-m", "specline.cli", "evidence", "refunds", "--root", str(root)],
    ]
    values = []
    for command in commands:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr[-1000:]
        values.append(json.loads(result.stdout))
    assert values[0] == catalogue(root, "refunds")
    assert values[1]["state"] == "PASS"
    assert validate_acceptance_envelope(root, "refunds", values[2])["state"] == "PASS"


def test_standalone_catalogue_does_not_need_cf(project):
    gate(project, "plan", "refunds", strict=False)
    items = catalogue(project, "refunds")
    assert len(items) >= 6
    assert any("```gherkin" in item["text"] for item in items)
    assert len({item["id"] for item in items}) == len(items)


def test_missing_cf_validator_is_explicitly_unsupported():
    source = str(Path(__file__).resolve().parents[1])
    script = f"import sys; sys.path.insert(0, {source!r}); from specline.acceptance import _verifier; _verifier()"
    result = subprocess.run([sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "E_ACCEPTANCE_UNSUPPORTED" in result.stderr
