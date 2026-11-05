import threading

import chess
import pytest

from chessreview.core import Document, Position, parse_pgn
from chessreview.engine import compare_moves
from chessreview.teaching import (Comparison, branch_key, guide_step, legal_line,
                                  line_goal, teaching_context)


def test_every_step_has_correct_mover_and_current_fen_independent_of_view():
    seed = chess.Board()
    moves = ["e2e4", "e7e5", "g1f3"]
    for step in range(1, 4):
        guide = guide_step(seed, moves, step)
        context = teaching_context(seed, moves, step, chess.BLACK)
        assert context["走棋方"] == ("白方" if step % 2 else "黑方")
        assert context["当前FEN"] == guide.after.fen()
        assert context["这一步"] == guide.san
        assert len(guide.arrows) <= 2
        assert all(source != target for source, target, _ in guide.arrows)
    assert teaching_context(seed, moves, 0, chess.BLACK)["线路步数"] == 0
    assert guide_step(seed, moves, 0) is None
    assert branch_key(seed, moves, 1) != branch_key(seed, moves, 2)
    assert branch_key(seed, moves, 1) != branch_key(seed, ["e2e4", "c7c5"], 1)


def test_terminal_comparisons_do_not_call_losing_mates_equivalent_good_choices():
    board = chess.Board()
    result = Comparison(board.fen(), Position(-99995, -5, ["e2e4"]), Position(-99997, -3, ["d2d4"]))
    assert result.description() == "两种走法都面临被将杀"


def test_discovered_check_arrow_points_from_actual_checker():
    board = chess.Board("4k3/8/8/8/8/8/4B3/4R1K1 w - - 0 1")
    guide = guide_step(board, ["e2h5"], 1)
    assert guide.after.is_check()
    assert guide.arrows[0][:2] == (chess.E1, chess.E8)
    assert "将军" in guide.purpose


def test_equal_exchange_is_not_explained_as_winning_a_piece():
    board = chess.Board("3rk3/8/8/8/8/8/8/3RK3 w - - 0 1")
    moves = ["d1d8", "e8d8"]
    guide = guide_step(board, moves, 1)
    assert "等值交换" in guide.purpose
    assert "赢" not in guide.purpose
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    assert "兵" in guide_step(board, ["e5d6"], 1).purpose


def test_mating_line_goal_and_every_ply_are_grounded():
    from pathlib import Path
    doc = parse_pgn(Path("examples/sample.pgn").read_text(encoding="utf-8"), chess.WHITE)
    seed = doc.board(83)
    moves = ["d6c5", "g5c5", "d7d2"]
    assert "黑方" in line_goal(seed, moves) and "将杀" in line_goal(seed, moves)
    guide = guide_step(seed, moves, 3)
    assert "完成将杀" in guide.purpose and guide.after.is_checkmate()
    assert teaching_context(seed, moves, 2, chess.WHITE)["本步及后续的子力核算"]["本人净子力变化"] == 3
    assert "被将杀" in guide_step(seed, moves, 2).purpose
    assert legal_line(seed, moves + ["a1a8"]) == moves


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_comparison_scores_always_use_root_mover(color):
    board = chess.Board()
    if not color:
        board.push_uci("e2e4")
    first, second = ("e7e5", "c7c5") if not color else ("e2e4", "d2d4")
    sign = 1 if color else -1
    result = Comparison(board.fen(), Position(100 * sign, None, [first], 18),
                        Position(-300 * sign, None, [second], 18))
    assert "明显更好" in result.description()
    assert result.context()["推荐评分"] == "+1.00"
    assert result.context()["对比评分"] == "-3.00"
    result.alternative.cp = 95 * sign
    assert result.description() == "两种走法都可考虑"
    assert Comparison.load(result.to_dict()).fen == board.fen()


def test_compare_engine_searches_same_root_and_exact_selected_moves(monkeypatch):
    board = chess.Board()
    preferred, other = board.parse_san("e4"), board.parse_san("d4")
    calls = []
    class Engine:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def search(engine, seed, seconds, roots):
        calls.append((seed.fen(), list(roots)))
        return Position(0, None, [roots[0].uci()], 18)
    monkeypatch.setattr("chessreview.engine.start_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr("chessreview.engine.analyse_position", search)
    result = compare_moves(board, preferred, other)
    assert calls == [(board.fen(), [preferred]), (board.fen(), [other])]
    assert result.alternative.pv == ["d2d4"]
    with pytest.raises(ValueError):
        compare_moves(board, preferred, preferred)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        compare_moves(board, preferred, other, cancel=cancelled)


def test_comparison_cache_roundtrip_and_reject_bad_lines(tmp_path):
    doc = parse_pgn("1. e4 e5 *", chess.BLACK)
    result = Comparison(doc.initial_fen, Position(20, None, ["e2e4", "e7e5"]), Position(15, None, ["d2d4", "d7d5"]))
    doc.comparisons["test"] = result.to_dict()
    doc.save(tmp_path / "teaching.chessreview")
    restored = Document.load(tmp_path / "teaching.chessreview")
    assert Comparison.load(restored.comparisons["test"]).alternative.pv == ["d2d4", "d7d5"]
    result.alternative.pv = ["e2e5"]
    with pytest.raises(ValueError):
        Comparison.load(result.to_dict())
