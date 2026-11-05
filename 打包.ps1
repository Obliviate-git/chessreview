$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$packageTaskPython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $packageTaskPython -PathType Leaf)) {
    throw '请先按照README安装开发依赖。'
}
if (-not (Get-ChildItem -LiteralPath '.\engine' -Filter 'stockfish*.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    throw '请先将Stockfish发行包解压到engine目录。'
}
$packageTaskData = @('--add-data', 'engine;engine', '--add-data', 'examples/sample.pgn;examples', '--add-data', 'licenses;licenses')
if (Test-Path -LiteralPath '.\tools\codex.exe' -PathType Leaf) {
    $packageTaskData += @('--add-data', 'tools/codex.exe;tools')
}
# 在独立暂存目录构建，再更新程序文件，保留用户放在发行目录的复盘。
$packageTaskStage = Join-Path $PSScriptRoot ('.dev\package-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $packageTaskStage -Force | Out-Null
# 避免其他工具放在PATH中的同名DLL污染打包结果，尤其是ICU。
$originalTaskPath = $env:PATH
$pythonTaskRoot = & $packageTaskPython -c 'import sys; print(sys.base_prefix)'
try {
    $env:PATH = "$PSScriptRoot\.venv\Scripts;$pythonTaskRoot;$pythonTaskRoot\DLLs;$env:WINDIR\System32;$env:WINDIR"
    & $packageTaskPython -m PyInstaller --clean --noconfirm --onedir --windowed --distpath $packageTaskStage --name '棋迹' @packageTaskData --hidden-import 'keyring.backends.Windows' 'main.py'
    if ($LASTEXITCODE -ne 0) { throw '打包失败，请查看上方错误。' }
} finally {
    $env:PATH = $originalTaskPath
}
New-Item -ItemType Directory -Path '.\dist\棋迹' -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $packageTaskStage '棋迹\棋迹.exe') -Destination '.\dist\棋迹\棋迹.exe' -Force
Copy-Item -LiteralPath (Join-Path $packageTaskStage '棋迹\_internal') -Destination '.\dist\棋迹' -Recurse -Force
Copy-Item -LiteralPath '.\.venv\Lib\site-packages\PySide6\vcruntime140.dll' -Destination '.\dist\棋迹\_internal\VCRUNTIME140.dll' -Force
Copy-Item -LiteralPath '.\.venv\Lib\site-packages\PySide6\vcruntime140_1.dll' -Destination '.\dist\棋迹\_internal\VCRUNTIME140_1.dll' -Force
Copy-Item -LiteralPath '.\使用说明.md' -Destination '.\dist\棋迹\使用说明.md' -Force
Copy-Item -LiteralPath '.\第三方说明.md' -Destination '.\dist\棋迹\第三方说明.md' -Force
Copy-Item -LiteralPath '.\README.md' -Destination '.\dist\棋迹\README.md' -Force
New-Item -ItemType Directory -Path '.\dist\棋迹\源码' -Force | Out-Null
Copy-Item -LiteralPath '.\chessreview' -Destination '.\dist\棋迹\源码' -Recurse -Force
Copy-Item -LiteralPath '.\main.py' -Destination '.\dist\棋迹\源码\main.py' -Force
Copy-Item -LiteralPath '.\requirements.txt' -Destination '.\dist\棋迹\源码\requirements.txt' -Force
New-Item -ItemType Directory -Path '.\dist\棋迹\源码\examples' -Force | Out-Null
Copy-Item -LiteralPath '.\examples\sample.pgn' -Destination '.\dist\棋迹\源码\examples\sample.pgn' -Force
Write-Host '打包完成：dist\棋迹\棋迹.exe'
