# 构建 Windows 可交付目录（PyInstaller onedir）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 检查依赖 =="
python -m pip install --upgrade pyinstaller

$icon = "D:\Study-AI.ico"
$iconArgs = @()
if (Test-Path $icon) { $iconArgs = @("--icon", $icon) }

Write-Host "== 清理旧构建 =="
if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
if (Test-Path "dist\Study-AI") { Remove-Item "dist\Study-AI" -Recurse -Force }

Write-Host "== PyInstaller 打包 =="
python -m PyInstaller `
  --noconfirm --clean --windowed --onedir `
  --name "Study-AI" `
  @iconArgs `
  --collect-all fastembed `
  --collect-all onnxruntime `
  --collect-submodules markdown `
  --collect-submodules pygments `
  app.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "== 复制本地模型和浏览器扩展 =="
if (Test-Path "models") {
  Copy-Item "models" "dist\Study-AI\models" -Recurse -Force
}
if (Test-Path "extension") {
  Copy-Item "extension" "dist\Study-AI\extension" -Recurse -Force
}
New-Item -ItemType Directory -Force -Path "dist\Study-AI\data" | Out-Null

Write-Host ""
Write-Host "构建完成：dist\Study-AI\Study-AI.exe"
Write-Host "首次启动会在 exe 同目录生成 config.json，请填写 API Key。"
Write-Host "如需安装包，可用 Inno Setup 打开 scripts\Study-AI.iss。"
