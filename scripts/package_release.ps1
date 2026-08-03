param(
    [string]$Destination = "dist"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Version = & py -3.11 -c "import sys; sys.path.insert(0, r'$Root'); from lavine_reversal.version import SKILL_VERSION; print(SKILL_VERSION)"
$Name = "shortterm-mean-reversal-lavine-version-$Version"
$Dist = Join-Path $Root $Destination
$Stage = Join-Path $Dist $Name
$Zip = Join-Path $Dist "$Name.zip"

& py -3.11 -m pytest -q $Root
if ($LASTEXITCODE -ne 0) { throw "tests failed" }
& py -3.11 (Join-Path $PSScriptRoot "validate_metadata.py")
if ($LASTEXITCODE -ne 0) { throw "metadata validation failed" }

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
$Files = @(
    ".github", "agents", "lavine_reversal", "references", "scripts", "tests",
    ".gitignore", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "PROGRESS.md", "README.md", "VALIDATION.md",
    "SKILL.md", "pyproject.toml", "requirements-dev.txt", "requirements.txt", "skill.json"
)
foreach ($File in $Files) {
    Copy-Item -Recurse -Force -Path (Join-Path $Root $File) -Destination $Stage
}
Get-ChildItem -Recurse -Directory -Path $Stage -Include "__pycache__", ".pytest_cache" |
    Remove-Item -Recurse -Force
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path "$Stage\*" -DestinationPath $Zip
Write-Output $Zip
