param(
  [string]$PfxPath = "",
  [string]$PfxPassword = "",
  [string]$Thumbprint = "",
  [string]$TimestampServer = "http://timestamp.digicert.com",
  [switch]$CreateSelfSigned,
  [switch]$SkipTimestamp
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$targets = @(
  "dist\Study-AI\Study-AI.exe",
  "dist\Study-AI\Study-AI-Sandbox.exe",
  "dist\Study-AI-Setup.exe"
) | Where-Object { Test-Path $_ }

if ($targets.Count -eq 0) {
  throw "No files to sign. Run scripts/build_windows.ps1 first."
}

$cert = $null
if ($CreateSelfSigned) {
  Write-Host "Creating development self-signed code-signing certificate..."
  $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Study-AI Development" -CertStoreLocation "Cert:\CurrentUser\My" -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(2)
  $signingDir = Join-Path $root "data\signing"
  New-Item -ItemType Directory -Force -Path $signingDir | Out-Null
  $pwd = ConvertTo-SecureString -String ([guid]::NewGuid().ToString("N")) -AsPlainText -Force
  $pfx = Join-Path $signingDir "study-ai-dev.pfx"
  Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $pwd | Out-Null
  Export-Certificate -Cert $cert -FilePath (Join-Path $signingDir "study-ai-dev.cer") | Out-Null
  Write-Host "Development certificate: $pfx"
  Write-Host "Thumbprint: $($cert.Thumbprint)"
  Write-Host "This certificate is NOT trusted by Windows. Use a real OV/EV certificate for distribution."
} elseif ($PfxPath) {
  if (-not $PfxPassword) { throw "PfxPassword is required with PfxPath" }
  $secure = ConvertTo-SecureString -String $PfxPassword -AsPlainText -Force
  $cert = Import-PfxCertificate -FilePath $PfxPath -Password $secure -CertStoreLocation "Cert:\CurrentUser\My"
} elseif ($Thumbprint) {
  $cert = Get-ChildItem "Cert:\CurrentUser\My\$Thumbprint"
} else {
  throw "Provide -PfxPath, -Thumbprint or -CreateSelfSigned"
}

foreach ($target in $targets) {
  Write-Host "Signing: $target"
  $signArgs = @{
    FilePath = (Resolve-Path $target)
    Certificate = $cert
  }
  if (-not $SkipTimestamp) {
    $signArgs.TimestampServer = $TimestampServer
  }
  Set-AuthenticodeSignature @signArgs | Out-Null
}

Write-Host "Signature result:"
foreach ($target in $targets) {
  $sig = Get-AuthenticodeSignature (Resolve-Path $target)
  Write-Host ($target + " -> " + $sig.Status + " | " + $sig.StatusMessage)
}
