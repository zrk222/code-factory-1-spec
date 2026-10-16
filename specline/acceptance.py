"""Optional shared SpecLine/CF/ForgeLine declared-acceptance evidence contract.

Validates recorded evidence integrity and explicit links, not semantic correctness
or independent observation. No command, model, approval or release is executed.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from pathlib import Path

from .gates import atomic_text, plan_identity, require_plan_approval
from .loop import _candidate, _completion
from .plan_lint import contained_path, lint_plan
from .strict_lint import _requirement_lines

SCHEMA = "factory.acceptance-evidence.v1"
CONTRACT_FIELDS = {"schema", "project_id", "candidate", "applicability", "checks"}
OBSERVATION_FIELDS = {"schema", "contract_sha256", "candidate_sha256", "observations"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _code_identity(package: Path) -> dict:
    files = sorted(package.rglob("*.py"))
    if len(files) > 1024:
        raise SystemExit("E_ACCEPTANCE_BUDGET: verifier package exceeds file bound")
    manifest, total = {}, 0
    for path in files:
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > 64 * 1024 * 1024:
                    raise SystemExit("E_ACCEPTANCE_BUDGET: verifier package exceeds byte bound")
                checksum.update(chunk)
        manifest[path.relative_to(package).as_posix()] = checksum.hexdigest()
    return manifest


def catalogue(root: Path, feature: str) -> list[dict]:
    approval = require_plan_approval(root, feature)
    path = contained_path(root, f"specs/{feature}.md")
    snapshot = path.read_bytes()
    snapshot_hash = hashlib.sha256(snapshot).hexdigest()
    if snapshot_hash != approval["spec_sha256"]:
        raise SystemExit("E_ACCEPTANCE_DRIFT: specification snapshot differs from approval")
    try:
        text = io.TextIOWrapper(io.BytesIO(snapshot), encoding="utf-8").read()
    except UnicodeDecodeError:
        # Existing Windows projects may use the locale encoding written by older SpecLine.
        text = io.TextIOWrapper(io.BytesIO(snapshot)).read()
    lines = text.splitlines()
    blocks = [f"Review complete approved specification SHA-256 {snapshot_hash}"]
    for number, _ in _requirement_lines(text):
        start = number - 1
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = start + 1
        while end < len(lines):
            line = lines[end]
            if line.lstrip().startswith(("#", "```")):
                break
            if line.lstrip().startswith("- ") and len(line) - len(line.lstrip()) <= indent:
                break
            end += 1
        blocks.append("\n".join(lines[start:end]).rstrip())
    scenarios = [match.group(0) for match in re.finditer(r"```gherkin[^\n]*\n.*?```", text, re.S)]
    if len(blocks) < 2 or not scenarios:
        raise SystemExit("E_ACCEPTANCE_EMPTY: approved spec needs requirements and acceptance scenarios")
    blocks.extend(scenarios)
    return [{"id": "acceptance-" + digest([index, block]), "text": block}
            for index, block in enumerate(blocks)]


def _verifier():
    try:
        from factoryline import workflow_audit
        assert callable(workflow_audit.audit_workflows) and callable(workflow_audit._load_input)
    except (ImportError, AttributeError, AssertionError) as exc:
        raise SystemExit("E_ACCEPTANCE_UNSUPPORTED: install CF with workflow-audit v1 support") from exc
    return workflow_audit


def _task_records(root: Path, feature: str) -> tuple[dict, dict]:
    tasks, errors = lint_plan(root / "plans" / f"{feature}.md")
    if errors:
        raise SystemExit("E_ACCEPTANCE_PLAN: " + "; ".join(errors))
    records, sources = {}, {}
    for task in tasks:
        record = _completion(root, feature, task)
        if record is None or record.get("returncode") != 0:
            raise SystemExit(f"E_ACCEPTANCE_TASK: verify task {task['id']} before exporting")
        records[task["id"]] = record["seal"]
        for name, value in _candidate(root, task).items():
            if value is None:
                raise SystemExit("E_ACCEPTANCE_SOURCE: declared task file is missing")
            sources[contained_path(root, name).relative_to(root).as_posix()] = value
    return records, sources


def _links(root, feature, contract, observations, obligations, records):
    checks = {check["id"]: check for check in contract["checks"]}
    applicable = {item["category"]: item["applicable"] for item in contract["applicability"]}
    rows = {(item["id"], item["profile"]): item for item in observations["observations"]}
    receipts = {contained_path(root, f".factory/specline/{feature}/{task}.json"): task for task in records}
    links = {}
    for item in obligations:
        check = checks.get(item["id"], {})
        if check.get("requirement") != item["text"] or not applicable.get(check.get("category")):
            raise SystemExit("E_ACCEPTANCE_LINK: every catalogue obligation needs an applicable exact check")
        if not check.get("profiles"):
            raise SystemExit("E_ACCEPTANCE_LINK: obligation profiles must not be empty")
        profiles = {}
        for profile in check["profiles"]:
            row = rows.get((item["id"], profile), {})
            tasks = sorted({receipts[path] for artifact in row.get("artifacts", [])
                            if (path := contained_path(root, artifact["path"])) in receipts})
            if row.get("status") != "PASS" or not tasks:
                raise SystemExit("E_ACCEPTANCE_LINK: each required profile needs PASS and current task receipt evidence")
            profiles[profile] = tasks
        links[item["id"]] = profiles
    return links


def _workflow(root, feature, contract_path, observations_path, obligations, sources, records):
    verifier = _verifier()
    verifier_hash = hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest()
    try:
        receipt = verifier.audit_workflows(root, contract_path, observations_path)
        contract, current_hash = verifier._load_input(
            root, contract_path, "factory.workflow-audit.contract.v1", CONTRACT_FIELDS,
        )
        observations, observed_hash = verifier._load_input(
            root, observations_path, "factory.workflow-audit.observations.v1", OBSERVATION_FIELDS,
        )
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise SystemExit("E_ACCEPTANCE_WORKFLOW: invalid or unavailable workflow evidence") from exc
    if (receipt["state"] != "PASS" or current_hash != receipt["contract_sha256"]
            or observed_hash != receipt["observations_sha256"]):
        raise SystemExit("E_ACCEPTANCE_WORKFLOW: workflow evidence is incomplete or changed")
    links = _links(root, feature, contract, observations, obligations, records)
    required = dict(sources)
    for name in (f"specs/{feature}.md", f"plans/{feature}.md"):
        path = contained_path(root, name)
        required[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    candidate = {item["path"]: item["sha256"] for item in contract["candidate"]}
    if any(candidate.get(name) != value for name, value in required.items()):
        raise SystemExit("E_ACCEPTANCE_CANDIDATE: workflow must bind every task file and governing spec/plan")
    return {
        "contract": {"path": contract_path, "sha256": receipt["contract_sha256"]},
        "observations": {"path": observations_path, "sha256": receipt["observations_sha256"]},
        "candidate_sha256": receipt["candidate_sha256"],
        "workflow_verifier_sha256": verifier_hash, "links": links,
    }


def _final_inputs(root, evidence):
    verifier = _verifier()
    try:
        final = verifier.audit_workflows(root, evidence["contract"]["path"], evidence["observations"]["path"])
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise SystemExit("E_ACCEPTANCE_DRIFT: evidence changed during export") from exc
    if (final["state"] != "PASS" or final["contract_sha256"] != evidence["contract"]["sha256"]
            or final["observations_sha256"] != evidence["observations"]["sha256"]
            or final["candidate_sha256"] != evidence["candidate_sha256"]):
        raise SystemExit("E_ACCEPTANCE_DRIFT: evidence changed during export")
    if hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest() != evidence["workflow_verifier_sha256"]:
        raise SystemExit("E_ACCEPTANCE_DRIFT: workflow verifier changed during export")


def acceptance_envelope(root: Path, feature: str, contract_path: str, observations_path: str) -> dict:
    verifier_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    cf_package = Path(_verifier().__file__).parent
    code_identity = {"specline": _code_identity(Path(__file__).parent),
                     "factoryline": _code_identity(cf_package),
                     "python": [sys.implementation.name, sys.version.split()[0]]}
    root = Path(root).resolve()
    approval = require_plan_approval(root, feature)
    identity = plan_identity(root, feature)
    obligations = catalogue(root, feature)
    records, sources = _task_records(root, feature)
    evidence = _workflow(root, feature, contract_path, observations_path, obligations, sources, records)
    # Recheck governing inputs and execution/source identity after evidence reads.
    if (plan_identity(root, feature) != identity
            or require_plan_approval(root, feature)["seal"] != approval["seal"]
            or _task_records(root, feature) != (records, sources)):
        raise SystemExit("E_ACCEPTANCE_DRIFT: approved inputs changed during export")
    _final_inputs(root, evidence)
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != verifier_hash:
        raise SystemExit("E_ACCEPTANCE_DRIFT: acceptance verifier changed during export")
    if (code_identity["specline"] != _code_identity(Path(__file__).parent)
            or code_identity["factoryline"] != _code_identity(cf_package)):
        raise SystemExit("E_ACCEPTANCE_DRIFT: verifier dependency changed during export")
    envelope = {
        "schema": "specline.intent-envelope.v1", "feature": feature,
        "assumptions": [item["text"] for item in obligations], "obligations": obligations,
        "acceptance_evidence": {
            "schema": SCHEMA, "feature": feature,
            "repository_sha256": digest(identity["repository"]),
            "spec_sha256": identity["spec_sha256"], "plan_sha256": identity["plan_sha256"],
            "approval_seal": approval["seal"], "completions": records,
            "sources": sources, **evidence,
            "specline_verifier_sha256": verifier_hash, "verifiers": code_identity,
        },
        "authority": "none",
        "limits": "Declared acceptance links and recorded evidence integrity; not semantic correctness or independent observation.",
    }
    envelope["sealed_hash"] = digest(envelope)
    return envelope


def validate_acceptance_envelope(root: Path, feature: str, envelope: dict) -> dict:
    try:
        evidence = envelope["acceptance_evidence"]
        if evidence["schema"] != SCHEMA:
            raise KeyError("unsupported schema")
        expected = acceptance_envelope(root, feature, evidence["contract"]["path"],
                                       evidence["observations"]["path"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("E_ACCEPTANCE_SCHEMA: unsupported acceptance evidence") from exc
    if envelope != expected:
        raise SystemExit("E_ACCEPTANCE_STALE: regenerate evidence for the current approved candidate")
    return {"schema": SCHEMA, "state": "PASS", "sealed_hash": expected["sealed_hash"],
            "obligations": len(expected["obligations"]), "authority": "none",
            "limits": expected["limits"]}


def export_acceptance_envelope(root: Path, feature: str, contract_path: str, observations_path: str) -> dict:
    envelope = acceptance_envelope(root, feature, contract_path, observations_path)
    root = Path(root).resolve()
    target = root / "envelopes" / f"{feature}.json"
    if target.resolve() != target:
        raise SystemExit("E_ACCEPTANCE_PATH: envelope output may not redirect through links")
    output = json.dumps(envelope, indent=2, ensure_ascii=False)
    if len(output.encode()) > 1_048_576:
        raise SystemExit("E_ACCEPTANCE_BUDGET: split the feature; envelope exceeds 1 MiB")
    atomic_text(target, output, encoding="utf-8")
    return envelope
