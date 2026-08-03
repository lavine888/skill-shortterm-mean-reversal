param(
    [string]$Destination = "dist"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& py -3.11 (Join-Path $PSScriptRoot "package_release.py") --destination (Join-Path $Root $Destination)
if ($LASTEXITCODE -ne 0) { throw "release packaging failed" }
