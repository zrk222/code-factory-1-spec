"""SL-4: the wheel ships templates inside the specline package, never as a top-level `templates/` directory."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    pytest.importorskip("setuptools")
    src = tmp_path_factory.mktemp("src")
    shutil.copytree(ROOT, src / "repo", ignore=shutil.ignore_patterns(".git", "*.egg-info", "build", "__pycache__"))
    out = tmp_path_factory.mktemp("dist")
    done = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q", "-w",
                           str(out), str(src / "repo")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return next(out.glob("*.whl"))


def test_every_wheel_member_lives_under_the_package(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    stray = [name for name in names if not name.startswith("specline/") and ".dist-info/" not in name]
    assert stray == []
    assert "specline/templates/SPEC_TEMPLATE.md" in names and "specline/templates/AGENTS.md" in names


def test_init_works_from_the_installed_wheel(wheel, tmp_path):
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    target = tmp_path / "site"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "-q", "--target", str(target), str(wheel)],
                   check=True)
    project = tmp_path / "proj"
    project.mkdir()
    env = dict(os.environ, PYTHONPATH=str(target))
    done = subprocess.run([str(python), "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                           "import specline; assert specline.__file__.startswith(sys.argv[1]), specline.__file__; "
                           "from specline.cli import main; main(['init'])", str(target)],
                          cwd=project, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (project / "AGENTS.md").exists() and (project / "context" / "PROGRESS.md").exists()
