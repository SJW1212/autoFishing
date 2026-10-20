$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '가상환경 생성 실패' }
}
& $pythonPath -m pip install -r requirements.txt pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw '라이브러리 설치 실패' }
& $pythonPath -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '회귀 테스트 실패' }
& $pythonPath -m PyInstaller --noconfirm --clean --onedir --windowed --name MinecraftSubtitleFisher --collect-submodules winrt --collect-binaries winrt --exclude-module numpy --distpath release app.py
if ($LASTEXITCODE -ne 0) { throw '실행 파일 생성 실패' }
$releasePath = Join-Path $PSScriptRoot 'release\MinecraftSubtitleFisher'
foreach ($documentName in @('README.md', '자막감지_방식조사.md', '검증결과.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $documentName) -Destination $releasePath
}
