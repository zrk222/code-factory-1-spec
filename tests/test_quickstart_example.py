"""SL-3: the shipped example passes every README quickstart step, files are UTF-8 regardless of locale, and the
small ergonomics fixes (optimize-prd by feature, literals in vague-term scan, phase line) hold."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "refund-portal"
QUICKSTART = [
    ["init"], ["new", "refunds"], ["validate", "refunds"], ["strict", "refunds"], ["verify-validators", "refunds"],
    ["optimize-prd", "refunds"], ["gate", "spec", "refunds"], ["tasks", "refunds"], ["gate", "plan", "refunds"],
    ["loop", "next", "refunds"], ["loop", "done", "refunds", "T1"], ["handoff", "refunds"], ["challenge", "refunds"],
    ["agent", "claude"], ["status"]]


def _run(argv, cwd, **env):
    environ = dict(os.environ, PYTHONPATH=str(ROOT), **env)
    return subprocess.run([sys.executable, "-m", "specline.cli", *argv], cwd=cwd, env=environ, capture_output=True,
                          text=True, encoding="utf-8")


def _workspace(tmp_path: Path) -> Path:
    for argv in QUICKSTART[:2]:
        assert _run(argv, tmp_path).returncode == 0
    for kind in ("specs", "plans"):
        shutil.copyfile(EXAMPLE / kind / "refunds.md", tmp_path / kind / "refunds.md")
    return tmp_path


def test_every_quickstart_step_passes_on_the_example(tmp_path):
    work = _workspace(tmp_path)
    for argv in QUICKSTART[2:]:
        done = _run(argv, work)
        assert done.returncode == 0, (argv, done.stdout[-600:], done.stderr[-600:])
    hsf_loader = pytest.importorskip("hsf.spec.loader")
    hsf_loader.load_spec(work / "handoff" / "refunds_decisions.yaml")


def test_progress_phase_follows_the_gates(tmp_path):
    work = _workspace(tmp_path)
    progress = work / "context" / "PROGRESS.md"
    assert "## Current phase\nspecify" in progress.read_text(encoding="utf-8")
    assert _run(["gate", "spec", "refunds"], work).returncode == 0
    assert "## Current phase\nplan" in progress.read_text(encoding="utf-8")
    assert _run(["gate", "plan", "refunds"], work).returncode == 0
    assert "## Current phase\nbuild" in progress.read_text(encoding="utf-8")


def test_spec_with_an_em_dash_survives_an_ascii_locale(tmp_path):
    work = _workspace(tmp_path)
    spec = work / "specs" / "refunds.md"
    before = spec.read_bytes()
    assert "—".encode() in before
    done = _run(["gate", "spec", "refunds"], work, PYTHONUTF8="0", LC_ALL="C", LANG="C")
    assert done.returncode == 0, done.stderr
    assert spec.read_bytes() == before
    (work / "context" / "PROGRESS.md").read_bytes().decode("utf-8")


def test_new_feature_files_are_utf8_in_an_ascii_locale(tmp_path):
    assert _run(["init"], tmp_path, PYTHONUTF8="0", LC_ALL="C", LANG="C").returncode == 0
    assert _run(["new", "draft"], tmp_path, PYTHONUTF8="0", LC_ALL="C", LANG="C").returncode == 0
    (tmp_path / "specs" / "draft.md").read_bytes().decode("utf-8")


def test_locale_encoded_files_still_load(tmp_path):
    from specline.paths import read_text_with_encoding
    legacy = tmp_path / "legacy.md"
    legacy.write_bytes("caf\xe9".encode("latin-1"))
    text, encoding = read_text_with_encoding(legacy)
    assert encoding != "utf-8" and text.startswith("caf")


def test_optimize_prd_accepts_a_feature_name_and_root(tmp_path):
    work = _workspace(tmp_path)
    by_name = _run(["optimize-prd", "refunds", "--root", str(work)], tmp_path)
    by_path = _run(["optimize-prd", "specs/refunds.md"], work)
    assert by_name.returncode == 0 and by_path.returncode == 0
    strip = lambda out: [line for line in out.splitlines() if "refunds.md" not in line]  # noqa: E731
    assert strip(by_name.stdout) == strip(by_path.stdout)


def test_vague_words_inside_literals_are_not_vague():
    from specline.strict_lint import strict_validate_text
    spec = (EXAMPLE / "specs" / "refunds.md").read_text(encoding="utf-8")
    quoted = spec.replace("Receipt Under Limit", "Small With Receipt")
    assert not [f for f in strict_validate_text(quoted).findings if f.code == "S_VAGUE_TERM"]
    unquoted = spec.replace("is below 100 dollars", "is small")
    assert [f for f in strict_validate_text(unquoted).findings if f.code == "S_VAGUE_TERM"]


def test_template_carries_non_goal_and_risk_sections():
    template = (ROOT / "specline" / "templates" / "SPEC_TEMPLATE.md").read_text(encoding="utf-8")
    assert "## Non-goals" in template and "## Risks and rollback" in template


@pytest.mark.parametrize("name", ["README.md", "pyproject.toml"])
def test_no_mojibake(name):
    raw = (ROOT / name).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "â€" not in raw.decode("utf-8") and "ðŸ" not in raw.decode("utf-8")
