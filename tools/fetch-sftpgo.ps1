<#
.SYNOPSIS
  Downloads the official SFTPGo release that NextPush was tested with, verifies its SHA-256, and unpacks it into .\bin (next to nextpush.exe / the project).

.DESCRIPTION
  SFTPGo is a separate program (AGPL-3.0, https://github.com/drakkan/sftpgo). NextPush never modifies it. The checksum below is the one GitHub publishes for the release asset;
  the script refuses to install anything that does not match. Use -FromFile to verify and unpack an archive you downloaded yourself.

  powershell -ExecutionPolicy Bypass -File tools\fetch-sftpgo.ps1
#>
param(
  [string]$Version = "2.7.6",
  [string]$Destination = (Join-Path (Split-Path -Parent $PSScriptRoot) "bin"),
  [string]$FromFile = ""
)
$ErrorActionPreference = "Stop"
$known = @{ "2.7.6" = "af3994bb690962c7575858aee368a4c1d6fd4748b35770bb0773eb06408d504d" }   # sftpgo_v<version>_windows_portable.zip
if (-not $known.ContainsKey($Version)) { throw "No pinned checksum for SFTPGo $Version. Add it to this script after checking it on the GitHub release page." }
$asset = "sftpgo_v${Version}_windows_portable.zip"
$url = "https://github.com/drakkan/sftpgo/releases/download/v$Version/$asset"
$work = Join-Path ([IO.Path]::GetTempPath()) ("nextpush-sftpgo-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
  $zip = Join-Path $work $asset
  if ($FromFile) { Copy-Item -LiteralPath $FromFile -Destination $zip } else { Write-Host "Downloading $url"; Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing }
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
  if ($actual -ne $known[$Version]) { throw "SHA-256 mismatch for $asset (got $actual). Nothing was installed." }
  Write-Host "SHA-256 verified."
  $extract = Join-Path $work "x"
  Expand-Archive -LiteralPath $zip -DestinationPath $extract
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  Copy-Item -LiteralPath (Join-Path $extract "sftpgo.exe") -Destination $Destination -Force
  foreach ($folder in @("templates")) { Copy-Item -LiteralPath (Join-Path $extract $folder) -Destination $Destination -Recurse -Force }
  foreach ($file in @("LICENSE.txt", "NOTICE.txt")) { Copy-Item -LiteralPath (Join-Path $extract $file) -Destination (Join-Path $Destination "SFTPGO-$file") -Force }   # AGPL: the license travels with the program
  Write-Host "SFTPGo $Version installed in $Destination"
} finally {
  Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
}
