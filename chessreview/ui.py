from __future__ import annotations

import copy
import hashlib
import html
import json
import time
from pathlib import Path

import chess
from PySide6.QtCore import QRect, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from .board import ChessBoard, EvaluationGraph
from .coach import COACH_VERSION, CodexClient, api_explain
from .core import (GRADING_VERSION, Document, LABEL_COLORS, Position, cache_key, context_for, parse_pgn,
                   line_context, material_facts, material_snapshot, pv_san, score_text, summary_context)
from .engine import analyse_game, analyse_trial, compare_moves, complete_reviews, resource_root
from .settings import Settings, data_dir
from .workers import Worker
from .variations import explanation_html
from .teaching import Comparison, branch_key, comparison_key, guide_step, line_goal, teaching_context
from .visuals import CapturedPieces, IconButton, MOVE_META, MoveDelegate, action_icon, grade_icon


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 14px; color: #e5e3df; }
QMainWindow, QDialog { background: #242320; }
QFrame#card { background: #2b2a27; border: none; border-radius: 12px; }
QFrame#boardPanel { background: transparent; border: none; }
QFrame#coachCard { background: #f7f6f2; border: none; border-radius: 12px; }
QFrame#coachCard QLabel { color: #353831; }
QFrame#coachCard QLabel#muted { color: #797c73; font-size: 12px; }
QFrame#coachCard QLabel#best { color: #527d2d; font-weight: 600; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #ece9e1; }
QLabel#muted { color: #aaa7a1; font-size: 12px; }
QLabel#heading { font-size: 19px; font-weight: 600; }
QLabel#badge { padding: 4px 9px; background: #383c30; border-radius: 6px; }
QLabel#best { color: #b5d77b; font-weight: 600; }
QPushButton { background: #3b3a36; border: none; border-radius: 8px; padding: 9px 13px; font-weight: 600; }
QPushButton:hover { background: #494842; }
QPushButton:checked { background: #425536; color: #cee7b1; }
QPushButton:disabled { color: #77746f; background: #302f2b; }
QPushButton#primary { background: #81b64c; color: #fff; font-weight: 600; border-bottom: 3px solid #5e8c35; }
QPushButton#primary:hover { background: #93c85c; }
QPushButton#primary:disabled { background: #586449; color: #a5ad9b; border-bottom-color: #48513d; }
QPushButton#navigation { padding: 5px; border-bottom: 2px solid #23231f; background: #3a3935; }
QPushButton#navigation:hover { background: #4c4b44; }
QPushButton#navigation:disabled { background: #302f2c; }
QComboBox, QLineEdit { background: #2b2a27; border: 1px solid #45443d; border-radius: 7px; padding: 7px; }
QComboBox QAbstractItemView { background: #302e2b; selection-background-color: #535f3b; }
QPlainTextEdit, QTextBrowser { background: #282623; border: none; border-radius: 7px; padding: 9px; }
QTextBrowser#coachText { background: transparent; color: #45483e; font-size: 14px; padding: 0; }
QTableWidget { background: #2b2a27; border: none; selection-background-color: transparent; }
QHeaderView::section { background: #2b2a27; border: none; padding: 5px; color: #949187; font-size: 12px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #2b2a27; color: #aaa7a1; padding: 10px 23px; border-bottom: 2px solid transparent; font-weight: 600; }
QTabBar::tab:selected { border-bottom: 2px solid #81b64c; color: #fff; }
QProgressBar { background: #302e2b; border: none; border-radius: 4px; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: #81b64c; }
QScrollBar:vertical { background: transparent; width: 7px; }
QScrollBar::handle:vertical { background: #56554e; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: #242320; }
QToolTip { background: #e5e3df; color: #262421; border: none; padding: 6px; }
"""


def button(text: str, callback=None, primary=False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    if callback:
        widget.clicked.connect(callback)
    return widget


def label(text: str, name="", wrap=False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class ImportDialog(QDialog):
    def __init__(self, parent=None, initial_text=""):
        super().__init__(parent)
        self.setWindowTitle("导入棋谱")
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(label("PGN棋谱", "heading"))
        self.text = QPlainTextEdit(initial_text)
        self.text.setPlaceholderText('[Event "Online Game"]\n[White "白方"]\n[Black "黑方"]\n\n1. e4 e5 2. Nf3 Nc6 ...')
        layout.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.addWidget(button("读取PGN文件", self.read_file))
        row.addWidget(button("载入示例", self.example))
        row.addStretch()
        row.addWidget(label("查看视角"))
        self.color = QComboBox()
        self.color.addItem("白方", chess.WHITE)
        self.color.addItem("黑方", chess.BLACK)
        self.color.setCurrentIndex(1 if parent and getattr(parent, "doc", None) and not parent.doc.color else 0)
        row.addWidget(self.color)
        layout.addLayout(row)
        self.error = label("", "muted", True)
        layout.addWidget(self.error)
        actions = QDialogButtonBox(QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Cancel).setText("取消")
        actions.rejected.connect(self.reject)
        self.import_button = actions.addButton("开始整盘分析", QDialogButtonBox.AcceptRole)
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.validate)
        layout.addWidget(actions)
        self.document = None

    def read_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择棋谱", "", "棋谱 (*.pgn *.txt);;所有文件 (*)")
        if path:
            try:
                self.text.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError):
                self.error.setText("文件无法以UTF-8读取，请从原应用复制棋谱文本。")

    def example(self):
        self.text.setPlainText((resource_root() / "examples/sample.pgn").read_text(encoding="utf-8"))

    def validate(self):
        try:
            self.document = parse_pgn(self.text.toPlainText(), self.color.currentData())
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent):
        super().__init__(parent)
        self.settings = copy.deepcopy(settings)
        self.keys: dict[str, str] = {}
        self.setWindowTitle("讲解与引擎设置")
        self.resize(650, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(label("讲解服务", "heading"))
        layout.addWidget(label("默认使用订阅账号的Codex。备用API只在你手动切换后使用。", "muted", True))
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(["Codex订阅账号", "备用API"])
        self.provider.setCurrentIndex(0 if settings.provider == "codex" else 1)
        form.addRow("当前服务", self.provider)
        self.model = QComboBox()
        self.model.addItem("Luna · 日常讲解", "gpt-6-luna")
        self.model.addItem("Sol · 复杂问题（中等思考）", "gpt-6-sol")
        self.model.setCurrentIndex(0 if settings.model == "gpt-6-luna" else 1)
        form.addRow("Codex模型", self.model)
        self.codex_path = QLineEdit(settings.codex)
        self.codex_path.setPlaceholderText("留空使用本机已安装的Codex")
        form.addRow("Codex程序", self.codex_path)
        layout.addLayout(form)
        layout.addWidget(button("使用订阅账号登录Codex", parent.start_login))
        layout.addWidget(label("登录会打开浏览器。完成后可关闭设置并点击主界面的“刷新额度”。", "muted", True))
        api_form = QFormLayout()
        self.profile = QComboBox()
        self.profile.addItems(list(self.settings.api_profiles))
        self.profile.setCurrentText(settings.api_profile)
        self.current_profile = self.profile.currentText()
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("服务提供的OpenAI兼容接口根地址，通常以/v1结尾")
        self.api_model = QLineEdit()
        self.api_model.setPlaceholderText("服务提供的模型名称")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("留空保持已有密钥；新密钥存入Windows凭据管理器")
        api_form.addRow("备用服务配置", self.profile)
        api_form.addRow("接口根地址", self.endpoint)
        api_form.addRow("模型名称", self.api_model)
        api_form.addRow("API密钥", self.key)
        layout.addLayout(api_form)
        self.load_profile()
        self.profile.currentTextChanged.connect(self.change_profile)
        layout.addWidget(label("备用服务需支持OpenAI兼容的流式对话接口；地址与模型以你的服务商提供的信息为准。", "muted", True))
        engine_form = QFormLayout()
        engine_row = QHBoxLayout()
        self.engine = QLineEdit(settings.engine)
        self.engine.setPlaceholderText("留空使用随应用附带的Stockfish")
        engine_row.addWidget(self.engine, 1)
        engine_row.addWidget(button("选择", self.select_engine))
        engine_form.addRow("棋类引擎", engine_row)
        layout.addLayout(engine_form)
        self.error = label("", "muted", True)
        layout.addWidget(self.error)
        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Save).setText("保存设置")
        actions.button(QDialogButtonBox.Cancel).setText("取消")
        actions.accepted.connect(self.commit)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def store_profile(self):
        self.settings.api_profiles[self.current_profile] = {"base_url": self.endpoint.text().strip(), "model": self.api_model.text().strip()}
        if self.key.text().strip():
            self.keys[self.current_profile] = self.key.text().strip()

    def load_profile(self):
        info = self.settings.api_profiles.get(self.current_profile, {})
        self.endpoint.setText(info.get("base_url", ""))
        self.api_model.setText(info.get("model", ""))
        self.key.clear()

    def change_profile(self, name):
        self.store_profile()
        self.current_profile = name
        self.load_profile()

    def select_engine(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择Stockfish", "", "引擎程序 (*.exe)")
        if path:
            self.engine.setText(path)

    def commit(self):
        self.store_profile()
        self.settings.provider = "codex" if self.provider.currentIndex() == 0 else "api"
        self.settings.model = self.model.currentData()
        self.settings.api_profile = self.current_profile
        self.settings.codex = self.codex_path.text().strip()
        self.settings.engine = self.engine.text().strip()
        try:
            for profile, key in self.keys.items():
                self.settings.set_key(profile, key)
            self.settings.save()
        except Exception:
            self.error.setText("设置保存失败，请检查Windows凭据管理器和本地目录权限。")
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, no_ai=False):
        super().__init__()
        self.settings = Settings.load()
        self.client = CodexClient(self.settings.codex)
        self.doc: Document | None = None
        self.filepath: Path | None = None
        self.ply = 0
        self.mode = "real"
        self.variation: list[str] = []
        self.variation_seed = chess.Board()
        self.variation_index = 0
        self.variation_name = "推荐变化"
        self.variation_origin_key = ""
        self.variation_context = {}
        self.demo_lines = []
        self.demo_context = {}
        self.chat_demo_context = {}
        self.trial_board = chess.Board()
        self.trial_history: list[chess.Board] = []
        self.trial_position: Position | None = None
        self.trial_worker: Worker | None = None
        self.analysis_worker: Worker | None = None
        self.ai_worker: Worker | None = None
        self.ai_key = ""
        self.workers: set[Worker] = set()
        self.closing = False
        self.no_ai = no_ai
        self.table_cells: dict[int, tuple[int, int]] = {}
        self.variation_cells: dict[int, tuple[int, int]] = {}
        self.variation_seed_ply = 0
        self.comparison_worker = None
        self.comparison_pick = None
        self.active_comparison = None
        self.comparison_epoch = object()
        self.setWindowTitle("棋迹 · 实战复盘")
        self.setWindowIcon(grade_icon("最佳", 64))
        self.resize(1320, 920)
        self.setMinimumSize(1040, 760)
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 16, 24, 12)
        outer.setSpacing(14)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("棋迹", "brand"))
        header.addLayout(brand)
        header.addWidget(label("实战复盘", "muted"))
        header.addStretch()
        self.source = QComboBox()
        self.source.addItems(["Codex · Luna", "Codex · Sol（中等）", "备用API"])
        self.source.setCurrentIndex(0 if self.settings.provider == "codex" and self.settings.model == "gpt-6-luna" else (1 if self.settings.provider == "codex" else 2))
        self.source.currentIndexChanged.connect(self.change_source)
        header.addWidget(self.source)
        self.auto_ai = QPushButton("自动讲解")
        self.auto_ai.setCheckable(True)
        self.auto_ai.setChecked(not no_ai)
        self.auto_ai.toggled.connect(self.schedule_explanation)
        header.addWidget(self.auto_ai)
        header.addSpacing(10)
        self.import_btn = button("＋ 导入棋谱", self.import_game, True)
        self.open_btn = button("打开复盘", self.open_review)
        self.save_btn = button("保存", self.save_review)
        header.addWidget(self.import_btn)
        header.addWidget(self.open_btn)
        header.addWidget(self.save_btn)
        self.settings_btn = IconButton("settings", "设置", self.configure)
        self.settings_btn.setFixedWidth(42)
        header.addWidget(self.settings_btn)
        outer.addLayout(header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QFrame()
        left.setObjectName("boardPanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 5, 0, 0)
        left_layout.setSpacing(10)
        self.opponent_label = label("黑方", "muted")
        self.opponent_label.setStyleSheet("font-size:15px; font-weight:600; color:#c5c3bb; padding-left:4px;")
        self.opponent_captures = CapturedPieces()
        opponent_row = QHBoxLayout()
        opponent_row.setSpacing(10)
        opponent_row.addWidget(self.opponent_label)
        opponent_row.addWidget(self.opponent_captures)
        opponent_row.addStretch()
        left_layout.addLayout(opponent_row)
        self.board = ChessBoard()
        self.board.move_requested.connect(self.user_trial_move)
        left_layout.addWidget(self.board, 1)
        self.player_label = label("白方", "muted")
        self.player_label.setStyleSheet("font-size:15px; font-weight:600; color:#c5c3bb; padding-left:4px;")
        self.player_captures = CapturedPieces()
        player_row = QHBoxLayout()
        player_row.setSpacing(10)
        player_row.addWidget(self.player_label)
        player_row.addWidget(self.player_captures)
        player_row.addStretch()
        left_layout.addLayout(player_row)
        row = QHBoxLayout()
        self.mode_label = label("实战", "badge")
        row.addWidget(self.mode_label)
        self.view_side = QComboBox()
        self.view_side.addItem("白方视角", chess.WHITE)
        self.view_side.addItem("黑方视角", chess.BLACK)
        self.view_side.currentIndexChanged.connect(self.change_perspective)
        row.addWidget(self.view_side)
        row.addStretch()
        self.return_btn = button("返回实战", self.return_real)
        self.trial_btn = button("试走", self.start_trial)
        self.undo_btn = button("撤回", self.undo_trial)
        row.addWidget(self.trial_btn)
        row.addWidget(self.undo_btn)
        row.addWidget(self.return_btn)
        self.flip_btn = IconButton("flip", "翻转棋盘", self.flip)
        self.flip_btn.setFixedWidth(42)
        row.addWidget(self.flip_btn)
        left_layout.addLayout(row)
        self.splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("card")
        right.setMinimumWidth(408)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(6)
        self.game_title = label("棋谱", "heading", True)
        self.game_title.setStyleSheet("font-size:15px;color:#ebe9e1;font-weight:600;")
        right_layout.addWidget(self.game_title)
        self.game_detail = label("", "muted", True)
        self.game_detail.hide()
        self.coach_card = QFrame()
        self.coach_card.setObjectName("coachCard")
        coach_layout = QVBoxLayout(self.coach_card)
        coach_layout.setContentsMargins(14, 12, 14, 12)
        coach_layout.setSpacing(6)
        self.coach_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        move_row = QHBoxLayout()
        self.grade_mark = QLabel()
        self.grade_mark.setFixedSize(28, 28)
        move_row.addWidget(self.grade_mark)
        self.move_heading = label("起始局面", "heading")
        self.badge = label("")
        self.badge.setStyleSheet("font-size:13px; font-weight:600;")
        move_row.addWidget(self.move_heading, 1)
        move_row.addWidget(self.badge)
        coach_layout.addLayout(move_row)
        self.score_label = label("评分：—", "muted", True)
        coach_layout.addWidget(self.score_label)
        self.best_label = label("", "best", True)
        coach_layout.addWidget(self.best_label)
        self.best_label.hide()
        self.hint_label = label("", wrap=True)
        self.hint_label.setStyleSheet("color:#5b6055;font-size:14px;")
        coach_layout.addWidget(self.hint_label)
        self.hint_label.hide()
        self.explanation = QTextBrowser()
        self.explanation.setObjectName("coachText")
        self.explanation.setOpenExternalLinks(False)
        self.explanation.setOpenLinks(False)
        self.explanation.anchorClicked.connect(self.coach_link)
        self.explanation.setMinimumHeight(42)
        self.explanation.setMaximumHeight(88)
        self.explanation.setPlainText("")
        self.explanation.hide()
        coach_layout.addWidget(self.explanation)
        self.demo_bar = QLabel()
        self.demo_bar.setTextFormat(Qt.RichText)
        self.demo_bar.setWordWrap(True)
        self.demo_bar.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.demo_bar.setOpenExternalLinks(False)
        self.demo_bar.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.demo_bar.linkActivated.connect(lambda url: self.coach_link(QUrl(url)))
        self.demo_bar.hide()
        coach_layout.addWidget(self.demo_bar)
        right_layout.addWidget(self.coach_card)
        actions = QHBoxLayout()
        self.explain_btn = button("解析", self.explain_step)
        self.explain_btn.setIcon(action_icon("bulb"))
        self.recommend_btn = button("推荐变化", self.start_variation, True)
        self.recommend_btn.setIcon(action_icon("play", "#ffffff"))
        actions.addWidget(self.explain_btn, 1)
        actions.addWidget(self.recommend_btn, 1)
        right_layout.addLayout(actions)
        self.guide_controls = QWidget()
        guide_controls_layout = QHBoxLayout(self.guide_controls)
        guide_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.intent_btn = button("意图", lambda: self.refresh())
        self.intent_btn.setCheckable(True)
        self.intent_btn.setChecked(True)
        self.intent_btn.setToolTip("绿：本步作用；橙：引擎后续。攻击箭头不表示一定能赢子。")
        self.guide_explain_btn = button("讲解", self.explain_step)
        self.why_btn = button("为什么", self.ask_why)
        self.compare_btn = button("换个走法", self.begin_comparison)
        for control in (self.guide_explain_btn, self.intent_btn, self.why_btn, self.compare_btn):
            guide_controls_layout.addWidget(control, 1)
        right_layout.addWidget(self.guide_controls)
        self.comparison_choices = QWidget()
        comparison_choices_layout = QHBoxLayout(self.comparison_choices)
        comparison_choices_layout.setContentsMargins(0, 0, 0, 0)
        self.reference_btn = button("推荐", lambda: self.switch_comparison(False))
        self.alternative_btn = button("对比", lambda: self.switch_comparison(True))
        self.reference_btn.setCheckable(True)
        self.alternative_btn.setCheckable(True)
        comparison_choices_layout.addWidget(self.reference_btn, 1)
        comparison_choices_layout.addWidget(self.alternative_btn, 1)
        right_layout.addWidget(self.comparison_choices)

        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(230)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "白方", "黑方"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 42)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setItemDelegate(MoveDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setMinimumHeight(0)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setShowGrid(False)
        self.table.cellClicked.connect(self.table_seek)
        self.tabs.addTab(self.table, "棋谱")
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(False)
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self.coach_link)
        chat_page = QWidget()
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 5, 0, 0)
        chat_layout.addWidget(self.chat, 1)
        self.tabs.addTab(chat_page, "追问")
        summary_page = QWidget()
        summary_layout = QVBoxLayout(summary_page)
        self.summary = QTextBrowser()
        self.summary.setPlainText("")
        self.summary_btn = button("生成总结", self.generate_summary, True)
        summary_layout.addWidget(self.summary)
        summary_layout.addWidget(self.summary_btn)
        self.tabs.addTab(summary_page, "总结")
        right_layout.addWidget(self.tabs, 1)
        ask_row = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText("问教练…")
        self.question.returnPressed.connect(self.ask)
        ask_row.addWidget(self.question, 1)
        self.ask_btn = button("追问", self.ask)
        self.cancel_ai_btn = button("停止", self.cancel_ai)
        ask_row.addWidget(self.ask_btn)
        ask_row.addWidget(self.cancel_ai_btn)
        chat_layout.addLayout(ask_row)
        graph_title = QHBoxLayout()
        self.graph_title = label("优势曲线 · 白方", "muted")
        graph_title.addWidget(self.graph_title)
        graph_title.addStretch()
        self.usage_label = label("本盘AI请求：0次", "muted")
        graph_title.addWidget(self.usage_label)
        right_layout.addLayout(graph_title)
        self.graph = EvaluationGraph()
        self.graph.seek.connect(self.go)
        right_layout.addWidget(self.graph)
        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self.first_btn = IconButton("start", "回到起点", lambda: self.jump_boundary(False))
        self.prev_btn = IconButton("prev", "上一步（←）", lambda: self.step(-1))
        self.play_btn = IconButton("play", "播放变化", self.toggle_play)
        self.next_btn = IconButton("next", "下一步（→）", lambda: self.step(1))
        self.last_btn = IconButton("end", "跳到最后", lambda: self.jump_boundary(True))
        for control in (self.first_btn, self.prev_btn, self.play_btn, self.next_btn, self.last_btn):
            navigation.addWidget(control, 1)
        right_layout.addLayout(navigation)
        self.splitter.addWidget(right)
        self.splitter.setSizes([790, 470])
        outer.addWidget(self.splitter, 1)

        progress_row = QHBoxLayout()
        self.progress_label = label("准备开始", "muted")
        self.progress = QProgressBar()
        self.progress.hide()
        self.cancel_analysis_btn = button("取消分析", self.cancel_analysis)
        self.cancel_analysis_btn.hide()
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_analysis_btn)
        progress_row.addStretch()
        self.account_label = label("Codex额度：尚未读取", "muted")
        progress_row.addWidget(self.account_label)
        self.account_btn = IconButton("refresh", "刷新额度", self.refresh_account)
        self.account_btn.setMinimumHeight(28)
        self.account_btn.setFixedWidth(32)
        progress_row.addWidget(self.account_btn)
        outer.addLayout(progress_row)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(1000)
        self.play_timer.timeout.connect(self.play_tick)
        self.ai_timer = QTimer(self)
        self.ai_timer.setSingleShot(True)
        self.ai_timer.setInterval(600)
        self.ai_timer.timeout.connect(self.auto_explain)
        self.shortcuts = []
        for key, fn in (("Left", lambda: self.navigate_shortcut("left")), ("Right", lambda: self.navigate_shortcut("right")),
                        ("Home", lambda: self.navigate_shortcut("home")), ("End", lambda: self.navigate_shortcut("end")),
                        ("Ctrl+S", self.save_review), ("Ctrl+O", self.open_review)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(fn)
            self.shortcuts.append(shortcut)
        self.refresh()
        if not no_ai:
            QTimer.singleShot(200, self.refresh_account)

    def launch(self, fn, result=None, error=None) -> Worker:
        worker = Worker(fn, self)
        self.workers.add(worker)
        if result:
            worker.result.connect(result)
        worker.error.connect(error or self.show_error)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        return worker

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "table"):
            QTimer.singleShot(0, self.keep_move_visible)
        if hasattr(self, "explanation"):
            QTimer.singleShot(0, self.fit_coach_text)

    def fit_coach_text(self):
        if self.explanation.isVisible():
            height = int(self.explanation.document().documentLayout().documentSize().height()) + 8
            self.explanation.setFixedHeight(max(42, min(88, height)))
        if self.mode == "variation" and self.hint_label.isVisible():
            width = max(160, self.coach_card.width() - 28)
            bounds = self.hint_label.fontMetrics().boundingRect(QRect(0, 0, width, 1000), Qt.TextWordWrap, self.hint_label.text())
            self.hint_label.setFixedHeight(max(24, bounds.height() + 4))

    def keep_move_visible(self):
        if self.table.currentItem():
            self.table.scrollToItem(self.table.currentItem())

    def show_error(self, message):
        if not self.closing:
            self.progress_label.setText(message)

    def import_game(self):
        if self.analysis_worker:
            return
        dialog = ImportDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.cancel_ai()
            self.filepath = None
            self.set_document(dialog.document)
            self.start_analysis()

    def set_document(self, doc: Document, upgrade=False):
        self.reset_comparison()
        self.doc = doc
        self.ply = 0
        self.mode = "real"
        self.variation_origin_key = ""
        self.variation_context = {}
        self.demo_lines = []
        self.board.orientation = doc.color
        self.view_side.blockSignals(True)
        self.view_side.setCurrentIndex(0 if doc.color else 1)
        self.view_side.blockSignals(False)
        self.build_table()
        self.graph.scores = [p.cp for p in doc.positions]
        self.graph.annotations = {ply: review.label for ply, review in doc.reviews.items()}
        self.graph.color = doc.color
        self.game_title.setText(doc.title)
        side = "白方" if doc.color else "黑方"
        self.game_detail.setText(f"{doc.headers.get('Date', '')} · {side} · {doc.headers.get('Result', '*')}")
        self.game_title.setToolTip(self.game_detail.text())
        if doc.positions:
            self.progress_label.setText(f"分析完成 · {doc.elapsed:.1f}秒")
        self.refresh()
        if upgrade and doc.positions and (len(doc.reviews) < len(doc.moves) or doc.grading_version < GRADING_VERSION):
            self.start_analysis()

    def change_perspective(self, index):
        if not self.doc:
            self.board.orientation = self.view_side.currentData()
            self.board.update()
            return
        if self.trial_worker or self.analysis_worker:
            self.view_side.blockSignals(True)
            self.view_side.setCurrentIndex(0 if self.doc.color else 1)
            self.view_side.blockSignals(False)
            return
        self.cancel_ai()
        self.reset_comparison()
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        self.mode = "real"
        self.doc.color = self.view_side.currentData()
        self.board.orientation = self.doc.color
        self.graph.color = self.doc.color
        self.build_table()
        side = "白方" if self.doc.color else "黑方"
        self.game_detail.setText(f"{self.doc.headers.get('Date', '')} · {side} · {self.doc.headers.get('Result', '*')}")
        self.game_title.setToolTip(self.game_detail.text())
        self.refresh()
        self.autosave()

    def build_table(self):
        self.table.clearSpans()
        self.table.setRowCount(0)
        self.table_cells.clear()
        self.variation_cells.clear()
        if not self.doc:
            return
        rows: dict[int, int] = {}
        board = chess.Board(self.doc.initial_fen)
        for ply, san in enumerate(self.doc.sans, 1):
            number = board.fullmove_number
            if number not in rows:
                row = self.table.rowCount()
                rows[number] = row
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(str(number)))
            row = rows[number]
            col = 1 if board.turn else 2
            review = self.doc.reviews.get(ply)
            annotation = self.doc.annotations.get(ply, "")
            item = QTableWidgetItem(san)
            item.setData(Qt.UserRole, ply)
            item.setData(MOVE_META, {"kind": "real", "grade": review.label if review else "", "annotation": annotation})
            tooltip = ("最佳：" + pv_san(board, review.before.pv[:1]) +
                       f"\n评分：{score_text(review.before, self.doc.color)} → {score_text(review.played, self.doc.color)}") if review else san
            if annotation:
                tooltip += "\n棋谱原标注：" + annotation
            if review and review.evidence:
                tooltip += "\n" + review.evidence
            item.setToolTip(tooltip)
            item.setForeground(QColor(LABEL_COLORS.get(review.label, "#f2f0eb") if review else "#f2f0eb"))
            self.table.setItem(row, col, item)
            self.table_cells[ply] = (row, col)
            board.push_uci(self.doc.moves[ply - 1])
        if self.mode == "variation" and self.variation:
            anchor = self.table_cells.get(self.ply, (-1, 0))[0]
            insert = anchor + 1
            self.table.insertRow(insert)
            header = QTableWidgetItem(self.variation_name + "　·　点击走法查看")
            header.setData(MOVE_META, {"kind": "branch_header", "step": 0})
            self.table.setItem(insert, 0, header)
            self.table.setSpan(insert, 0, 1, 3)
            self.variation_cells[0] = (insert, 0)
            added = 1
            branch_board = self.variation_seed.copy()
            branch_rows = {}
            for step, uci in enumerate(self.variation, 1):
                number = branch_board.fullmove_number
                if number not in branch_rows:
                    row = insert + added
                    self.table.insertRow(row)
                    added += 1
                    branch_rows[number] = row
                    number_item = QTableWidgetItem(str(number) + ".")
                    number_item.setData(MOVE_META, {"kind": "branch_number"})
                    self.table.setItem(row, 0, number_item)
                    # Empty cells still belong to the shaded variation block.
                    for column in (1, 2):
                        empty = QTableWidgetItem("")
                        empty.setData(MOVE_META, {"kind": "variation"})
                        self.table.setItem(row, column, empty)
                row = branch_rows[number]
                column = 1 if branch_board.turn else 2
                san = branch_board.san(chess.Move.from_uci(uci))
                item = QTableWidgetItem(san)
                item.setData(MOVE_META, {"kind": "variation", "step": step})
                item.setToolTip(f"{number}{'.' if branch_board.turn else '...'}{san} · {self.variation_name}")
                self.table.setItem(row, column, item)
                self.variation_cells[step] = (row, column)
                branch_board.push_uci(uci)
            self.table_cells = {ply: (row + added if row >= insert else row, col)
                                for ply, (row, col) in self.table_cells.items()}

    def table_seek(self, row, column):
        item = self.table.item(row, column)
        if not item:
            return
        meta = item.data(MOVE_META) or {}
        if meta.get("kind") in ("variation", "branch_header") and "step" in meta:
            if self.comparison_pick:
                return
            self.play_timer.stop()
            self.play_btn.setText("播放变化")
            self.variation_index = meta["step"]
            self.refresh()
        elif item.data(Qt.UserRole) is not None:
            self.go(item.data(Qt.UserRole))

    def start_analysis(self):
        if not self.doc or self.analysis_worker:
            return
        doc = self.doc
        self.progress.show()
        self.cancel_analysis_btn.show()
        self.progress.setValue(0)
        upgrading = bool(doc.positions)
        self.progress_label.setText("正在更新走法评价…" if upgrading else "正在本机分析双方走法…")
        analysis = complete_reviews if upgrading else analyse_game
        worker = Worker(lambda w: analysis(doc, self.settings.engine, w.progress.emit, w.cancelled), self)
        self.analysis_worker = worker
        self.workers.add(worker)
        worker.progress.connect(self.analysis_progress)
        worker.result.connect(self.analysis_done)
        worker.error.connect(self.show_error)
        worker.finished.connect(self.analysis_finished)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        self.refresh()

    def analysis_progress(self, completed, total, elapsed):
        self.progress.setValue(round(completed / total * 100))
        self.progress_label.setText(f"整盘分析 · {completed}/{total} · 已用{elapsed:.0f}秒")

    def analysis_done(self, doc):
        self.build_table()
        self.graph.scores = [p.cp for p in doc.positions]
        self.graph.annotations = {ply: review.label for ply, review in doc.reviews.items()}
        self.progress_label.setText(f"分析完成 · {doc.elapsed:.1f}秒")
        self.autosave()

    def analysis_finished(self):
        self.analysis_worker = None
        self.progress.hide()
        self.cancel_analysis_btn.hide()
        self.refresh()

    def cancel_analysis(self):
        if self.analysis_worker:
            self.analysis_worker.cancelled.set()
            self.progress_label.setText("正在停止分析…")

    def open_review(self):
        if self.analysis_worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开复盘", str(data_dir()), "复盘文件 (*.chessreview);;棋谱 (*.pgn)")
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".pgn":
                dialog = ImportDialog(self, Path(path).read_text(encoding="utf-8-sig"))
                if dialog.exec() == QDialog.Accepted:
                    self.cancel_ai()
                    self.filepath = None
                    self.set_document(dialog.document)
                    self.start_analysis()
            else:
                doc = Document.load(Path(path))
                self.cancel_ai()
                self.filepath = Path(path)
                self.set_document(doc, upgrade=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, "打开失败", str(error))

    def save_review(self):
        if not self.doc or self.analysis_worker:
            return
        path = self.filepath
        if path is None:
            filename = "复盘_" + self.doc.headers.get("Date", "").replace(".", "-").replace("/", "-") + ".chessreview"
            chosen, _ = QFileDialog.getSaveFileName(self, "保存这盘复盘", str(data_dir() / filename), "复盘文件 (*.chessreview)")
            if not chosen:
                return
            path = Path(chosen)
        try:
            self.doc.save(path)
            self.filepath = path
            self.progress_label.setText("已保存")
        except OSError as error:
            self.show_error(f"保存失败：{error}")

    def autosave(self):
        if self.doc and self.doc.positions:
            try:
                self.doc.save(data_dir() / "last.chessreview")
            except OSError:
                self.show_error("自动保存失败，可点击“保存”另选目录。")

    def display_board(self) -> chess.Board:
        if self.comparison_pick:
            return self.comparison_pick[0].copy()
        if not self.doc:
            return chess.Board()
        if self.mode == "trial":
            return self.trial_board.copy()
        if self.mode == "variation":
            board = self.variation_seed.copy()
            for move in self.variation[:self.variation_index]:
                board.push_uci(move)
            return board
        return self.doc.board(self.ply)

    def current_key(self) -> str:
        if not self.doc:
            return ""
        review = self.doc.reviews.get(self.ply) if self.mode == "real" else None
        grading = f":grade-{GRADING_VERSION}:{review.label}" if review and review.evidence else ""
        if self.mode == "variation":
            grading += ":" + branch_key(self.variation_seed, self.variation, self.variation_index)
            if self.active_comparison:
                grading += ":" + hashlib.sha256(json.dumps(self.active_comparison.to_dict(), sort_keys=True).encode()).hexdigest()
        return cache_key(self.display_board(), self.ply, self.mode != "real",
                         self.settings.provider_id() + ":" + self.mode + ":" + COACH_VERSION + grading,
                         self.settings.active_model(), self.settings.endpoint() if self.settings.provider == "api" else "",
                         self.doc.color)

    def refresh(self):
        ready = bool(self.doc and self.doc.positions and not self.analysis_worker)
        browse_ready = bool(self.doc)
        display = self.display_board()
        captures, balance = material_snapshot(display)
        for widget, color in ((self.player_captures, self.board.orientation),
                              (self.opponent_captures, not self.board.orientation)):
            widget.set_material(captures[color], balance if color else -balance, color)
        last = display.peek() if display.move_stack else None
        arrow = None
        if self.mode == "variation" and self.variation_index < len(self.variation):
            arrow = chess.Move.from_uci(self.variation[self.variation_index])
        elif self.mode == "trial" and self.trial_position and self.trial_position.pv:
            arrow = chess.Move.from_uci(self.trial_position.pv[0])
        grade = self.doc.reviews[self.ply].label if self.doc and self.mode == "real" and self.ply in self.doc.reviews else ""
        annotation = self.doc.annotations.get(self.ply, "") if self.doc and self.mode == "real" else ""
        self.board.set_position(display, last, arrow, grade, annotation=annotation)
        self.board.interactive = bool(self.comparison_pick) or (self.mode == "trial" and not self.trial_worker and display.turn == (self.doc.color if self.doc else chess.WHITE) and not display.is_game_over())
        self.board.set_teaching_marks()
        self.guide_controls.setVisible(self.mode == "variation")
        self.comparison_choices.setVisible(self.mode == "variation" and self.active_comparison is not None)
        self.tabs.setMinimumHeight(130 if self.mode == "variation" else 230)
        self.mode_label.setText({"real": "实战", "variation": self.variation_name, "trial": "试走"}[self.mode])
        self.import_btn.setEnabled(not self.analysis_worker)
        self.open_btn.setEnabled(not self.analysis_worker)
        self.save_btn.setEnabled(ready)
        self.prev_btn.setEnabled(browse_ready and (self.ply > 0 if self.mode == "real" else (self.variation_index > 0 if self.mode == "variation" else bool(self.trial_history) and not self.trial_worker)))
        self.next_btn.setEnabled(browse_ready and (self.ply < len(self.doc.moves) if self.mode == "real" else self.mode == "variation" and self.variation_index < len(self.variation)))
        self.play_btn.setEnabled(ready and self.mode != "trial")
        self.view_side.setEnabled(not self.analysis_worker and not self.trial_worker)
        self.first_btn.setEnabled(browse_ready and self.mode != "trial")
        self.last_btn.setEnabled(browse_ready and self.mode != "trial")
        self.return_btn.setEnabled(self.mode != "real")
        self.recommend_btn.setEnabled(ready and self.mode == "real" and bool(self.recommended_line()[1]))
        self.recommend_btn.setVisible(self.mode != "variation")
        self.explain_btn.setVisible(self.mode != "variation")
        self.explain_btn.setText("讲解本步" if self.mode == "variation" else "解析")
        self.trial_btn.setEnabled(ready and not self.trial_worker and self.mode == "real")
        self.undo_btn.setEnabled(self.mode == "trial" and bool(self.trial_history) and not self.trial_worker)
        self.summary_btn.setEnabled(ready and not self.ai_worker)
        self.ask_btn.setEnabled(ready and not self.ai_worker and not self.trial_worker and not self.comparison_pick and not self.comparison_worker)
        self.explain_btn.setEnabled(ready and not self.ai_worker and not self.trial_worker and not self.comparison_pick and not self.comparison_worker)
        self.cancel_ai_btn.setEnabled(bool(self.ai_worker))
        self.graph.ply = self.variation_seed_ply if self.mode == "variation" else self.ply
        self.graph_title.setText("优势曲线 · " + ("白方" if self.graph.color else "黑方"))
        self.graph.setToolTip("点击跳转走法；上方为" + ("白方" if self.graph.color else "黑方") + "优势。")
        self.graph.update()
        if not self.doc:
            self.explanation.hide()
            self.demo_bar.hide()
            return
        self.badge.setStyleSheet("")
        self.grade_mark.clear()
        self.grade_mark.hide()
        self.best_label.hide()
        self.hint_label.hide()
        for widget, color in ((self.player_label, self.board.orientation), (self.opponent_label, not self.board.orientation)):
            side = "White" if color else "Black"
            role = "白方" if color else "黑方"
            rating = self.doc.headers.get(side + "Elo", "")
            widget.setText(f"{role} · {self.doc.headers.get(side, side)}" + (f"（{rating}）" if rating else ""))
        if self.mode == "trial":
            self.move_heading.setText(("白方" if self.doc.color else "黑方") + "试走")
            self.badge.setText("试走")
            self.score_label.setText("评分：" + (score_text(self.trial_position, self.doc.color) if self.trial_position else "计算中…"))
        elif self.mode == "variation":
            self.move_heading.setText(self.variation_name)
            self.badge.setText(f"{self.variation_index}/{len(self.variation)}")
            self.score_label.setText("" if not self.variation_index else pv_san(self.variation_seed, self.variation[:self.variation_index], 14).split()[-1])
            self.refresh_guide()
        elif self.ply in self.doc.reviews:
            review = self.doc.reviews[self.ply]
            before_board = self.doc.board(self.ply - 1)
            number = before_board.fullmove_number
            side = "白方" if before_board.turn else "黑方"
            self.move_heading.setText(f"{side} · {number}{'.' if before_board.turn else '...'}{self.doc.sans[self.ply - 1]}")
            self.badge.setText(review.label)
            color = LABEL_COLORS.get(review.label, "#8eaf70")
            self.badge.setStyleSheet(f"color: {color}; font-weight:600;")
            self.grade_mark.setPixmap(grade_icon(review.label).pixmap(QSize(28, 28)))
            self.grade_mark.show()
            self.score_label.setText(f"{'白方' if self.doc.color else '黑方'}视角：{score_text(review.before, self.doc.color)} → {score_text(review.played, self.doc.color)}")
            if review.before.pv and review.before.pv[0] != self.doc.moves[self.ply - 1]:
                before = self.doc.board(self.ply - 1)
                best = chess.Move.from_uci(review.before.pv[0])
                if best in before.legal_moves:
                    self.best_label.setText("最佳：" + pv_san(before, [best.uci()], 1))
                    self.best_label.show()
        else:
            self.badge.setStyleSheet("")
            self.badge.setText("起始" if self.ply == 0 else "待分析")
            before = self.doc.board(max(0, self.ply - 1))
            self.move_heading.setText("起始局面" if self.ply == 0 else f"{'白方' if before.turn else '黑方'} · {self.doc.sans[self.ply - 1]}")
            pending_score = "分析中…" if self.analysis_worker else "尚未分析"
            self.score_label.setText("评分：" + (score_text(self.doc.positions[self.ply], self.doc.color) if ready else pending_score))
            if ready and self.doc.positions[self.ply].pv:
                self.best_label.setText("推荐：" + pv_san(display, self.doc.positions[self.ply].pv[:1]))
                self.best_label.show()
        selection = self.variation_cells.get(self.variation_index) if self.mode == "variation" else self.table_cells.get(self.ply)
        if selection:
            row, col = selection
            self.table.setCurrentCell(row, col)
            self.table.scrollToItem(self.table.item(row, col))
        else:
            self.table.clearSelection()
        self.render_chat()
        self.refresh_usage()
        self.schedule_explanation()

    def refresh_guide(self):
        self.guide_explain_btn.setEnabled(not self.ai_worker and not self.comparison_pick and not self.comparison_worker)
        self.why_btn.setEnabled(not self.ai_worker and not self.comparison_pick and not self.comparison_worker)
        self.compare_btn.setEnabled(not self.comparison_worker)
        self.compare_btn.setText("取消选棋" if self.comparison_pick else "换个走法")
        self.hint_label.show()
        self.hint_label.setToolTip(line_goal(self.variation_seed, self.variation))
        if self.comparison_pick:
            self.board.arrow = None
            self.reference_btn.setEnabled(False)
            self.alternative_btn.setEnabled(False)
            side = "白方" if self.comparison_pick[0].turn else "黑方"
            self.move_heading.setText(side + " · 选择对比走法")
            self.hint_label.setText("在棋盘上点棋子与目标格，选择另一种走法。")
            self.score_label.setText("与推荐走法在同一局面比较")
        else:
            guide = guide_step(self.variation_seed, self.variation, self.variation_index)
            if guide:
                self.move_heading.setText(("白方" if guide.color else "黑方") + " · " + guide.san)
                self.hint_label.setText("目的：" + guide.purpose + ("\n" + guide.response if guide.response else ""))
                if self.intent_btn.isChecked():
                    self.board.set_teaching_marks(guide.arrows, guide.squares)
            else:
                self.hint_label.setText("线路：" + line_goal(self.variation_seed, self.variation))
        if not self.intent_btn.isChecked():
            self.board.arrow = None
        if self.comparison_worker:
            self.hint_label.setText("正在比较两种走法；仍可返回实战。")
        if self.comparison_pick:
            for control in (self.first_btn, self.prev_btn, self.play_btn, self.next_btn, self.last_btn):
                control.setEnabled(False)
        else:
            self.reference_btn.setEnabled(True)
            self.alternative_btn.setEnabled(True)
        if self.active_comparison:
            result = self.active_comparison
            seed = chess.Board(result.fen)
            side = "白方" if seed.turn else "黑方"
            self.score_label.setText(side + "视角 · " + result.description())
            for widget, position in ((self.reference_btn, result.recommended), (self.alternative_btn, result.alternative)):
                name = "推荐" if widget is self.reference_btn else "对比"
                widget.setText(name + " " + seed.san(chess.Move.from_uci(position.pv[0])) + "　" + score_text(position, seed.turn))
                widget.setChecked(self.variation == position.pv[:14])

    def reset_comparison(self):
        self.comparison_epoch = object()
        if self.comparison_worker:
            self.comparison_worker.cancelled.set()
        self.comparison_worker = None
        self.comparison_pick = None
        self.active_comparison = None

    def ask_why(self):
        if self.mode != "variation" or self.ai_worker or self.comparison_pick or self.comparison_worker:
            return
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        self.tabs.setCurrentIndex(1)
        self.request_ai(self.current_key(), self.step_context(),
                        "请解释这一步为什么这样走，具体解决什么威胁、创造什么机会，下一步怎样衔接；有对比时指出替代走法的区别。")

    def begin_comparison(self):
        if self.mode != "variation" or self.comparison_worker:
            return
        if self.comparison_pick:
            self.comparison_pick = None
            self.refresh()
            return
        index = max(0, self.variation_index - 1)
        if index >= len(self.variation):
            return
        seed = self.variation_seed.copy()
        for uci in self.variation[:index]:
            seed.push_uci(uci)
        self.cancel_ai()
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        reference = self.active_comparison.recommended.pv[0] if self.active_comparison and index == 0 else self.variation[index]
        self.comparison_pick = (seed, chess.Move.from_uci(reference))
        self.refresh()

    def run_comparison(self, alternative):
        if not self.comparison_pick or self.comparison_worker:
            return
        seed, recommended = self.comparison_pick
        if alternative not in seed.legal_moves:
            return
        if alternative == recommended:
            self.show_error("这就是当前推荐走法，请选另一种走法。")
            return
        key = comparison_key(seed, recommended, alternative, self.settings.engine)
        doc, epoch = self.doc, self.comparison_epoch
        def done(result):
            if self.doc is doc and self.comparison_epoch is epoch and self.mode == "variation":
                doc.comparisons[key] = result.to_dict()
                self.open_line(seed, result.alternative.pv, "走法对比", {}, "", step=1, comparison=result)
                self.progress_label.setText("对比完成，可以切换两条线路逐步查看。")
                self.autosave()
        try:
            cached = Comparison.load(doc.comparisons[key]) if key in doc.comparisons else None
        except (ValueError, KeyError, TypeError):
            cached = None
        if cached:
            done(cached)
            return
        self.progress_label.setText("本机引擎正在比较两种走法…")
        worker = Worker(lambda w: compare_moves(seed, recommended, alternative, self.settings.engine, w.cancelled), self)
        self.comparison_worker = worker
        self.workers.add(worker)
        worker.result.connect(done)
        def failed(message):
            if self.doc is doc and self.comparison_epoch is epoch:
                self.show_error(message)
        def finished():
            if self.comparison_worker is worker:
                self.comparison_worker = None
                self.refresh()
        worker.error.connect(failed)
        worker.finished.connect(finished)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        self.refresh()

    def switch_comparison(self, alternative):
        if not self.active_comparison or self.comparison_worker or self.comparison_pick:
            return
        result = self.active_comparison
        position = result.alternative if alternative else result.recommended
        self.open_line(self.variation_seed.copy(), position.pv, "走法对比", {}, "",
                       step=min(self.variation_index, len(position.pv)), comparison=result)

    def go(self, ply):
        if not self.doc:
            return
        self.play_timer.stop()
        self.reset_comparison()
        self.play_btn.setText("播放变化")
        self.ply = max(0, min(len(self.doc.moves), int(ply)))
        was_variation = self.mode == "variation"
        self.mode = "real"
        if was_variation:
            self.build_table()
        self.refresh()

    def navigate_shortcut(self, action):
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)):
            return
        if action == "left":
            self.step(-1)
        elif action == "right":
            self.step(1)
        elif action == "home":
            self.jump_boundary(False)
        elif action == "end":
            self.jump_boundary(True)

    def jump_boundary(self, end):
        if not self.doc or self.mode == "trial" or self.comparison_pick:
            return
        if self.mode == "variation":
            self.play_timer.stop()
            self.play_btn.setText("播放变化")
            self.variation_index = len(self.variation) if end else 0
            self.refresh()
        else:
            self.go(len(self.doc.moves) if end else 0)

    def step(self, direction):
        if not self.doc or self.comparison_pick:
            return
        if self.mode == "variation":
            self.play_timer.stop()
            self.play_btn.setText("播放变化")
            self.variation_index = max(0, min(len(self.variation), self.variation_index + direction))
            self.refresh()
        elif self.mode == "trial":
            if direction < 0:
                self.undo_trial()
        else:
            self.go(self.ply + direction)

    def recommended_line(self):
        if not self.doc or not self.doc.positions:
            return chess.Board(), []
        if self.ply in self.doc.reviews:
            return self.doc.board(self.ply - 1), self.doc.reviews[self.ply].before.pv
        return self.doc.board(self.ply), self.doc.positions[self.ply].pv

    def start_variation(self):
        if not self.doc or not self.doc.positions:
            return
        seed, line = self.recommended_line()
        self.open_line(seed, line, "推荐变化", self.step_context(), self.current_key())

    def open_line(self, seed, line, name, context, origin_key, step=0, autoplay=False, comparison=None):
        self.reset_comparison()
        self.active_comparison = comparison
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        seed_ply = next((ply for ply, fen in enumerate(self.doc.fens) if fen == seed.fen()), None)
        self.variation_seed_ply = seed_ply if seed_ply is not None else self.ply
        self.variation_seed = self.doc.board(seed_ply) if seed_ply is not None else seed.copy()
        if seed_ply is None:
            # Coach links carry a FEN; recover the trial/branch history for capture totals.
            cursor = self.display_board()
            while True:
                if cursor.fen() == seed.fen():
                    self.variation_seed = cursor
                    break
                if not cursor.move_stack:
                    break
                cursor.pop()
        self.variation = []
        board = self.variation_seed.copy()
        for uci in line[:14]:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                break
            self.variation.append(uci)
            board.push(move)
        if not self.variation:
            self.show_error("这个局面没有后续变化。")
            return
        self.mode = "variation"
        self.variation_name = name
        self.variation_context = context
        self.variation_origin_key = origin_key
        self.variation_index = max(0, min(len(self.variation), step))
        self.build_table()
        self.tabs.setCurrentIndex(0)
        self.refresh()
        if autoplay:
            self.play_btn.setText("暂停")
            self.play_timer.start()

    def coach_link(self, url):
        if url.scheme() != "coach" or url.host() != "line" or not self.doc or not self.doc.positions or self.analysis_worker:
            return
        try:
            index, step = (int(value) for value in url.path().strip("/").split("/"))
            if not 0 <= index < len(self.demo_lines):
                return
            line = self.demo_lines[index]
            context, key = self.demo_context, self.rendered_coach_key
        except (ValueError, AttributeError):
            return
        moves = line.moves
        for candidate in context.get("可演示线路", []):
            if candidate.get("起始FEN") == line.fen and candidate.get("uci", [])[:len(moves)] == moves:
                moves = candidate["uci"]
                break
        name = "对方应对" if line.name == "对方应对" else "推荐变化"
        self.open_line(chess.Board(line.fen), moves, name, context, key, step)

    def toggle_play(self):
        if self.comparison_pick:
            return
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setText("播放变化")
            return
        if self.mode != "variation":
            self.start_variation()
        if self.mode == "variation":
            if self.variation_index == len(self.variation):
                self.variation_index = 0
            self.play_btn.setText("暂停")
            self.play_timer.start()

    def play_tick(self):
        if self.mode != "variation" or self.variation_index >= len(self.variation):
            self.play_timer.stop()
            self.play_btn.setText("播放变化")
            return
        self.variation_index += 1
        self.refresh()
        if self.variation_index >= len(self.variation):
            self.play_timer.stop()
            self.play_btn.setText("播放变化")

    def return_real(self):
        self.reset_comparison()
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        self.mode = "real"
        self.build_table()
        self.refresh()

    def flip(self):
        self.board.orientation = not self.board.orientation
        self.refresh()

    def start_trial(self):
        if not self.doc or not self.doc.positions or self.trial_worker:
            return
        self.play_timer.stop()
        self.play_btn.setText("播放变化")
        seed_ply = self.ply
        board = self.doc.board(seed_ply)
        if board.turn != self.doc.color and seed_ply:
            seed_ply -= 1
            board = self.doc.board(seed_ply)
        if board.turn != self.doc.color or board.is_game_over():
            self.show_error("请选择当前视角一方可以走棋的局面再试走。")
            return
        self.trial_board = board
        self.trial_history = []
        self.trial_position = self.doc.positions[seed_ply]
        self.mode = "trial"
        self.refresh()

    def user_trial_move(self, move):
        if self.comparison_pick:
            self.run_comparison(move)
            return
        if self.mode != "trial" or self.trial_worker or not self.doc:
            return
        if self.trial_board.turn != self.doc.color or move not in self.trial_board.legal_moves:
            return
        self.trial_history.append(self.trial_board.copy())
        self.trial_board.push(move)
        self.trial_position = None
        board = self.trial_board.copy()
        expected_fen = board.fen()
        doc = self.doc
        self.progress_label.setText("引擎正在考虑对方的最佳应对…")

        def done(result):
            if self.doc is doc and self.mode == "trial" and self.trial_board.fen() == expected_fen:
                self.trial_board, self.trial_position, response = result
                self.progress_label.setText("对方已走最佳应对，你可以继续试走。" if response else "试走局面已结束。")

        worker = Worker(lambda w: analyse_trial(board, doc.color, self.settings.engine), self)
        self.trial_worker = worker
        self.workers.add(worker)
        worker.result.connect(done)
        worker.error.connect(self.trial_failed)
        worker.finished.connect(self.trial_finished)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        self.refresh()

    def trial_failed(self, error):
        # Restore the user's turn if the engine cannot respond; never leave a dead branch.
        if self.mode == "trial" and self.trial_history:
            self.trial_board = self.trial_history.pop()
            self.trial_position = None
        self.show_error(error)

    def trial_finished(self):
        self.trial_worker = None
        self.refresh()

    def undo_trial(self):
        if self.mode != "trial" or self.trial_worker or not self.trial_history:
            return
        self.trial_board = self.trial_history.pop()
        self.trial_position = None
        self.refresh()
        # Recompute the restored position so subsequent questions have current evidence.
        board, doc = self.trial_board.copy(), self.doc
        expected = board.fen()

        def done(result):
            if self.mode == "trial" and self.doc is doc and self.trial_board.fen() == expected:
                self.trial_board, self.trial_position, _ = result

        worker = Worker(lambda w: analyse_trial(board, doc.color, self.settings.engine), self)
        self.trial_worker = worker
        self.workers.add(worker)
        worker.result.connect(done)
        worker.error.connect(self.show_error)
        worker.finished.connect(self.trial_finished)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        self.refresh()

    def schedule_explanation(self, *_):
        if hasattr(self, "ai_timer"):
            self.ai_timer.start()

    def auto_explain(self):
        if (not self.no_ai and self.auto_ai.isChecked() and self.doc and self.doc.positions
                and not self.analysis_worker and not self.comparison_pick and not self.comparison_worker
                and not self.play_timer.isActive()
                and ((self.mode == "real" and self.ply in self.doc.reviews) or self.mode == "variation")
                and not self.ai_worker and not self.doc.chats.get(self.current_key())):
            self.explain_step()

    def step_context(self):
        if self.mode == "variation":
            context = teaching_context(self.variation_seed, self.variation, self.variation_index, self.doc.color)
            if self.active_comparison:
                context["替代走法对比"] = self.active_comparison.context()
            return context
        context = context_for(self.doc, self.ply, self.display_board(), self.mode != "real")
        if self.mode == "trial" and self.trial_position:
            context.update({"本人视角评分": score_text(self.trial_position, self.doc.color),
                            "当前局面的最佳后续": pv_san(self.trial_board, self.trial_position.pv),
                            "后续线路的子力核算": material_facts(self.trial_board, self.trial_position.pv, self.doc.color),
                            "可演示线路": [line_context(self.trial_board, self.trial_position.pv, "continuation", "后续")]})
        return context

    def explain_step(self):
        if not self.doc or not self.doc.positions or self.ai_worker or self.trial_worker or self.comparison_pick or self.comparison_worker:
            return
        key = self.current_key()
        if self.doc.chats.get(key):
            self.render_chat()
            return
        if self.mode == "real" and self.ply in self.doc.reviews:
            side = "白方" if self.doc.board(self.ply - 1).turn else "黑方"
            question = f"请简短解释{side}刚才这一步的得失、另一方的应对和更好的选择，用白方、黑方称呼。"
        elif self.mode == "variation":
            question = "请用两三句话解释线路当前这一步的目的、对方的应对和后续衔接。若停在起点，先说明整条线路的目标。"
            if self.active_comparison:
                question += "请再用一句话明确对比两种选择：为什么另一种走法更差，或为什么两种都可以；必须引用提供的对比线路，不能只重复评分。"
        else:
            question = "请简短解释当前局面的威胁、计划和接下来的选择，用白方、黑方称呼。"
        self.request_ai(key, self.step_context(), question)

    def ask(self):
        question = self.question.text().strip()
        if not question or not self.doc or not self.doc.positions or self.ai_worker or self.trial_worker or self.comparison_pick or self.comparison_worker:
            return
        self.question.clear()
        self.tabs.setCurrentIndex(1)
        self.request_ai(self.current_key(), self.step_context(), question)

    def summary_key(self):
        if not self.doc:
            return ""
        raw = json.dumps([self.doc.pgn, self.doc.color, self.settings.provider_id(), self.settings.active_model(), self.settings.endpoint(), COACH_VERSION], ensure_ascii=False)
        return "summary:" + hashlib.sha256(raw.encode()).hexdigest()

    def generate_summary(self):
        if not self.doc or not self.doc.positions or self.ai_worker:
            return
        self.tabs.setCurrentIndex(2)
        key = self.summary_key()
        if self.doc.chats.get(key):
            self.summary.setPlainText(self.doc.chats[key][-1]["content"])
            return
        self.request_ai(key, summary_context(self.doc), "请总结这盘最值得记住的两三个问题，以及下一盘可以注意的事。", summary=True)

    def request_ai(self, key, context, question, summary=False):
        if self.ai_worker:
            return
        doc = self.doc
        settings = copy.deepcopy(self.settings)
        client = self.client
        history = list(doc.chats.get(key, []))
        self.ai_key = key
        self.pending_question = question
        self.stream_text = ""
        self.progress_label.setText("正在讲解…")

        def work(worker):
            if settings.provider == "codex":
                return client.explain(context, history, question, settings.model, worker.delta.emit, worker.cancelled)
            return api_explain(settings, context, history, question, worker.delta.emit, worker.cancelled)

        def delta(text):
            self.stream_text = text
            if self.doc is not doc:
                return
            if summary and self.summary_key() == key:
                self.summary.setPlainText(text)
            elif not summary and self.current_key() == key:
                self.render_chat()

        def done(result):
            answer, usage = result
            doc.chats.setdefault(key, []).extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
            doc.usage.append({"provider": settings.provider_id(), "model": settings.active_model(), "time": time.time(), "tokens": usage})
            if self.doc is doc:
                if summary and self.summary_key() == key:
                    self.summary.setPlainText(answer)
                self.autosave()
                self.progress_label.setText("讲解完成")
                self.refresh_usage()

        def error(message):
            if self.doc is doc:
                if summary:
                    self.summary.setPlainText(message)
                elif self.current_key() == key:
                    self.explanation.setPlainText(message)
                self.show_error(message)
            # Failed requests are not cached and never trigger paid fallback automatically.

        def finished():
            self.ai_worker = None
            self.ai_key = ""
            self.stream_text = ""
            self.render_chat(preserve_error=True)
            self.refresh_controls()
            if self.doc is doc and self.current_key() != key and not summary:
                self.schedule_explanation()

        worker = Worker(work, self)
        self.ai_worker = worker
        self.workers.add(worker)
        worker.delta.connect(delta)
        worker.result.connect(done)
        worker.error.connect(error)
        worker.finished.connect(finished)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()
        self.refresh_controls()
        self.render_chat()

    def refresh_controls(self):
        ready = bool(self.doc and self.doc.positions and not self.analysis_worker)
        self.ask_btn.setEnabled(ready and not self.ai_worker and not self.trial_worker and not self.comparison_pick and not self.comparison_worker)
        self.explain_btn.setEnabled(ready and not self.ai_worker and not self.trial_worker and not self.comparison_pick and not self.comparison_worker)
        self.cancel_ai_btn.setEnabled(bool(self.ai_worker))
        self.summary_btn.setEnabled(ready and not self.ai_worker)
        if self.mode == "variation":
            self.refresh_guide()

    def render_chat(self, preserve_error=False):
        if not self.doc:
            return
        key = self.current_key()
        history = self.doc.chats.get(key, [])
        context = self.step_context()
        origin_fallback = False
        if (not history and self.mode == "variation" and self.variation_origin_key
                and not (self.ai_worker and self.ai_key == key)):
            key = self.variation_origin_key
            history = self.doc.chats.get(key, [])
            context = self.variation_context
            origin_fallback = True
        self.rendered_coach_key = key
        self.demo_context = context
        self.demo_lines = []
        self.demo_bar.clear()
        self.demo_bar.hide()
        answers = [item["content"] for item in history if item["role"] == "assistant"]
        if answers:
            body, lines = explanation_html(answers[0], context, theme="light")
            self.demo_lines.extend(lines)
            if not origin_fallback:
                self.set_coach_body(body)
                self.explanation.show()
                if self.mode == "variation" and not self.comparison_pick:
                    self.hint_label.hide()  # Detailed coach prose replaces the basic local caption.
            else:
                self.explanation.clear()
                self.explanation.hide()
                _, separator, links = body.partition('<p style="margin-top:')
                if separator:
                    self.demo_bar.setText(separator + links)
                    self.demo_bar.show()
        elif self.ai_key == key and self.ai_worker:
            body, lines = explanation_html(getattr(self, "stream_text", "") or "正在思考…", context, theme="light")
            self.set_coach_body(body)
            self.demo_lines.extend(lines)
            self.explanation.show()
        elif not preserve_error:
            self.explanation.clear()
            self.explanation.hide()
        elif not self.explanation.toPlainText():
            self.explanation.hide()
        parts = []
        for item in history:
            if item["role"] == "user":
                body = html.escape(item["content"])
            else:
                body, lines = explanation_html(item["content"], context, len(self.demo_lines))
                self.demo_lines.extend(lines)
            name = "你" if item["role"] == "user" else "教练"
            parts.append(f'<p style="color:#aaa7a1;margin-bottom:5px">{name}</p><div>{body}</div>')
        if self.ai_key == key and self.ai_worker:
            body, lines = explanation_html(getattr(self, "stream_text", "") or "正在思考…", context, len(self.demo_lines))
            self.demo_lines.extend(lines)
            parts += ["<p>你</p>" + html.escape(getattr(self, "pending_question", "")), "<p>教练</p>" + body]
        self.chat.setHtml("<br>".join(parts))
        if origin_fallback:
            # Retain origin demo links, but never pass old prose off as this step's explanation.
            self.chat.clear()
        if self.comparison_pick:
            self.explanation.hide()
            self.demo_bar.hide()
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())
        summaries = self.doc.chats.get(self.summary_key(), [])
        if summaries:
            self.summary.setPlainText(summaries[-1]["content"])
        elif not (self.ai_worker and self.ai_key == self.summary_key()):
            self.summary.clear()
        # Showing a reply changes the table height; keep its selected move in view.
        QTimer.singleShot(0, self.keep_move_visible)
        QTimer.singleShot(0, self.fit_coach_text)

    def set_coach_body(self, body):
        # Keep the demo controls visible even when a long explanation scrolls.
        prose, separator, links = body.partition('<p style="margin-top:')
        self.explanation.setHtml(prose)
        if separator:
            self.demo_bar.setText(separator + links)
            self.demo_bar.show()

    def refresh_usage(self):
        if not self.doc:
            return
        total = sum(item.get("tokens", {}).get("totalTokens", item.get("tokens", {}).get("total_tokens", 0)) or 0 for item in self.doc.usage)
        self.usage_label.setText(f"AI · {len(self.doc.usage)}次")
        self.usage_label.setToolTip(f"本盘AI请求：{len(self.doc.usage)}次 · 已记录{total:,}token")

    def cancel_ai(self):
        self.ai_timer.stop()
        if self.ai_worker:
            self.ai_worker.cancelled.set()
            self.progress_label.setText("正在停止讲解…")

    def configure(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            old_custom = self.settings.codex
            self.cancel_ai()
            self.settings = dialog.settings
            if self.settings.codex != old_custom:
                self.client.close()
                self.client = CodexClient(self.settings.codex)
            self.source.blockSignals(True)
            self.source.setCurrentIndex(0 if self.settings.provider == "codex" and self.settings.model == "gpt-6-luna" else (1 if self.settings.provider == "codex" else 2))
            self.source.blockSignals(False)
            self.refresh()
            if self.settings.provider == "codex":
                self.refresh_account()

    def change_source(self, index):
        self.settings.provider = "codex" if index < 2 else "api"
        if index < 2:
            self.settings.model = "gpt-6-luna" if index == 0 else "gpt-6-sol"
        self.settings.save()
        self.refresh()

    def refresh_account(self):
        if not self.account_btn.isEnabled():
            return
        self.account_btn.setEnabled(False)
        self.account_label.setText("正在读取Codex额度…")
        client = self.client

        def done(status):
            if not status.get("logged_in"):
                self.account_label.setText("Codex未登录 · 在设置中登录")
                return
            limits = status.get("limits", {}).get("rateLimitsByLimitId", {})
            bucket = limits.get("codex") or status.get("limits", {}).get("rateLimits") or {}
            values = []
            for key, name in (("primary", "5小时"), ("secondary", "每周")):
                window = bucket.get(key)
                if window and window.get("usedPercent") is not None:
                    remaining = max(0, min(100, 100 - window["usedPercent"]))
                    values.append(f"{name}剩余{remaining:g}%")
            self.account_label.setText("Codex · " + " · ".join(values) if values else "Codex已登录 · 额度数据暂不可用")

        def error(message):
            self.account_label.setText("额度暂不可用 · 可重试")
            self.account_label.setToolTip(message)

        worker = self.launch(lambda w: client.status(), done, error)
        worker.finished.connect(lambda: self.account_btn.setEnabled(True))

    def start_login(self):
        client = self.client

        def done(result):
            url = result.get("authUrl")
            if url:
                QDesktopServices.openUrl(QUrl(url))
                self.progress_label.setText("浏览器登录完成后，请点击“刷新额度”。")
            else:
                self.show_error("登录请求没有返回地址，请检查Codex程序路径。")

        self.launch(lambda w: client.login(), done)

    def closeEvent(self, event):
        self.closing = True
        self.ai_timer.stop()
        self.play_timer.stop()
        for worker in list(self.workers):
            worker.cancelled.set()
        self.client.close()
        if any(worker.isRunning() for worker in self.workers):
            event.ignore()
            QTimer.singleShot(250, self.close)
            return
        self.autosave()
        event.accept()
