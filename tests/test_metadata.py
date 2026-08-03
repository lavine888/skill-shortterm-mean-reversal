from __future__ import annotations

import json
from pathlib import Path

from lavine_reversal.version import SKILL_NAME, SKILL_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_metadata_matches_runtime_version():
    metadata = json.loads((ROOT / "skill.json").read_text(encoding="utf-8"))
    assert metadata["name"] == SKILL_NAME
    assert metadata["version"] == SKILL_VERSION
