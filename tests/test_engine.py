import threading

import chess
import pytest

from chessreview.core import GRADING_VERSION, Position, Review, parse_pgn
from chessreview.engine import analyse_game, complete_reviews


@pytest.fixture
def fake_engine(monkeypatch):
    class Engine:
        id = {"name": "Test engine"}
        closed = False

        def quit(self):
            self.closed = True

    engine = Engine()
    calls = []

    def analyse(engine, board, seconds, root_moves=None):
        calls.append((board.fen(), root_moves, seconds))
        if root_moves:
            move = root_moves[0]
            after = board.copy()
            after.push(move)
            reply = next(iter(after.legal_moves))
            return Position(-500 if board.turn else 500, None, [move.uci(), reply.uci()], 18)
        return Position(0, None, [next(iter(board.legal_moves)).uci()], 18)

    monkeypatch.setattr("chessreview.engine.start_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr("chessreview.engine.analyse_position", analyse)
    return engine, calls


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_both_sides_graded_from_actual_mover(fake_engine, color):
    doc = parse_pgn("1. e4 e5 2. Nf3 Nc6 *", color)
    progress = []
    analyse_game(doc, progress=lambda *args: progress.append(args))
    assert set(doc.reviews) == {1, 2, 3, 4}
    assert all(review.label == "严重失误" for review in doc.reviews.values())
    assert all(review.loss == 500 for review in doc.reviews.values())
    assert progress[-1][0] == progress[-1][1] == 13
    assert doc.grading_version == GRADING_VERSION
    assert fake_engine[0].closed


def test_legacy_upgrade_keeps_existing_analysis_and_chats(fake_engine):
    doc = parse_pgn("1. e4 e5 *", chess.BLACK)
    doc.positions = [Position(0, None, ["d2d4"]), Position(0, None, ["c7c5"]), Position()]
    black = Review(2, "好棋", 10, doc.positions[1], doc.positions[2], "已有分析")
    doc.reviews[2] = black
    doc.chats["old"] = [{"role": "assistant", "content": "已有讲解"}]
    positions = list(doc.positions)
    complete_reviews(doc)
    assert set(doc.reviews) == {1, 2}
    assert doc.reviews[2] is black
    assert doc.positions == positions
    assert doc.chats["old"][0]["content"] == "已有讲解"
    assert len(fake_engine[1]) == 1
    assert fake_engine[1][0][1][0].uci() == "e2e4"


def test_cancelled_upgrade_does_not_publish_partial_reviews(fake_engine):
    doc = parse_pgn("1. e4 e5 *", chess.WHITE)
    doc.positions = [Position() for _ in doc.fens]
    cancel = threading.Event()
    with pytest.raises(InterruptedError):
        complete_reviews(doc, cancel=cancel, progress=lambda *args: cancel.set())
    assert not doc.reviews
    assert fake_engine[0].closed
