# 棋迹

棋迹是一个Windows国际象棋复盘工具。导入PGN后，可以立即在棋盘上查看实战走法。Stockfish在后台分析双方走法，补上评价、优势曲线和推荐变化。你可以逐步查看推荐走法的意图、在同一局面对比另一种走法，或者试走并让引擎自动应对。中文AI讲解和追问可选。

## 从源码运行

需要64位Windows和Python 3.12。在仓库根目录打开PowerShell，运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --no-ai
```

无需账号就能导入并查看棋谱。需要分析时，从[Stockfish官网下载](https://stockfishchess.org/download/)适合本机的Windows版本，解压后在棋迹设置中选择引擎程序。也可以把解压目录放在仓库根目录的`engine/`下，应用会查找`stockfish*.exe`。导入窗口自带一个已去掉身份信息的示例。

需要AI讲解时，运行`main.py`时去掉`--no-ai`，然后在设置中选用已安装并登录的Codex CLI，或配置兼容接口。GitHub账号不用于棋局分析。详细操作见[使用说明](使用说明.md)，依赖来源和许可见[第三方说明](第三方说明.md)。

## 测试和打包

`chessreview/`存放应用源码，`tests/`存放自动测试，`examples/`存放示例。GitHub Actions会在推送或提交拉取请求后运行Windows测试。测试不需要安装Stockfish，也不会调用真实AI服务。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
```

本地打包时，在`engine/`下准备Stockfish及随包提供的源码和许可说明，再运行`打包.ps1`。如果在`tools/`下放入`codex.exe`，打包脚本也会将其附带。输出位于`dist/棋迹/`，请保留整个目录。Git仓库不包含打包产物、引擎、Codex二进制或个人复盘文件。
