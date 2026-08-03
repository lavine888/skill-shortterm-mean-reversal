from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal.version import SKILL_NAME, SKILL_VERSION


def main() -> None:
    metadata = json.loads((ROOT / "skill.json").read_text(encoding="utf-8"))
    if metadata.get("name") != SKILL_NAME:
        raise ValueError("skill.json name does not match the runtime skill name")
    if metadata.get("version") != SKILL_VERSION:
        raise ValueError("skill.json version does not match the runtime version")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("version") != SKILL_VERSION:
        raise ValueError("pyproject.toml version does not match the runtime version")
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if f"name: {SKILL_NAME}" not in skill_text:
        raise ValueError("SKILL.md name does not match the runtime skill name")
    print(f"metadata valid: {SKILL_NAME} {SKILL_VERSION}")


if __name__ == "__main__":
    main()
