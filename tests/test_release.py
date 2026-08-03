from __future__ import annotations

from zipfile import ZipFile
import hashlib

from scripts.package_release import build_release, validate_release


def test_release_contains_both_readmes_and_excludes_generated_data(tmp_path):
    archive = build_release(tmp_path)
    validate_release(archive)
    with ZipFile(archive) as release:
        names = set(release.namelist())
    assert {"README.md", "README.en.md", "SKILL.md", "MANIFEST.sha256"}.issubset(names)
    assert not any(name.startswith("output/") or "__pycache__" in name for name in names)


def test_release_archive_is_deterministic(tmp_path):
    first = build_release(tmp_path / "first")
    second = build_release(tmp_path / "second")
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
