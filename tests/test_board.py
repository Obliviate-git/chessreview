import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chessreview.board import ChessBoard, adjacent_motion


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app):
    board = ChessBoard()
    board.resize(480, 480)
    board.show()
    QApplication.processEvents()
    yield board
    board.close()


def test_single_move_interpolates_and_finishes(widget):
    after = chess.Board()
    after.push_uci("e2e4")
    widget.set_position(after, after.peek())
    assert len(widget.motions) == 1
    motion = widget.motions[0]
    assert motion.source == chess.E2 and motion.target == chess.E4
    assert widget.position.fen() == after.fen()
    widget.animation.setCurrentTime(110)
    point = widget.motion_point(motion)
    assert min(motion.start.y(), motion.end.y()) < point.y() < max(motion.start.y(), motion.end.y())
    widget.grab()  # Exercises the intermediate painter, not only the logical board.
    QTest.qWait(250)
    assert not widget.motions
    assert widget.position.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)


def test_refresh_same_fen_keeps_animation_and_rapid_undo_retargets(widget):
    before = chess.Board()
    after = before.copy()
    after.push_uci("e2e4")
    widget.set_position(after)
    widget.animation.setCurrentTime(90)
    point = widget.motion_point(widget.motions[0])
    widget.set_position(after, grade="好棋")
    assert widget.motion_point(widget.motions[0]) == point
    widget.set_position(before)
    assert widget.motions[0].source == chess.E4
    assert widget.motions[0].start == point
    widget.animation.setCurrentTime(220)
    assert not widget.motions
    assert widget.position.fen() == before.fen()


def test_multi_step_jump_and_flip_are_instant(widget):
    after = chess.Board()
    after.push_uci("e2e4")
    after.push_uci("e7e5")
    widget.set_position(after)
    assert not widget.motions
    after.push_uci("g1f3")
    widget.set_position(after)
    assert widget.motions
    widget.orientation = chess.BLACK
    widget.set_position(after)
    assert not widget.motions
    assert widget.position.fen() == after.fen()


@pytest.mark.parametrize("fen,uci,king,rook", [
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", chess.G1, chess.F1),
    ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8c8", chess.C8, chess.D8),
])
def test_castling_animates_king_and_rook_in_both_directions(widget, fen, uci, king, rook):
    before = chess.Board(fen)
    after = before.copy()
    after.push_uci(uci)
    widget.set_position(before, animate=False)
    widget.set_position(after)
    assert {motion.target for motion in widget.motions} == {king, rook}
    widget.grab()
    widget.animation.setCurrentTime(220)
    widget.set_position(before)
    assert len(widget.motions) == 2
    assert {motion.source for motion in widget.motions} == {king, rook}


@pytest.mark.parametrize("fen,uci,victim", [
    ("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1", "e4d5", chess.D5),
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", chess.D5),
])
def test_capture_and_en_passant_overlays(widget, fen, uci, victim):
    before = chess.Board(fen)
    after = before.copy()
    after.push_uci(uci)
    widget.set_position(before, animate=False)
    widget.set_position(after)
    assert widget.capture_overlays[victim] == chess.Piece(chess.PAWN, chess.BLACK)
    widget.animation.setCurrentTime(100)
    widget.grab()
    widget.animation.setCurrentTime(220)
    widget.set_position(before)
    assert victim in widget.restore_squares
    widget.grab()


def test_promotion_moves_pawn_then_shows_queen_and_undo_restores_pawn(widget):
    before = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    after = before.copy()
    after.push_uci("a7a8q")
    widget.set_position(before, animate=False)
    widget.set_position(after)
    assert widget.motions[0].piece.piece_type == chess.PAWN
    widget.animation.setCurrentTime(220)
    assert widget.position.piece_at(chess.A8).piece_type == chess.QUEEN
    widget.set_position(before)
    assert widget.motions[0].piece.piece_type == chess.QUEEN
    widget.animation.setCurrentTime(220)
    assert widget.position.piece_at(chess.A7).piece_type == chess.PAWN


def test_input_is_blocked_only_while_piece_is_moving(widget):
    after = chess.Board()
    after.push_uci("e2e4")
    widget.set_position(after)
    widget.interactive = True
    target = widget.square_rect(chess.E7).center().toPoint()
    QTest.mouseClick(widget, Qt.LeftButton, pos=target)
    assert widget.selected is None
    widget.animation.setCurrentTime(220)
    QTest.mouseClick(widget, Qt.LeftButton, pos=target)
    assert widget.selected == chess.E7


def test_move_badge_appears_after_animation_and_stays_inside_edges(widget):
    before = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    after = before.copy()
    after.push_uci("a7a8q")
    widget.set_position(before, animate=False)
    widget.set_position(after, after.peek(), annotation="!!")
    assert widget.move_badge_rect().isEmpty()


    widget.animation.setCurrentTime(220)
    for orientation in (chess.WHITE, chess.BLACK):
        widget.orientation = orientation
        widget.set_position(after, after.peek(), annotation="!!")
        badge = widget.move_badge_rect()
        assert not badge.isEmpty()
        assert widget.rect().contains(badge.toAlignedRect())
        assert badge.intersects(widget.square_rect(chess.A8))
        widget.grab()
    widget.set_position(before, animate=False)
    assert widget.move_badge_rect().isEmpty()


@pytest.mark.parametrize("grade,visible", [("好棋", False), ("最佳", False), ("可改进", False),
                                         ("关键走法", True), ("妙招", True),
                                         ("失误", True), ("严重失误", True)])
def test_only_exceptional_positive_grades_get_board_marks(widget, grade, visible):
    board = chess.Board()
    board.push_uci("e2e4")
    widget.set_position(board, board.peek(), grade=grade, animate=False)
    assert widget.move_badge_rect().isEmpty() != visible
