$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.\dist\棋迹-导入即看\棋迹.exe') {
    Start-Process -FilePath '.\dist\棋迹-导入即看\棋迹.exe'
} elseif (Test-Path -LiteralPath '.\dist\棋迹\棋迹.exe') {
    Start-Process -FilePath '.\dist\棋迹\棋迹.exe'
} elseif (Test-Path -LiteralPath '.\.venv\Scripts\pythonw.exe') {
    Start-Process -FilePath '.\.venv\Scripts\pythonw.exe' -ArgumentList 'main.py' -WindowStyle Hidden
} else {
    Write-Host '请先按照使用说明安装依赖，或使用打包后的棋迹.exe。'
}
