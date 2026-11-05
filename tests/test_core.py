import json
from pathlib import Path

import chess
import pytest

from chessreview.core import (Document, ImportErrorPGN, Position, Review, cache_key,
                             classify, context_for, material_snapshot, parse_pgn, pv_san, score_text, summary_context)


@pytest.fixture
def sample():
    return Path("examples/sample.pgn").read_text(encoding="utf-8")


def test_real_game_and_pasted_numbering(sample):
    original = parse_pgn(sample, chess.BLACK)
    pasted = sample.replace("\n9. Nc3", "\n2. Nc3").replace("\n35. Qxe5", "\n3. Qxe5").replace("\n41. Kf3", "\n4. Kf3")
    recovered = parse_pgn(pasted, chess.BLACK)
    assert recovered.moves == original.moves
    assert len(original.moves) == 86
    assert original.sans[-1] == "Qd2#"
    assert original.board(86).is_checkmate()
    assert original.board(86).outcome().winner == chess.BLACK
    assert recovered.original_pgn == pasted
    assert "41. Kf3" in recovered.pgn
    original.reviews[44] = Review(44, "严重失误", 500, Position(), Position(), "")
    evidence = context_for(original, 44, original.board(44))
    assert "吃掉马" in evidence["实战这一步的具体事实"][0]


@pytest.mark.parametrize("pgn", ["1. e5 *", "1. e4 e5 2. Qh8 *", "1. e4 e5 gibberish", "", "1. e4 e5\n\n2. Nf3 Nc6 *"])
def test_reject_invalid_or_truncated_input(pgn):
    with pytest.raises(ImportErrorPGN):
        parse_pgn(pgn, chess.WHITE)


def test_black_pov_and_mate_classification():
    assert score_text(Position(150), chess.BLACK) == "-1.50"
    assert score_text(Position(0, -3), chess.BLACK) == "3步内将杀"
    assert classify(Position(-20), Position(200), chess.BLACK, False)[0] == "失误"
    assert classify(Position(-50), Position(500), chess.BLACK, False)[0] == "严重失误"
    assert classify(Position(-100000, -3), Position(-100000, -5), chess.BLACK, False)[0] == "好棋"


def test_save_and_reload_analysis_chats_and_usage(tmp_path):
    doc = parse_pgn("1. e4 e5 2. Nf3 Nc6 *", chess.BLACK)
    doc.positions = [Position() for _ in doc.fens]
    doc.reviews[2] = Review(2, "好棋", 10, Position(10, None, ["e7e5"], 15), Position(20), "提示")
    doc.chats["one"] = [{"role": "assistant", "content": "这是解释。"}]
    doc.usage = [{"tokens": {"totalTokens": 99}}]
    path = tmp_path / "review.chessreview"
    doc.save(path)
    loaded = Document.load(path)
    assert loaded.moves == doc.moves
    assert loaded.reviews[2].before.pv == ["e7e5"]
    assert loaded.chats == doc.chats
    assert loaded.usage == doc.usage
    data = json.loads(path.read_text(encoding="utf-8"))
    data["moves"][0] = "d2d4"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="不一致"):
        Document.load(path)


def test_context_and_cache_do_not_mix_player_color_or_service():
    doc = parse_pgn("1. e4 e5 *", chess.BLACK)
    doc.reviews[2] = Review(2, "最佳", 0, Position(25, None, ["e7e5", "g1f3"], 16), Position(25, None, ["g1f3"], 16), "提示")
    context = context_for(doc, 2, doc.board(2))
    assert context["本人"] == "黑方"
    assert context["当前轮到"] == "白方"
    assert "兵e5" in context["棋子位置"]["黑方"]
    assert "1...e5" in context["推荐走法及后续"]
    args = (doc.board(2), 2, False)
    assert cache_key(*args, "codex", "luna") != cache_key(*args, "codex", "sol")
    assert cache_key(*args, "api", "same", "https://a") != cache_key(*args, "api", "same", "https://b")


def test_variation_notation_and_legality():
    board = chess.Board()
    assert pv_san(board, ["e2e4", "e7e5", "g1f3"]) == "1.e4 e5 2.Nf3"
    board.push_uci("e2e4")
    assert pv_san(board, ["e7e5", "g1f3"]) == "1...e5 2.Nf3"
    assert pv_san(board, ["a1a8"]) == ""


def test_white_move_context_does_not_assume_black_is_user():
    doc = parse_pgn("1. e4 e5 *", chess.BLACK)
    doc.reviews[1] = Review(1, "失误", 200, Position(100, None, ["d2d4"]), Position(-100, None, ["e7e5"]), "")
    context = context_for(doc, 1, doc.board(1))
    assert context["走棋方"] == context["分析方"] == context["本人"] == "白方"
    assert context["查看视角"] == "黑方"
    assert context["本人视角评分之前"] == "+1.00"
    assert context["本人视角评分之后"] == "-1.00"
    assert context["可演示线路"][0]["走法"] == "1.d4"
    before = context["刚走的棋"]
    doc.color = chess.WHITE
    assert context_for(doc, 1, doc.board(1))["刚走的棋"] == before
    summary = summary_context(doc)
    assert summary["评价统计"]["白方"]["失误"] == 1
    assert summary["评价统计"]["黑方"]["失误"] == 0


def test_cache_includes_viewing_side():
    args = (chess.Board(), 0, True, "codex", "luna")
    assert cache_key(*args, color=chess.WHITE) != cache_key(*args, color=chess.BLACK)


def test_material_tracks_captures_recaptures_and_undo():
    doc = parse_pgn("1. d4 d5 2. c4 dxc4 3. e3 e5 4. Bxc4 *", chess.WHITE)
    board = doc.board(4)
    before = board.fen()
    captured, balance = material_snapshot(board)
    assert captured[chess.BLACK] == [chess.Piece(chess.PAWN, chess.WHITE)]
    assert captured[chess.WHITE] == []
    assert balance == -1
    assert board.fen() == before and len(board.move_stack) == 4
    captured, balance = material_snapshot(doc.board(7))
    assert captured[chess.WHITE] == [chess.Piece(chess.PAWN, chess.BLACK)]
    assert balance == 0
    board.pop()
    assert material_snapshot(board) == ({chess.WHITE: [], chess.BLACK: []}, 0)


def test_material_en_passant_promotions_and_fen_start():
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    assert material_snapshot(board) == ({chess.WHITE: [], chess.BLACK: []}, 0)
    board.push_uci("e5d6")
    captured, balance = material_snapshot(board)
    assert captured[chess.WHITE] == [chess.Piece(chess.PAWN, chess.BLACK)]
    assert balance == 1
    board = chess.Board("1r2k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    board.push_uci("a7a8q")
    assert material_snapshot(board)[1] == 4
    board.push_uci("b8a8")
    captured, balance = material_snapshot(board)
    assert captured[chess.BLACK] == [chess.Piece(chess.QUEEN, chess.WHITE)]
    assert captured[chess.WHITE] == []
    assert balance == -5


def test_pgn_punctuation_and_numeric_annotations_survive_reload(tmp_path):
    doc = parse_pgn("1. e4! e5 $2 2. Nf3!! Nc6 $4 3. Bb5!? a6?! *", chess.WHITE)
    expected = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!"}
    assert doc.annotations == expected
    assert doc.sans == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
    path = tmp_path / "annotated.chessreview"
    doc.save(path)
    assert Document.load(path).annotations == expected
