from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

import chess
import chess.engine

from .core import (GRADING_VERSION, PIECE_VALUES, Document, Position, Review,
                   basic_hint, classify, material_snapshot, pov_cp, score_text, winning_chance)


class PositiveBudget:
    """Bound extra verification independently of the main position analysis."""
    def __init__(self, deadline, cancel=None):
        self.remaining = 20.0
        self.deadline = deadline
        self.cancel = cancel

    def analyse(self, engine, board, roots, seconds=.35):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError("分析已取消。")
        seconds = min(seconds, self.remaining, self.deadline - time.monotonic())
        if seconds < .20:
            return None
        started = time.monotonic()
        try:
            return analyse_position(engine, board, seconds, roots)
        finally:
            self.remaining -= time.monotonic() - started


def sacrifice_reply(board, move, pv):
    """Require a real net sacrifice, not a normal capture/recapture sequence."""
    piece = board.piece_at(move.from_square)
    if not piece or PIECE_VALUES[piece.piece_type] < 3 or len(pv) < 2:
        return None
    color = board.turn
    initial = material_snapshot(board)[1] * (1 if color else -1)
    cursor = board.copy()
    cursor.push(move)
    reply = chess.Move.from_uci(pv[0])
    if reply not in cursor.legal_moves or not cursor.is_capture(reply) or reply.to_square != move.to_square:
        return None
    cursor.push(reply)
    if initial - material_snapshot(cursor)[1] * (1 if color else -1) < 2:
        return None
    # An ordinary exchange completed a few moves later is not a sacrifice.
    for uci in pv[1:5]:
        response = chess.Move.from_uci(uci)
        if response not in cursor.legal_moves:
            return None
        cursor.push(response)
        if initial - material_snapshot(cursor)[1] * (1 if color else -1) < 2:
            return None
    if len(pv) < 5 and not cursor.is_game_over(claim_draw=False):
        return None  # A truncated PV cannot establish that the exchange is complete.
    return reply


def exceptional_review(engine, board, move, review, budget):
    """Conservative positive marks; leave ordinary best/good grades unchanged."""
    if review.label not in {"最佳", "好棋"} or review.before.depth < 16 or board.is_check():
        return review
    if review.label == "好棋" and (review.loss > 30 or not sacrifice_reply(board, move, review.played.pv)):
        return review  # Equivalent mating sacrifices need not be the engine's first choice.
    roots = [other for other in board.legal_moves if other != move]
    if len(roots) < 2:  # Forced moves need no celebratory mark.
        return review
    color = board.turn
    prior = pov_cp(review.before, color)
    if prior < -100 or (review.before.mate is None and prior > 500):
        return review
    after = board.copy()
    after.push(move)
    if after.is_game_over(claim_draw=False):
        return review
    verified = budget.analyse(engine, board, [move], .30)
    if verified is None or verified.depth < 16 or not verified.pv or verified.pv[0] != move.uci():
        return review
    best = pov_cp(verified, color)
    # Abstain when the fresh search disagrees substantially with the original.
    if best < -50 or abs(best - prior) > 100:
        return review
    reply = sacrifice_reply(board, move, verified.pv[1:])
    if reply and (best <= 300 or verified.mate is not None):
        capture = budget.analyse(engine, after, [reply])
        if (capture is not None and capture.depth >= 16 and capture.pv
                and capture.pv[0] == reply.uci() and pov_cp(capture, color) >= -50
                and best - pov_cp(capture, color) <= 50):
            evidence = f"引擎主变包含净弃子至少2分；复核对方吃子后评分{score_text(capture, color)}，有足够补偿。"
            return replace(review, label="妙招", evidence=evidence,
                           hint=basic_hint(board, move, "妙招", review.before, review.played) + evidence)
    # Simple recaptures and already-decided positions are not critical finds.
    if review.label != "最佳":
        return review
    recapture = board.is_capture(move) and board.move_stack and move.to_square == board.peek().to_square
    if recapture:
        return review
    alternative = budget.analyse(engine, board, roots)
    if alternative is None or alternative.depth < 16:
        return review
    if not alternative.pv or alternative.pv[0] not in {other.uci() for other in roots}:
        return review
    other = pov_cp(alternative, color)
    if best - other < 150 or winning_chance(best) - winning_chance(other) < .12:
        return review
    difference = "相差至少1.50分。"
    if verified.mate is not None or alternative.mate is not None:
        difference = "本步保住了强制将杀。" if verified.mate is not None else "本步避免了被将杀。"
    evidence = (f"复核本步评分{score_text(verified, color)}，其余走法中最高评分"
                f"{score_text(alternative, color)}；" + difference)
    return replace(review, label="关键走法", evidence=evidence,
                   hint=basic_hint(board, move, "关键走法", review.before, review.played) + evidence)


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def engine_path(custom: str = "") -> Path:
    if custom:
        path = Path(custom)
        if not path.is_file():
            raise FileNotFoundError("设置中的棋类引擎路径不存在。")
        return path
    for root in (resource_root() / "engine", Path(sys.executable).parent / "engine"):
        paths = list(root.rglob("stockfish*.exe")) if root.exists() else []
        if paths:
            return paths[0]
    raise FileNotFoundError("没有找到Stockfish，请在设置中选择引擎程序。")


