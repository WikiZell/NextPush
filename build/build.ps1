<#
.SYNOPSIS
  Builds dist\nextpush (a PyInstaller one-folder program) with the verified SFTPGo next to it.

  powershell -ExecutionPolicy Bypass -File build\build.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path (Join-Path $root "bin\sftpgo.exe"))) { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "tools\fetch-sftpgo.ps1") }
py -3 -m pip install --quiet pyinstaller
py -3 -m PyInstaller --noconfirm --clean --name nextpush --console --distpath dist --workpath build\work --specpath build\work `
  --add-data "$root\nextpush\web;nextpush\web" "$root\nextpush_main.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item -Recurse -Force (Join-Path $root "bin") (Join-Path $root "dist\nextpush\bin")
Copy-Item -Force (Join-Path $root "LICENSE"), (Join-Path $root "THIRD-PARTY-NOTICES.md") (Join-Path $root "dist\nextpush")
Write-Host "Built: $root\dist\nextpush\nextpush.exe"
