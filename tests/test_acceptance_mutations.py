import hashlib
import json

import pytest

from specline.acceptance import acceptance_envelope, catalogue
from specline.gates import gate


@pytest.mark.parametrize("missing", ["requirements", "scenarios"])
def test_catalogue_requires_both_requirements_and_scenarios(project, monkeypatch, missing):
    import re
    path = project / "specs/refunds.md"
    text = path.read_text()
    if missing == "scenarios":
        text = re.sub(r"```gherkin.*?```", "", text, flags=re.S)
    else:
        text = re.sub(r"^- (?:The system|When |If |While ).*$", "", text, flags=re.M)
    path.write_text(text)
    # Isolate catalogue's defensive check; the normal plan gate also rejects this input.
    monkeypatch.setattr("specline.acceptance.require_plan_approval", lambda *args: {
        "spec_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_EMPTY"):
        catalogue(project, "refunds")



def test_observations_changed_after_audit_are_rejected(evidence_project, monkeypatch):
    root, contract, observations = evidence_project
    from factoryline import workflow_audit
    original = workflow_audit.audit_workflows

    def change_after_audit(*args):
        result = original(*args)
        (root / observations).write_text("{}")
        return result

    monkeypatch.setattr(workflow_audit, "audit_workflows", change_after_audit)
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_WORKFLOW|E_ACCEPTANCE_DRIFT"):
        acceptance_envelope(root, "refunds", contract, observations)


def test_spec_snapshot_cannot_mix_transient_text_with_approved_hash(project, monkeypatch):
    gate(project, "plan", "refunds", strict=False)
    from pathlib import Path
    original = Path.read_bytes
    target = project / "specs/refunds.md"

    def wrong_snapshot(path):
        result = original(path)
        return result + b"\n- The system shall disclose private data." if path == target else result

    # Approval validation performs its own digest read first; change the catalogue's read only.
    calls = 0

    def transient_snapshot(path):
        nonlocal calls
        if path == target:
            calls += 1
            if calls >= 2:
                return wrong_snapshot(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", transient_snapshot)
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_DRIFT"):
        catalogue(project, "refunds")


def test_inapplicable_na_obligation_is_not_acceptance_evidence(evidence_project):
    root, contract_path, observations_path = evidence_project
    path = root / contract_path
    contract = json.loads(path.read_text())
    contract["applicability"][0]["applicable"] = False
    contract["applicability"][0]["review_evidence"] = contract["applicability"][1]["review_evidence"]
    path.write_text(json.dumps(contract))
    path = root / observations_path
    observations = json.loads(path.read_text())
    observations["contract_sha256"] = hashlib.sha256((root / contract_path).read_bytes()).hexdigest()
    for row in observations["observations"]:
        row["status"] = "N/A"
    path.write_text(json.dumps(observations))
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_LINK"):
        acceptance_envelope(root, "refunds", contract_path, observations_path)


def test_task_receipt_must_be_explicit_in_every_obligation_profile(evidence_project):
    root, contract_path, observations_path = evidence_project
    path = root / observations_path
    observations = json.loads(path.read_text())
    observations["observations"][0]["artifacts"] = [
        json.loads((root / contract_path).read_text())["applicability"][1]["review_evidence"]
    ]
    path.write_text(json.dumps(observations))
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_LINK"):
        acceptance_envelope(root, "refunds", contract_path, observations_path)


def test_non_pass_validator_cannot_promote_forge(evidence_project, monkeypatch):
    root, contract, observations = evidence_project
    from specline.acceptance import export_acceptance_envelope
    from forgeline.intent_thread import verify_against_intent
    from forgeline.orchestrator import Orchestrator
    from forgeline.states import State
    envelope = export_acceptance_envelope(root, "refunds", contract, observations)
    trace = verify_against_intent(root, "refunds", root)
    assert trace.traceable
    monkeypatch.setattr("specline.acceptance.validate_acceptance_envelope", lambda *args: {
        "state": "INCOMPLETE", "sealed_hash": envelope["sealed_hash"],
    })
    orchestrator = Orchestrator(root, "refunds")
    orchestrator.store.set_state(State.SMOKED)
    assert orchestrator._record_ship_success(trace, {})["shipped"] is False
    assert orchestrator.store.state == State.SMOKED


def test_empty_obligation_profiles_reject_without_vacuous_links():
    from pathlib import Path
    from specline.acceptance import _links
    contract = {"checks": [{"id": "R1", "requirement": "Required", "category": "happy_path", "profiles": []}],
                "applicability": [{"category": "happy_path", "applicable": True}]}
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_LINK.*profiles"):
        _links(Path.cwd(), "demo", contract, {"observations": []}, [{"id": "R1", "text": "Required"}], {})


@pytest.mark.parametrize("owner", ["spec", "cf"])
def test_verifier_source_identity_must_stay_stable(evidence_project, monkeypatch, owner):
    root, contract, observations = evidence_project
    from pathlib import Path
    from specline import acceptance
    from factoryline import workflow_audit
    target = Path(acceptance.__file__ if owner == "spec" else workflow_audit.__file__)
    original = Path.read_bytes
    reads = 0

    def changed_bytes(path):
        nonlocal reads
        data = original(path)
        if path == target:
            reads += 1
            if reads > 1:
                return data + b"\n# changed verifier\n"
        return data

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_DRIFT.*verifier"):
        acceptance_envelope(root, "refunds", contract, observations)