def start_engine(custom: str = "", threads: int | None = None) -> chess.engine.SimpleEngine:
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    engine = chess.engine.SimpleEngine.popen_uci(str(engine_path(custom)), timeout=15, **kwargs)
    engine.configure({"Threads": threads or min(6, max(1, (os.cpu_count() or 4) // 2)), "Hash": 256})
    return engine


def position_from_info(info: dict) -> Position:
    score = info["score"].white()
    return Position(score.score(mate_score=100000) or 0, score.mate(),
                    [move.uci() for move in info.get("pv", [])], info.get("depth", 0))


def terminal_position(board: chess.Board) -> Position:
    if board.is_checkmate():
        return Position(-100000 if board.turn else 100000, 0, [], 0)
    return Position()


def analyse_position(engine: chess.engine.SimpleEngine, board: chess.Board, seconds: float,
                     root_moves: list[chess.Move] | None = None) -> Position:
    if board.is_game_over(claim_draw=False):
        return terminal_position(board)
    info = engine.analyse(board, chess.engine.Limit(time=seconds), root_moves=root_moves)
    return position_from_info(info)


def analyse_move(engine, board, move, before, after_position, seconds, ply):
    """Grade a move from the mover's side, independent of the viewing side."""
    is_best = bool(before.pv and before.pv[0] == move.uci())
    if is_best:
        played = Position(before.cp, before.mate, before.pv[1:], before.depth)
    else:
        forced = analyse_position(engine, board, seconds, [move])
        played = Position(forced.cp, forced.mate, forced.pv[1:], forced.depth)
    after = board.copy()
    after.push(move)
    if after.is_game_over(claim_draw=False):
        played = after_position
    label, loss = classify(before, played, board.turn, is_best)
    return Review(ply, label, loss, before, played,
                  basic_hint(board, move, label, before, played))


def analyse_game(doc: Document, custom: str = "", progress: Callable = lambda *args: None,
                 cancel: threading.Event | None = None) -> Document:
    started = time.monotonic()
    engine = start_engine(custom)
    positions: list[Position] = []
    reviews: dict[int, Review] = {}
    boards = [doc.board(ply) for ply in range(len(doc.moves) + 1)]
    all_plies = range(1, len(doc.moves) + 1)
    total_work = len(boards) + 2 * len(doc.moves)
    unit = min(0.70, 90 / (len(boards) + 0.55 * len(doc.moves)))
    completed = 0
    try:
        for board in boards:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("分析已取消。")
            positions.append(analyse_position(engine, board, unit))
            completed += 1
            progress(completed, total_work, time.monotonic() - started)
        for ply in all_plies:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("分析已取消。")
            board = boards[ply - 1]
            move = chess.Move.from_uci(doc.moves[ply - 1])
            before = positions[ply - 1]
            reviews[ply] = analyse_move(engine, board, move, before, positions[ply], unit * 0.55, ply)
            completed += 1
            progress(completed, total_work, time.monotonic() - started)
        budget = PositiveBudget(started + 110, cancel)
        for ply in all_plies:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("分析已取消。")
            reviews[ply] = exceptional_review(engine, boards[ply - 1],
                chess.Move.from_uci(doc.moves[ply - 1]), reviews[ply], budget)
            completed += 1
            progress(completed, total_work, time.monotonic() - started)
        # Publish only a complete analysis. Cancellation leaves the previous review intact.
        doc.positions = positions
        doc.reviews = reviews
        doc.elapsed = time.monotonic() - started
        doc.engine_name = engine.id.get("name", "Stockfish")
        doc.grading_version = GRADING_VERSION
        doc.chats.clear()  # Cached explanations depend on these exact analysis results.
        return doc
    finally:
        engine.quit()


def complete_reviews(doc: Document, custom: str = "", progress: Callable = lambda *args: None,
                     cancel: threading.Event | None = None) -> Document:
    """Upgrade missing sides and positive marks, preserving existing positions/chats."""
    if len(doc.positions) != len(doc.moves) + 1:
        return analyse_game(doc, custom, progress, cancel)
    missing = [ply for ply in range(1, len(doc.moves) + 1) if ply not in doc.reviews]
    upgrade_marks = doc.grading_version < GRADING_VERSION
    if not missing and not upgrade_marks:
        return doc
    started = time.monotonic()
    engine = start_engine(custom)
    reviews = dict(doc.reviews)
    try:
        seconds = min(.385, 90 / max(1, len(missing)))
        marks = list(range(1, len(doc.moves) + 1)) if upgrade_marks else missing
        total = len(missing) + len(marks)
        for completed, ply in enumerate(missing, 1):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("分析已取消。")
            board = doc.board(ply - 1)
            reviews[ply] = analyse_move(engine, board, chess.Move.from_uci(doc.moves[ply - 1]),
                doc.positions[ply - 1], doc.positions[ply], seconds, ply)
            progress(completed, total, time.monotonic() - started)
        budget = PositiveBudget(started + 110, cancel)
        for completed, ply in enumerate(marks, len(missing) + 1):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("分析已取消。")
            reviews[ply] = exceptional_review(engine, doc.board(ply - 1),
                chess.Move.from_uci(doc.moves[ply - 1]), reviews[ply], budget)
            progress(completed, total, time.monotonic() - started)
        doc.reviews = reviews
        doc.grading_version = GRADING_VERSION
        doc.elapsed += time.monotonic() - started
        return doc
    finally:
        engine.quit()


def analyse_trial(board: chess.Board, color: bool, custom: str = "") -> tuple[chess.Board, Position, str | None]:
    with start_engine(custom, threads=2) as engine:
        response = None
        result = board.copy()
        if not result.is_game_over() and result.turn != color:
            info = analyse_position(engine, result, 0.7)
            if info.pv:
                response = info.pv[0]
                result.push_uci(response)
        position = analyse_position(engine, result, 0.6)
        return result, position, response


def compare_moves(board, recommended, alternative, custom="", cancel=None):
    from .teaching import Comparison, legal_line
    if recommended not in board.legal_moves or alternative not in board.legal_moves or recommended == alternative:
        raise ValueError("请选择与推荐不同的合法走法。")
    results = []
    with start_engine(custom, threads=2) as engine:
        for move in (recommended, alternative):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("对比已取消。")
            position = analyse_position(engine, board, .65, [move])
            position.pv = legal_line(board, position.pv, 10)
            if not position.pv or position.pv[0] != move.uci():
                raise ValueError("引擎没有返回这一步的有效后续，请重试。")
            results.append(position)
    return Comparison(board.fen(), *results)
