from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal.version import SKILL_NAME, SKILL_VERSION


REPOSITORY_URL = "https://github.com/lavine888/skill-shortterm-mean-reversal"


def validate_skill_document(skill_text: str) -> None:
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*\n", skill_text, flags=re.DOTALL)
    if not frontmatter:
        raise ValueError("SKILL.md has no valid YAML frontmatter")
    header = frontmatter.group(1)
    required_patterns = {
        "name": rf"(?m)^name:\s*{re.escape(SKILL_NAME)}\s*$",
        "description": r"(?m)^description:\s*.+$",
        "repository": r"(?m)^\s+repository:\s*lavine888/skill-shortterm-mean-reversal\s*$",
        "repository_url": rf"(?m)^\s+repository_url:\s*{re.escape(REPOSITORY_URL)}\s*$",
        "collection": r"(?m)^\s+collection:\s*liangshuyuan-q58\s*$",
        "license": r"(?m)^\s+license:\s*GPL-3.0-only\s*$",
        "validation_level": r"(?m)^\s+validation_level:\s*runnable\s*$",
    }
    for field, pattern in required_patterns.items():
        if not re.search(pattern, header):
            raise ValueError(f"SKILL.md frontmatter has invalid {field}")
    blocks = re.findall(r"```json qsh-form\s*\n(.*?)\n```", skill_text, flags=re.DOTALL)
    if len(blocks) != 1:
        raise ValueError("SKILL.md must contain exactly one json qsh-form block")
    form = json.loads(blocks[0])
    if form.get("version") != 1 or form.get("task", {}).get("required") is not True:
        raise ValueError("qsh-form task contract is invalid")
    fields = form.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("qsh-form fields must be a non-empty list")
    keys = [field.get("key") for field in fields]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("qsh-form field keys must be unique and non-empty")
    template = form.get("prompt_template", "")
    if "{{task}}" not in template or any(f"{{{{{key}}}}}" not in template for key in keys):
        raise ValueError("qsh-form prompt_template does not reference every input")


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
    if metadata.get("license") != "GPL-3.0-only" or project.get("license", {}).get("text") != "GPL-3.0-only":
        raise ValueError("license metadata must use GPL-3.0-only")
    validate_skill_document(skill_text)
    if not (ROOT / "README.en.md").exists():
        raise ValueError("README.en.md is required")
    print(f"metadata valid: {SKILL_NAME} {SKILL_VERSION}")


if __name__ == "__main__":
    main()
