import threading
import time

import chess
import pytest

from chessreview.core import GRADING_VERSION, Document, Position, Review, parse_pgn
from chessreview.engine import PositiveBudget, complete_reviews, exceptional_review, sacrifice_reply


class SearchResults:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def analyse(self, engine, board, roots, seconds=.35):
        self.calls.append((board.fen(), roots))
        return self.results.pop(0)


def review_for(board, move, cp=0, mate=None):
    position = Position(cp if board.turn else -cp, mate, [move.uci()], 20)
    return Review(1, "最佳", 0, position, position, "已有讲解")


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_only_clearly_superior_move_receives_exclamation(color):
    board = chess.Board()
    if color == chess.BLACK:
        board.push_uci("e2e4")
    move = board.parse_san("d4" if color else "d5")
    sign = 1 if color else -1
    alternative = next(other for other in board.legal_moves if other != move)
    review = review_for(board, move)
    budget = SearchResults(Position(10 * sign, None, [move.uci()], 19),
                           Position(-200 * sign, None, [alternative.uci()], 19))
    result = exceptional_review(None, board, move, review, budget)
    assert result.label == "关键走法"
    assert "相差至少1.50分" in result.evidence
    assert result.before is review.before and result.played is review.played
    assert review.label == "最佳"  # Atomic migration must not mutate old reviews.


@pytest.mark.parametrize("alternative_cp,depth", [(0, 20), (-149, 20), (-500, 12)])
def test_routine_best_or_unreliable_search_is_not_marked(alternative_cp, depth):
    board = chess.Board()
    move = board.parse_san("e4")
    alternative = board.parse_san("d4")
    review = review_for(board, move)
    budget = SearchResults(Position(0, None, [move.uci()], 20),
                           Position(alternative_cp, None, [alternative.uci()], depth))
    assert exceptional_review(None, board, move, review, budget) is review


def sacrifice_position():
    # The rook is offered for a pawn; black's king takes it. Material remains lost.
    board = chess.Board("4k3/4p3/8/8/8/8/4R3/K7 w - - 0 1")
    move = board.parse_san("Rxe7+")
    return board, move, ["e8e7", "a1b1", "e7d7", "b1c1", "d7e7"]


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_brilliant_requires_net_sacrifice_and_verified_compensation(color):
    board, move, pv = sacrifice_position()
    if color == chess.BLACK:
        board = board.mirror()
        def mirrored(uci):
            item = chess.Move.from_uci(uci)
            return chess.Move(chess.square_mirror(item.from_square), chess.square_mirror(item.to_square)).uci()
        move = chess.Move.from_uci(mirrored(move.uci()))
        pv = [mirrored(uci) for uci in pv]
    review = review_for(board, move)
    budget = SearchResults(Position(0, None, [move.uci()] + pv, 20),
                           Position(0, None, pv, 20))
    result = exceptional_review(None, board, move, review, budget)
    assert result.label == "妙招" and "净弃子至少2分" in result.evidence
    review.label = "好棋"
    review.played = Position(0, None, pv, 20)
    budget = SearchResults(Position(0, None, [move.uci()] + pv, 20), Position(0, None, pv, 20))
    assert exceptional_review(None, board, move, review, budget).label == "妙招"
    # The fixture has no real compensation: a losing capture search must reject it.
    budget = SearchResults(Position(0, None, [move.uci()] + pv, 20),
                           Position(-450 if color else 450, None, pv, 20))
    assert exceptional_review(None, board, move, review, budget) is review


def test_equal_exchange_and_immediate_recapture_are_not_brilliant():
    board = chess.Board("3rk3/8/8/8/8/8/8/3RK3 w - - 0 1")
    move = board.parse_san("Rxd8+")
    assert sacrifice_reply(board, move, ["e8d8", "e1d2"]) is None
    board = parse_pgn("1. e4 e5 2. Nf3 Nc6 3. d4 exd4 *", chess.WHITE).board(6)
    move = board.parse_san("Nxd4")
    review = review_for(board, move)
    budget = SearchResults(Position(0, None, [move.uci()], 20))
    assert exceptional_review(None, board, move, review, budget) is review
    assert len(budget.calls) == 1  # No comparison with artificial non-recaptures.


def test_verification_budget_and_cancel_are_respected(monkeypatch):
    budget = PositiveBudget(time.monotonic() - 1)
    monkeypatch.setattr("chessreview.engine.analyse_position", lambda *args: pytest.fail("超时仍搜索"))
    assert budget.analyse(None, chess.Board(), []) is None
    budget = PositiveBudget(time.monotonic() + 30, threading.Event())
    budget.cancel.set()
    with pytest.raises(InterruptedError):
        budget.analyse(None, chess.Board(), [])


def test_complete_saved_review_upgrades_once_and_keeps_chats(monkeypatch, tmp_path):
    doc = parse_pgn("1. e4 e5 *", chess.WHITE)
    doc.positions = [Position() for _ in doc.fens]
    for ply in (1, 2):
        doc.reviews[ply] = Review(ply, "好棋", 0, Position(), Position(), "已有说明")
    doc.chats["old"] = [{"role": "assistant", "content": "保留追问"}]
    old_reviews = dict(doc.reviews)

    class Engine:
        def quit(self):
            pass

    monkeypatch.setattr("chessreview.engine.start_engine", lambda *args: Engine())
    complete_reviews(doc)
    assert doc.grading_version == GRADING_VERSION
    assert all(doc.reviews[ply] is review for ply, review in old_reviews.items())
    doc.reviews[1].evidence = "判定依据"
    doc.save(tmp_path / "saved.chessreview")
    loaded = Document.load(tmp_path / "saved.chessreview")
    assert loaded.grading_version == GRADING_VERSION and loaded.reviews[1].evidence == "判定依据"
    assert loaded.chats == doc.chats
    monkeypatch.setattr("chessreview.engine.start_engine", lambda *args: pytest.fail("重复升级"))
    assert complete_reviews(loaded) is loaded
