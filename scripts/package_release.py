from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal.version import SKILL_NAME, SKILL_VERSION


RELEASE_PATHS = (
    ".github", "agents", "lavine_reversal", "references", "scripts", "tests",
    ".gitignore", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "PROGRESS.md", "ROADMAP.md",
    "README.md", "README.en.md", "SKILL.md", "VALIDATION.md", "pyproject.toml",
    "requirements-dev.txt", "requirements.txt", "skill.json",
)
FORBIDDEN_PARTS = {".git", ".pytest_cache", "__pycache__", "output", "dist", "build"}


def release_files() -> list[Path]:
    files: list[Path] = []
    for relative in RELEASE_PATHS:
        path = ROOT / relative
        if not path.exists():
            raise FileNotFoundError(f"release path is missing: {relative}")
        candidates = path.rglob("*") if path.is_dir() else [path]
        files.extend(
            candidate for candidate in candidates
            if candidate.is_file() and not FORBIDDEN_PARTS.intersection(candidate.relative_to(ROOT).parts)
            and candidate.suffix not in {".pyc", ".pyo"}
        )
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def _write_bytes(archive: ZipFile, name: str, content: bytes) -> None:
    info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def build_release(destination: str | Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"{SKILL_NAME}-{SKILL_VERSION}.zip"
    manifest: list[str] = []
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in release_files():
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            _write_bytes(archive, relative, content)
            manifest.append(f"{hashlib.sha256(content).hexdigest()}  {relative}")
        _write_bytes(archive, "MANIFEST.sha256", ("\n".join(manifest) + "\n").encode("utf-8"))
    validate_release(output)
    return output


def validate_release(path: str | Path) -> None:
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {"README.md", "README.en.md", "SKILL.md", "skill.json", "MANIFEST.sha256"}
        missing = sorted(required - names)
        if missing:
            raise ValueError("release archive missing: " + ", ".join(missing))
        for name in names:
            parts = PurePosixPath(name).parts
            if FORBIDDEN_PARTS.intersection(parts) or name.endswith((".pyc", ".pyo", ".env")):
                raise ValueError(f"forbidden release entry: {name}")
        manifest_lines = archive.read("MANIFEST.sha256").decode("utf-8").splitlines()
        manifest = dict(line.split("  ", 1)[::-1] for line in manifest_lines)
        for name in names - {"MANIFEST.sha256"}:
            expected = manifest.get(name)
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if expected != actual:
                raise ValueError(f"release manifest mismatch: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic Q58 release archive")
    parser.add_argument("--destination", default="dist")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--validate")
    args = parser.parse_args()
    if args.validate:
        validate_release(args.validate)
        print(f"valid release: {args.validate}")
        return
    if not args.skip_checks:
        subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "scripts/validate_metadata.py"], cwd=ROOT, check=True)
    print(build_release(args.destination))


if __name__ == "__main__":
    main()
