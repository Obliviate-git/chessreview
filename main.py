from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="棋迹：个人国际象棋复盘")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--self-check", type=Path)
    args = parser.parse_args()
    if args.self_check:
        import json
        import keyring
        from chessreview.coach import CodexClient
        from chessreview.engine import start_engine, analyse_position
        import chess
        report = {}
        try:
            with start_engine(threads=1) as engine:
                position = analyse_position(engine, chess.Board(), 0.15)
                report["engine"] = engine.id.get("name")
                report["engine_depth"] = position.depth
            report["credential_backend"] = type(keyring.get_keyring()).__name__
            keyring.get_password("ChessReview", "self-check-nonexistent")
            client = CodexClient()
            try:
                status = client.status()
                report["codex_logged_in"] = status.get("logged_in")
                models = client.rpc("model/list", {})
                report["models"] = [model["model"] for model in models.get("data", [])]
            finally:
                client.close()
            report["ok"] = True
        except Exception as error:
            report["ok"] = False
            report["error"] = str(error)
        args.self_check.parent.mkdir(parents=True, exist_ok=True)
        args.self_check.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0 if report["ok"] else 1)
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from chessreview.core import Document
    from chessreview.settings import data_dir
    from chessreview.ui import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ChessReview")
    app.setOrganizationName("Personal")
    if os.name == "nt":
        # The offscreen plugin does not discover Windows fonts by itself.
        for filename in ("msyh.ttc", "segoeui.ttf"):
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(no_ai=args.no_ai or bool(args.screenshot))
    path = args.review or (data_dir() / "last.chessreview")
    if path.is_file():
        try:
            window.set_document(Document.load(path), upgrade=not bool(args.screenshot))
        except (ValueError, KeyError, TypeError, OSError):
            pass
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
            window.close()
        QTimer.singleShot(800, capture)
    sys.exit(app.exec())


def report_fatal(error_type, error, tb):
    report_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChessReview"
    if "--screenshot" in sys.argv:
        index = sys.argv.index("--screenshot")
        if index + 1 < len(sys.argv):
            report_dir = Path(sys.argv[index + 1]).parent
    report_dir.mkdir(parents=True, exist_ok=True)
    message = "".join(traceback.format_exception(error_type, error, tb))
    (report_dir / "startup-error.log").write_text(message, encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.write(message)
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()
    except Exception:
        pass


if __name__ == "__main__":
    sys.excepthook = report_fatal
    try:
        main()
    except Exception:
        report_fatal(*sys.exc_info())
        sys.exit(1)
