"""Test framework + build-system detection for a cloned repo.

Phase 1 supports pytest only. The detector returns a TestFrameworkSpec that
tells the runner how to install deps, run tests, and which parser to use.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class TestFrameworkSpec:
    language: str
    name: str                # pytest, mocha, gotest, ...
    install_cmd: str
    deps_cmd: str
    test_cmd: str
    select_tests_cmd: str    # like test_cmd but with a {files} placeholder
    parser: str              # evaluator/parsers/<parser>.py
    base_image: str
    system_deps: list[str]
    marker_files: list[str]


def load_languages_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text())


def detect(repo_dir: Path, config_path: Path) -> TestFrameworkSpec | None:
    """Walk the repo root to decide which (language, test_framework) applies.

    Returns None if no supported framework is detected.
    """
    cfg = load_languages_config(config_path)
    for lang_name, lang_cfg in cfg.items():
        for fw in lang_cfg.get("test_frameworks", []):
            for marker in fw.get("marker_files", []):
                if (repo_dir / marker).exists():
                    return TestFrameworkSpec(
                        language=lang_name,
                        name=fw["name"],
                        install_cmd=fw.get("install_cmd", ""),
                        deps_cmd=fw.get("deps_cmd", ""),
                        test_cmd=fw.get("test_cmd", ""),
                        select_tests_cmd=fw.get("select_tests_cmd", fw.get("test_cmd", "")),
                        parser=fw.get("parser", fw["name"]),
                        base_image=lang_cfg["base_image"],
                        system_deps=lang_cfg.get("system_deps", []),
                        marker_files=fw.get("marker_files", []),
                    )
    return None


def is_python_repo(repo_dir: Path) -> bool:
    markers = ["pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"]
    return any((repo_dir / m).exists() for m in markers)
