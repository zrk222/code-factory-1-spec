"""SL-5a: `specline evidence --init` drafts the two hashed JSON inputs; the draft never validates, and a completed
draft does."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from specline.acceptance import draft_evidence, export_acceptance_envelope


def _complete(project, contract: dict, observations: dict) -> tuple[str, str]:
    review = {"path": ".factory/acceptance-review.md", "kind": "review",
              "sha256": hashlib.sha256((project / ".factory/acceptance-review.md").read_bytes()).hexdigest()}
    for row in contract["applicability"]:  # the human's decision
        row["applicable"] = row["category"] == "happy_path"
        row["reason"] = "Reviewed for this feature."
        if not row["applicable"]:
            row["review_evidence"] = review
    contract_path = project / ".factory/workflow-audit-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    observations["contract_sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    for row in observations["observations"]:  # the recorder's results
        row.update(status="PASS", reason="Recorded task verification.", execution_identity="local subprocess",
                   observed_at=datetime.now(timezone.utc).isoformat())
    (project / ".factory/workflow-audit-observations.json").write_text(json.dumps(observations), encoding="utf-8")
    return ".factory/workflow-audit-contract.json", ".factory/workflow-audit-observations.json"


def test_drafts_are_written_and_never_claim_pass(evidence_project):
    project, _, _ = evidence_project
    result = draft_evidence(project, "refunds")
    contract_text = (project / result["drafts"]["contract"]).read_text(encoding="utf-8")
    observations_text = (project / result["drafts"]["observations"]).read_text(encoding="utf-8")
    assert result["drafts"]["contract"].endswith(".draft.json") and result["checks"] > 0
    assert '"PASS"' not in contract_text + observations_text
    contract = json.loads(contract_text)
    assert all(row["applicable"] is None for row in contract["applicability"])
    assert {row["status"] for row in json.loads(observations_text)["observations"]} == {"NOT_RUN"}


def test_untouched_draft_fails_closed(evidence_project):
    project, _, _ = evidence_project
    drafts = draft_evidence(project, "refunds")["drafts"]
    with pytest.raises(SystemExit, match="E_ACCEPTANCE_"):
        export_acceptance_envelope(project, "refunds", drafts["contract"], drafts["observations"])


def test_completed_draft_exports(evidence_project):
    project, _, _ = evidence_project
    drafts = draft_evidence(project, "refunds")["drafts"]
    contract = json.loads((project / drafts["contract"]).read_text(encoding="utf-8"))
    observations = json.loads((project / drafts["observations"]).read_text(encoding="utf-8"))
    contract_path, observations_path = _complete(project, contract, observations)
    envelope = export_acceptance_envelope(project, "refunds", contract_path, observations_path)
    assert envelope["acceptance_evidence"]["completions"].keys() == {"T1", "T2"}
