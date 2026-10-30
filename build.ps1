$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name BuckshotAssistant --add-data 'ocr_worker.ps1;.' --add-data 'game_bridge.gd;.' --add-data 'third_party/buckshot-roulette-solver/best.onnx;third_party/buckshot-roulette-solver' --add-data 'third_party/buckshot-roulette-solver/ai_engine.py;third_party/buckshot-roulette-solver' --add-data 'third_party/buckshot-roulette-solver/LICENSE;third_party/buckshot-roulette-solver' app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Copy-Item -LiteralPath 'README.md' -Destination 'dist\BuckshotAssistant\README.md' -Force
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination 'dist\BuckshotAssistant\THIRD_PARTY_NOTICES.md' -Force
Copy-Item -LiteralPath '启动游戏与助手.cmd','启动助手.cmd' -Destination 'dist\BuckshotAssistant' -Force
