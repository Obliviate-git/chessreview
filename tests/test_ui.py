import os
import threading
import time
from contextlib import nullcontext
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from chessreview.core import Position, Review, parse_pgn
from chessreview.ui import ImportDialog, MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSREVIEW_DATA_DIR", str(tmp_path / "data"))
    win = MainWindow(no_ai=True)
    doc = parse_pgn("1. e4 e5 2. Nf3 Nc6 *", chess.BLACK)
    doc.positions = [Position(25, None, pv) for pv in
                     (["e2e4"], ["e7e5", "g1f3", "b8c6"], ["g1f3"], ["b8c6"], [])]
    doc.reviews[2] = Review(2, "最佳", 0, doc.positions[1], doc.positions[2], "好棋")
    doc.reviews[4] = Review(4, "最佳", 0, doc.positions[3], doc.positions[4], "好棋")
    win.set_document(doc)
    win.show()
    QApplication.processEvents()
    yield win
    win.close()
    deadline = time.monotonic() + 10
    while win.workers and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)


def test_variation_returns_to_exact_real_position(window):
    window.go(2)
    original = window.display_board().fen()
    window.start_variation()
    assert window.mode == "variation"
    assert window.display_board().fen() == window.doc.board(1).fen()
    window.step(1)
    assert window.display_board().fen() == original
    window.step(1)
    assert window.display_board().piece_at(chess.F3) == chess.Piece(chess.KNIGHT, chess.WHITE)
    window.return_real()
    assert window.display_board().fen() == original
    assert window.ply == 2


def test_click_trial_engine_response_and_undo(window, monkeypatch):
    monkeypatch.setattr("chessreview.engine.start_engine", lambda *args, **kwargs: nullcontext(object()))
    monkeypatch.setattr(
        "chessreview.engine.analyse_position",
        lambda engine, board, seconds: Position(25, None, ["g1f3"] if board.turn == chess.WHITE else []),
    )
    window.go(2)
    real_fen = window.display_board().fen()
    window.start_trial()
    trial_start = window.trial_board.fen()
    assert window.trial_board.turn == chess.BLACK
    QTest.qWait(250)
    source = window.board.square_rect(chess.E7).center().toPoint()
    target = window.board.square_rect(chess.E5).center().toPoint()
    QTest.mouseClick(window.board, Qt.LeftButton, pos=source)
    QTest.mouseClick(window.board, Qt.LeftButton, pos=target)
    deadline = time.monotonic() + 15
    while window.trial_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert window.trial_worker is None
    assert window.trial_board.turn == chess.BLACK
    assert len(window.trial_board.move_stack) == 3
    assert window.trial_board.peek().uci() == "g1f3"
    window.undo_trial()
    while window.trial_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert window.trial_board.fen() == trial_start
    window.return_real()
    assert window.display_board().fen() == real_fen


def test_import_dialog_loads_sample_without_personal_metadata(app):
    dialog = ImportDialog()
    dialog.example()
    dialog.validate()
    assert dialog.document.board(86).is_checkmate()
    assert dialog.document.headers["Black"] == "黑方示例"
    assert "BlackElo" not in dialog.document.headers
    dialog.close()


def test_board_black_orientation_and_flip(window):
    assert window.board.square_rect(chess.H1).topLeft().x() < window.board.square_rect(chess.A1).topLeft().x()
    assert window.board.square_rect(chess.H1).topLeft().y() < window.board.square_rect(chess.H8).topLeft().y()
    window.flip()
    assert window.board.square_rect(chess.A8).topLeft().x() < window.board.square_rect(chess.H8).topLeft().x()


def test_question_input_only_appears_in_question_tab(window):
    assert not window.question.isVisible()
    window.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert window.question.isVisible()
    window.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert not window.question.isVisible()
    window.resize(1040, 760)
    QApplication.processEvents()
    assert window.table.viewport().height() >= 190


def test_best_move_label_on_non_best_own_move(window):
    review = window.doc.reviews[2]
    review.label = "可改进"
    review.before.pv = ["c7c5"]
    window.go(2)
    assert window.best_label.isVisible()
    assert "1...c5" in window.best_label.text()
    window.go(4)
    assert not window.best_label.isVisible()


def test_selected_move_stays_visible_when_explanation_uses_space(window):
    doc = parse_pgn(Path("examples/sample.pgn").read_text(encoding="utf-8"), chess.BLACK)
    doc.positions = [Position() for _ in doc.fens]
    window.set_document(doc)
    window.resize(1040, 760)
    window.go(69)
    QApplication.processEvents()
    doc.chats[window.current_key()] = [{"role": "assistant", "content": "注意对方的威胁。"}]
    window.render_chat()
    QApplication.processEvents()
    QApplication.processEvents()
    rect = window.table.visualItemRect(window.table.currentItem())
    assert window.table.viewport().rect().contains(rect.center())


