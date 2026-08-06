from __future__ import annotations

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from lavine_reversal.version import SKILL_NAME, SKILL_VERSION
from scripts.validate_metadata import validate_skill_document


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_metadata_matches_runtime_version():
    metadata = json.loads((ROOT / "skill.json").read_text(encoding="utf-8"))
    assert metadata["name"] == SKILL_NAME
    assert metadata["version"] == SKILL_VERSION
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == SKILL_VERSION


def test_skill_frontmatter_and_qsh_form_are_valid():
    validate_skill_document((ROOT / "SKILL.md").read_text(encoding="utf-8"))
