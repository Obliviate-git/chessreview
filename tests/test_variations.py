from pathlib import Path

import chess

from chessreview.core import (Document, context_for, line_context, material_facts,
                             parse_pgn, real_exchange)
from chessreview.variations import explanation_html, explanation_lines, validated_line


def sample_doc():
    return parse_pgn(Path("examples/sample.pgn").read_text(encoding="utf-8"), chess.BLACK)


def test_exact_move_13_prose_is_a_clickable_three_move_line():
    doc = sample_doc()
    board = doc.board(25)
    line = line_context(board, ["e7g5", "b2b4", "h5f4"], "continuation", "后续")
    text = "推荐13...Qg5，白方可能走b4，黑方再以...Nf4进攻。"
    body, lines = explanation_html(text, {"可演示线路": [line]})
    assert len(lines) == 1
    assert lines[0].moves == ["e7g5", "b2b4", "h5f4"]
    assert 'href="coach://line/0/3"' in body
    assert "演示推荐" in body


def test_tagged_reply_validates_and_truncates_at_the_requested_ply():
    line = line_context(chess.Board(), ["e2e4", "e7e5", "g1f3"], "recommend", "推荐")
    clean, lines = explanation_lines("抢占中心。[[line:recommend:2]]", {"可演示线路": [line]})
    assert clean == "抢占中心。"
    assert lines[0].moves == ["e2e4", "e7e5"]
    bad = {**line, "uci": ["e2e4", "e7e4"]}
    assert validated_line(bad, 2) is None
    assert explanation_lines("说明[[line:missing:3]]", {"可演示线路": [line]})[1] == []
    assert explanation_lines("说明[[line:recommend:", {})[0] == "说明"


def test_html_is_escaped_and_external_links_cannot_be_injected():
    body, lines = explanation_html('<img src="file:///secret">', {})
    assert "<img" not in body
    assert "&lt;img" in body
    assert not lines


def test_recapturing_a_pawn_includes_the_previous_capture():
    doc = sample_doc()
    facts = real_exchange(doc, 34)
    assert facts["交换走法"] == "17.fxg4 Bxg4"
    assert facts["本人吃掉总价值"] == facts["本人被吃总价值"] == 1
    assert facts["本人净子力变化"] == 0
    assert "等值换子" in facts["结论"]


def test_bishop_for_knight_is_equal_not_a_lost_bishop():
    board = sample_doc().board(43)
    facts = material_facts(board, ["e6d5", "c4d5"], chess.BLACK)
    assert facts["本人吃掉总价值"] == facts["本人被吃总价值"] == 3
    assert facts["本人净子力变化"] == 0
    assert "等值换子" in facts["结论"]


def test_cutoff_does_not_omit_an_immediate_recapture():
    board = sample_doc().board(43)
    complete = material_facts(board, ["e6d5", "c4d5"], chess.BLACK, limit=1)
    assert complete["本人净子力变化"] == 0
    partial = material_facts(board, ["e6d5"], chess.BLACK, limit=1)
    assert "cxd5" in partial["线路末尾可吃回"]
    assert "不能据此断言" in partial["结论"]


def test_en_passant_and_promotion_material_accounting():
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    facts = material_facts(board, ["e5d6"], chess.WHITE)
    assert facts["本人净子力变化"] == 1
    board = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    facts = material_facts(board, ["a7a8q"], chess.WHITE)
    assert facts["升变净增值"] == 8