def test_coach_demo_moves_and_return_preserve_real_position(window):
    doc = parse_pgn(Path("examples/sample.pgn").read_text(encoding="utf-8"), chess.BLACK)
    doc.positions = [Position() for _ in doc.fens]
    doc.positions[25] = Position(191, None, ["e7g5", "b2b4", "h5f4"])
    window.set_document(doc)
    window.go(25)
    fen = window.display_board().fen()
    key = window.current_key()
    doc.chats[key] = [{"role": "assistant", "content": "推荐Qg5，白方b4，再走Nf4。[[line:continuation:3]]"}]
    window.render_chat()
    QApplication.processEvents()
    assert window.demo_bar.isVisible()
    assert "演示推荐" in window.demo_bar.text()
    QTest.mouseClick(window.demo_bar, Qt.LeftButton,
                    pos=QPoint(35, window.demo_bar.height() // 2))
    assert not window.play_timer.isActive()
    assert window.variation_index == 0
    assert window.variation_cells
    for _ in range(3):
        window.play_tick()
    assert window.display_board().piece_at(chess.F4) == chess.Piece(chess.KNIGHT, chess.BLACK)
    assert window.display_board().piece_at(chess.G5) == chess.Piece(chess.QUEEN, chess.BLACK)
    assert window.display_board().piece_at(chess.B4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert "Nf4" in window.move_heading.text()
    assert window.explanation.toPlainText() == ""  # Do not reuse the origin step's prose.
    assert window.hint_label.isVisible()
    assert window.step_context()["这一步"] == "Nf4"
    assert len(doc.moves) == 86
    window.return_real()
    assert window.display_board().fen() == fen
    assert window.ply == 25
    # A move link jumps directly, without starting autoplay.
    window.coach_link(QUrl("coach://line/0/2"))
    assert window.variation_index == 2
    assert not window.play_timer.isActive()
    window.return_real()
    window.coach_link(QUrl("https://example.com"))
    assert window.mode == "real"


def test_inline_branch_clicks_and_replacement(window):
    from chessreview.visuals import MOVE_META
    window.go(2)
    real_moves = list(window.doc.moves)
    original_rows = window.table.rowCount()
    key = window.current_key()
    window.doc.chats[key] = [{"role": "assistant", "content":
        "推荐e5。[[line:recommend:1]] 对方可以Nf3。[[line:reply:1]]"}]
    # Supply both full lines, including moves not mentioned in the prose.
    window.doc.positions[2].pv = ["g1f3", "b8c6"]
    window.doc.reviews[2].played = window.doc.positions[2]
    window.render_chat()
    window.coach_link(QUrl("coach://line/0/0"))
    assert not window.play_timer.isActive()
    assert len(window.variation) == 3
    assert window.table.rowCount() > original_rows
    row, col = window.variation_cells[2]
    item = window.table.item(row, col)
    assert item.text() == "Nf3"
    QTest.mouseClick(window.table.viewport(), Qt.LeftButton,
                    pos=window.table.visualItemRect(item).center())
    assert window.variation_index == 2
    assert window.display_board().piece_at(chess.F3)
    window.coach_link(QUrl("coach://line/1/0"))
    assert window.variation_name == "对方应对"
    assert window.variation_seed.fen() == window.doc.board(2).fen()
    assert len(window.variation) == 2
    assert sum((window.table.item(row, 0).data(MOVE_META) or {}).get("kind") == "branch_header"
               for row in range(window.table.rowCount())) == 1
    window.toggle_play()
    assert window.play_timer.isActive()
    window.play_tick()
    assert window.variation_index == 1
    assert window.play_timer.isActive()
    window.step(-1)
    assert not window.play_timer.isActive()
    window.jump_boundary(True)
    assert window.variation_index == 2
    window.return_real()
    assert window.table.rowCount() == original_rows
    assert window.doc.moves == real_moves
    assert window.ply == 2


def test_graph_grade_colors_and_click_seek(window):
    from chessreview.visuals import MOVE_META
    row, col = window.table_cells[2]
    item = window.table.item(row, col)
    assert item.text() == "e5"
    assert item.data(MOVE_META)["grade"] == "最佳"
    assert window.graph.annotations == {2: "最佳", 4: "最佳"}
    point = window.graph.point_at(2).toPoint()
    QTest.mouseClick(window.graph, Qt.LeftButton, pos=point)
    assert window.ply == 2
    assert window.board.grade == "最佳"


def test_import_allows_browsing_while_analysis_is_running(window, monkeypatch):
    imported = parse_pgn("1. d4 d5 2. c4 e6 *", chess.WHITE)
    release_analysis = threading.Event()

    class ImportWithoutDialog:
        def __init__(self, parent):
            self.document = imported

        def exec(self):
            return QDialog.Accepted

    def slow_analysis(doc, custom, progress, cancel):
        while not release_analysis.wait(.01):
            if cancel.is_set():
                raise InterruptedError("分析已取消。")
        doc.positions = [Position(0, None, []) for _ in doc.fens]
        doc.reviews[1] = Review(1, "好棋", 0, doc.positions[0], doc.positions[1], "")
        return doc

    monkeypatch.setattr("chessreview.ui.ImportDialog", ImportWithoutDialog)
    monkeypatch.setattr("chessreview.ui.analyse_game", slow_analysis)
    try:
        window.import_game()
        assert window.doc is imported
        assert window.analysis_worker is not None
        assert window.table.rowCount() == 2
        assert window.table.item(*window.table_cells[1]).text() == "d4"
        assert window.next_btn.isEnabled()
        assert window.last_btn.isEnabled()
        assert not window.explain_btn.isEnabled()
        assert not window.save_btn.isEnabled()

        window.step(1)
        assert window.ply == 1
        assert window.board.position.piece_at(chess.D4) == chess.Piece(chess.PAWN, chess.WHITE)
        window.jump_boundary(True)
        assert window.ply == 4
        window.step(-1)
        assert window.ply == 3
        row, column = window.table_cells[2]
        item = window.table.item(row, column)
        QTest.mouseClick(window.table.viewport(), Qt.LeftButton,
                         pos=window.table.visualItemRect(item).center())
        assert window.ply == 2
        assert window.board.position.fen() == imported.board(2).fen()
        assert window.analysis_worker is not None
    finally:
        release_analysis.set()

    deadline = time.monotonic() + 5
    while window.analysis_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert window.analysis_worker is None
    assert window.ply == 2
    assert window.table.item(*window.table_cells[1]).data(Qt.UserRole) == 1
    assert window.graph.annotations[1] == "好棋"
    assert window.explain_btn.isEnabled()


def test_capture_strip_follows_board_side_and_branch(window):
    doc = parse_pgn("1. d4 d5 2. c4 dxc4 3. e3 e5 4. Bxc4 *", chess.WHITE)
    doc.positions = [Position() for _ in doc.fens]
    window.set_document(doc)
    window.go(4)
    assert window.player_captures.balance == -1
    assert window.player_captures.pieces == []
    assert window.opponent_captures.balance == 1
    assert window.opponent_captures.pieces == [chess.Piece(chess.PAWN, chess.WHITE)]
    assert "黑方子力领先1分" in window.opponent_captures.toolTip()
    window.flip()
    assert window.player_captures.color == chess.BLACK
    assert window.player_captures.balance == 1
    assert "黑方" in window.player_label.text()
    window.player_captures.grab()
    window.open_line(chess.Board(doc.fens[4]), ["e2e3", "e7e5", "f1c4"], "推荐变化", {}, "", step=3)
    assert window.player_captures.balance == window.opponent_captures.balance == 0
    assert window.opponent_captures.pieces == [chess.Piece(chess.PAWN, chess.BLACK)]
    window.return_real()
    assert window.player_captures.balance == 1
    window.go(6)
    window.start_trial()
    window.trial_board.push_san("Bxc4")
    window.refresh()
    assert window.player_captures.balance == window.opponent_captures.balance == 0
    window.open_line(chess.Board(window.trial_board.fen()), ["b8c6"], "推荐变化", {}, "", step=1)
    assert window.player_captures.pieces == [chess.Piece(chess.PAWN, chess.WHITE)]
    assert window.opponent_captures.pieces == [chess.Piece(chess.PAWN, chess.BLACK)]
    window.return_real()
    window.go(3)
    assert window.player_captures.pieces == window.opponent_captures.pieces == []
    assert window.player_captures.balance == window.opponent_captures.balance == 0


def test_punctuation_badges_show_for_both_sides_before_analysis(window):
    from chessreview.visuals import MOVE_META
    doc = parse_pgn("1. e4! e5? 2. Nf3!! Nc6?? *", chess.BLACK)
    window.set_document(doc)
    for ply, symbol in doc.annotations.items():
        window.go(ply)
        window.board.stop_animation()
        assert window.board.annotation == symbol
        assert not window.board.move_badge_rect().isEmpty()
        assert window.table.item(*window.table_cells[ply]).data(MOVE_META)["annotation"] == symbol
        window.flip()
        assert window.board.annotation == symbol
        assert not window.board.move_badge_rect().isEmpty()
    window.go(0)
    assert window.board.annotation == ""
    assert window.board.move_badge_rect().isEmpty()


def test_white_side_branch_at_initial_position(window):
    doc = parse_pgn("1. e4 e5 2. Nf3 Nc6 *", chess.WHITE)
    doc.positions = [Position() for _ in doc.fens]
    doc.positions[0].pv = ["d2d4", "d7d5", "c2c4"]
    window.set_document(doc)
    window.start_variation()
    assert window.variation_cells[1] == (1, 1)
    assert window.table.item(1, 1).text() == "d4"
    assert window.table.item(*window.table_cells[1]).text() == "e4"
    window.jump_boundary(True)
    assert window.display_board().piece_at(chess.C4) == chess.Piece(chess.PAWN, chess.WHITE)
    window.table_seek(*window.table_cells[3])
    assert window.mode == "real"
    assert not window.variation_cells
    assert window.ply == 3


def test_compact_coach_controls_remain_visible(window):
    doc = parse_pgn(Path("examples/sample.pgn").read_text(encoding="utf-8"), chess.BLACK)
    doc.positions = [Position() for _ in doc.fens]
    doc.positions[25].pv = ["e7g5", "b2b4", "h5f4"]
    window.set_document(doc)
    window.resize(1040, 760)
    window.go(25)
    doc.chats[window.current_key()] = [{"role": "assistant", "content":
        "注意对方的威胁。" * 30 + "[[line:continuation:3]]"}]
    window.render_chat()
    for _ in range(3):
        QApplication.processEvents()
    assert window.demo_bar.isVisible()
    assert window.coach_card.rect().contains(window.demo_bar.geometry())
    assert window.table.viewport().height() >= 190
    QTest.mouseClick(window.demo_bar, Qt.LeftButton,
                    pos=QPoint(35, 8))
    assert window.mode == "variation"
    assert window.variation_index == 0


def test_switch_view_updates_board_graph_and_trial_side_without_changing_grades(window):
    doc = window.doc
    doc.reviews[1] = Review(1, "好棋", 0, doc.positions[0], doc.positions[1], "")
    window.build_table()
    window.go(1)
    black_key = window.current_key()
    grades = {ply: review.label for ply, review in doc.reviews.items()}
    assert "白方 · 1.e4" == window.move_heading.text()
    assert "黑方视角" in window.score_label.text()
    assert window.step_context()["分析方"] == "白方"
    assert window.recommended_line()[0].fen() == doc.board(0).fen()
    window.view_side.setCurrentIndex(0)
    assert doc.color == chess.WHITE
    assert window.board.orientation == window.graph.color == chess.WHITE
    assert window.ply == 1
    assert window.current_key() != black_key
    assert "白方视角" in window.score_label.text()
    assert "白方" in window.player_label.text()
    assert "黑方" in window.opponent_label.text()
    assert {ply: review.label for ply, review in doc.reviews.items()} == grades
    window.start_trial()
    assert window.trial_board.turn == chess.WHITE
    window.return_real()
    window.view_side.setCurrentIndex(1)
    window.start_trial()
    assert window.trial_board.turn == chess.BLACK


def test_auto_explanation_can_run_on_white_move_in_black_view(window, monkeypatch):
    doc = window.doc
    doc.reviews[1] = Review(1, "好棋", 0, doc.positions[0], doc.positions[1], "")
    window.go(1)
    window.no_ai = False
    window.auto_ai.setChecked(True)
    requests = []
    monkeypatch.setattr(window, "request_ai", lambda key, context, question: requests.append((context, question)))
    window.auto_explain()
    assert requests[0][0]["走棋方"] == "白方"
    assert "白方刚才这一步" in requests[0][1]


def test_open_legacy_review_automatically_completes_both_sides(window, monkeypatch):
    doc = window.doc

    def complete(doc, custom, progress, cancel):
        from chessreview.core import GRADING_VERSION
        for ply in (1, 3):
            doc.reviews[ply] = Review(ply, "好棋", 0, doc.positions[ply - 1], doc.positions[ply], "")
        doc.grading_version = GRADING_VERSION
        return doc

    monkeypatch.setattr("chessreview.ui.complete_reviews", complete)
    window.set_document(doc, upgrade=True)
    deadline = time.monotonic() + 5
    while window.analysis_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert window.analysis_worker is None
    assert set(window.graph.annotations) == {1, 2, 3, 4}
    assert window.view_side.isEnabled()
    monkeypatch.setattr(window, "start_analysis", lambda: pytest.fail("重复分析完整结果"))
    window.set_document(doc, upgrade=True)


def test_guided_branch_updates_purpose_marks_and_current_step_question(window, monkeypatch):
    window.go(2)
    window.start_variation()
    assert window.guide_controls.isVisible()
    assert window.guide_explain_btn.isVisible() and not window.explain_btn.isVisible()
    assert "线路" in window.hint_label.text()
    window.step(1)
    context = window.step_context()
    assert context["走棋方"] == "黑方" and context["这一步"] == "e5"
    assert "目的" in window.hint_label.text()
    assert window.board.teaching_arrows
    first_key = window.current_key()
    window.step(1)
    assert window.step_context()["走棋方"] == "白方"
    assert window.current_key() != first_key
    window.intent_btn.setChecked(False)
    window.refresh()
    assert not window.board.teaching_arrows and window.board.arrow is None
    requests = []
    monkeypatch.setattr(window, "request_ai", lambda key, context, question: requests.append(context))
    window.ask_why()
    assert requests[0]["这一步"] == "Nf3"
    assert window.tabs.currentIndex() == 1
    window.return_real()
    assert not window.guide_controls.isVisible() and not window.board.teaching_arrows


def test_guided_auto_explanation_waits_until_playback_stops(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, "request_ai", lambda key, context, question: requests.append(context))
    window.go(2)
    window.start_variation()
    window.step(1)
    window.no_ai = False
    window.auto_ai.setChecked(True)
    window.play_timer.start()
    window.auto_explain()
    assert requests == []
    window.play_timer.stop()
    window.auto_explain()
    assert requests[0]["模式"] == "逐步讲解" and requests[0]["这一步"] == "e5"


def test_alternative_board_choice_compares_same_root_and_is_cached(window, monkeypatch):
    from chessreview.teaching import Comparison
    window.go(2)
    real_fen = window.display_board().fen()
    window.start_variation()
    window.step(1)
    root = window.variation_seed.copy()
    calls = []
    def compare(seed, preferred, alternative, custom, cancelled):
        calls.append((seed.fen(), preferred.uci(), alternative.uci()))
        return Comparison(seed.fen(), Position(25, None, ["e7e5", "g1f3", "b8c6"], 18),
                          Position(28, None, ["c7c5", "g1f3", "b8c6"], 18))
    monkeypatch.setattr("chessreview.ui.compare_moves", compare)
    window.begin_comparison()
    assert window.board.interactive
    assert window.display_board().fen() == root.fen()
    assert not window.next_btn.isEnabled()
    window.user_trial_move(chess.Move.from_uci("c7c5"))
    deadline = time.monotonic() + 5
    while window.comparison_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert window.active_comparison is not None
    assert window.comparison_choices.isVisible()
    assert calls == [(root.fen(), "e7e5", "c7c5")]
    assert "两种走法" in window.score_label.text()
    assert window.display_board().piece_at(chess.C5) == chess.Piece(chess.PAWN, chess.BLACK)
    assert window.step_context()["替代走法对比"]["对比评分视角"] == "黑方"
    window.resize(1040, 760)
    QTest.qWait(100)
    assert window.tabs.geometry().bottom() < window.graph_title.geometry().top()
    assert window.hint_label.height() >= window.hint_label.fontMetrics().height() * 2
    window.switch_comparison(False)
    assert window.display_board().fen() == real_fen
    window.begin_comparison()
    window.user_trial_move(chess.Move.from_uci("c7c5"))
    assert len(calls) == 1
    window.return_real()
    assert window.display_board().fen() == real_fen


def test_slow_comparison_cannot_replace_board_after_return_to_game(window, monkeypatch):
    from chessreview.teaching import Comparison
    started, release = threading.Event(), threading.Event()
    def compare(seed, preferred, alternative, custom, cancelled):
        started.set()
        release.wait(3)
        return Comparison(seed.fen(), Position(0, None, [preferred.uci()]), Position(0, None, [alternative.uci()]))
    monkeypatch.setattr("chessreview.ui.compare_moves", compare)
    window.go(2)
    fen = window.display_board().fen()
    window.start_variation()
    window.begin_comparison()
    window.user_trial_move(chess.Move.from_uci("c7c5"))
    assert started.wait(2)
    window.return_real()
    release.set()
    deadline = time.monotonic() + 5
    while window.workers and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert window.mode == "real" and window.display_board().fen() == fen
    assert not window.active_comparison and not window.doc.comparisons
