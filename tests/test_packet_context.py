import pytest

from specline.packets import TOKEN_BUDGET, _est_tokens, build_packet
from specline.plan_lint import lint_plan, task_path_errors


def task(**changes):
    return {
        "id": "T1", "text": "Build intake", "slice": "slices/refunds",
        "files": ["slices/refunds/intake.py", "tests/test_intake.py"],
        "verify": "python -c \"print('ok')\"", **changes,
    }


def link_or_skip(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"host does not permit symbolic links: {exc}")


def test_packet_preserves_complete_unmatched_and_nested_requirements(project):
    spec = project / "specs/refunds.md"
    additional = (
        "\n## Global invariants\n"
        "- Every record is isolated.\n"
        "  - Unless authorized, access is denied.\n"
        "    Preserve original identity.\n"
        "\n```gherkin\nScenario: denied access\n"
        "  Given an unauthenticated visitor\n  Then nothing is disclosed\n```\n"
    )
    # Cross the old 2,600-character excerpt limit without exceeding the packet cap.
    spec.write_text(spec.read_text() + additional + "\nContext: " + "x" * 2100)
    expected = spec.read_text()
    out, meta = build_packet(project, "refunds", task())
    assert expected in out.read_text()
    assert meta["tokens_est"] == _est_tokens(out.read_text()) <= TOKEN_BUDGET


@pytest.mark.parametrize("oversized", ["spec", "task"])
def test_packet_budget_rejects_without_replacing_previous_packet(project, oversized):
    out, _ = build_packet(project, "refunds", task())
    previous = out.read_bytes()
    value = task()
    if oversized == "spec":
        (project / "specs/refunds.md").write_text("A" * 10000)
    else:
        value["text"] = "A" * 10000
    with pytest.raises(SystemExit, match="E_PACKET_BUDGET.*split"):
        build_packet(project, "refunds", value)
    assert out.read_bytes() == previous


def test_budget_rounds_up_and_accepts_exact_character_boundary():
    assert _est_tokens("x" * (TOKEN_BUDGET * 4)) == TOKEN_BUDGET
    assert _est_tokens("x" * (TOKEN_BUDGET * 4 + 1)) == TOKEN_BUDGET + 1


@pytest.mark.parametrize("name", [
    "slices/refunds_extra/bad.py", "tests_extra/bad.py", "../outside.py",
    "slices/refunds/../../../outside.py", r"..\outside.py",
    "/outside.py", r"C:\outside.py", r"C:outside.py", r"\\server\share\file.py",
    "skeleton/core.py",
])
def test_task_paths_reject_escape_and_prefix_lookalikes(project, name):
    value = task(files=[name])
    assert task_path_errors(project, value)
    with pytest.raises(SystemExit, match="E_SLICE_ESCAPE|E_SKELETON_TASK"):
        build_packet(project, "refunds", value)
    assert not (project / "packets/refunds-T1.md").exists()


def test_plan_linter_uses_canonical_containment(project):
    plan = project / "plans/refunds.md"
    plan.write_text(plan.read_text().replace("slices/refunds/intake.py", "slices/refunds_extra/intake.py"))
    _, errors = lint_plan(plan)
    assert any("E_SLICE_ESCAPE" in error for error in errors)


@pytest.mark.parametrize("scope", [".", "./", "../"])
def test_repository_wide_task_slice_is_rejected(project, scope):
    with pytest.raises(SystemExit, match="E_SLICE_ESCAPE"):
        build_packet(project, "refunds", task(slice=scope, files=["specline/cli.py"]))
    assert not (project / "packets/refunds-T1.md").exists()


def test_packet_budget_exact_boundary_preserves_previous_packet(project):
    out, _ = build_packet(project, "refunds", task())
    spec = project / "specs/refunds.md"
    original = spec.read_text()
    padding = TOKEN_BUDGET * 4 - len(out.read_text())
    assert padding > 0
    spec.write_text(original + "x" * padding)
    out, meta = build_packet(project, "refunds", task())
    assert len(out.read_text()) == TOKEN_BUDGET * 4
    assert meta["tokens_est"] == TOKEN_BUDGET
    previous = out.read_bytes()
    spec.write_text(spec.read_text() + "x")
    with pytest.raises(SystemExit, match="E_PACKET_BUDGET"):
        build_packet(project, "refunds", task())
    assert out.read_bytes() == previous


@pytest.mark.parametrize("feature,identity", [("../outside", "T1"), ("refunds", "../../outside")])
def test_invalid_packet_identifiers_are_rejected(project, feature, identity):
    with pytest.raises(SystemExit, match="E_PACKET_ID"):
        build_packet(project, feature, task(id=identity))


@pytest.mark.parametrize("relative,directory", [
    ("slices/refunds", True), ("specs/refunds.md", False), ("packets", True),
])
def test_packet_rejects_symlink_escape(project, tmp_path, relative, directory):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    target = outside if directory else outside / "spec.md"
    if not directory:
        target.write_text("must stay unchanged")
    link = project / relative
    if link.exists():
        if link.is_dir():
            link.rmdir()
        else:
            link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link_or_skip(link, target, directory)
    before = sorted(p.name for p in outside.iterdir())
    with pytest.raises(SystemExit, match="E_SLICE_ESCAPE|E_PACKET_PATH"):
        build_packet(project, "refunds", task())
    assert sorted(p.name for p in outside.iterdir()) == before
    if not directory:
        assert target.read_text() == "must stay unchanged"


@pytest.mark.parametrize("target_name", ["specs/refunds.md", "packets/other-T1.md"])
def test_packet_rejects_internal_output_link(project, target_name):
    output = project / "packets/refunds-T1.md"
    target = project / target_name
    if not target.exists():
        target.write_text("other packet must stay unchanged")
    before = target.read_bytes()
    link_or_skip(output, target)
    with pytest.raises(SystemExit, match="E_PACKET_PATH"):
        build_packet(project, "refunds", task())
    assert target.read_bytes() == before


def test_portable_task_paths_accept_backslash_separator(project):
    assert not task_path_errors(project, task(files=[r"slices\refunds\intake.py"]))
