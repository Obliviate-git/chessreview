from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import chess

from .core import (PIECE_NAMES, Position, line_context, material_facts, pov_cp,
                   pv_san, score_text, variation_facts, winning_chance)

TEACHING_VERSION = "teaching-v1"
GREEN = "#6ba94f"
ORANGE = "#e59a47"


def legal_line(seed: chess.Board, moves: list[str], limit=14) -> list[str]:
    board, result = seed.copy(), []
    for uci in moves[:limit]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        result.append(uci)
        board.push(move)
    return result


def branch_key(seed: chess.Board, moves: list[str], step: int) -> str:
    raw = json.dumps([TEACHING_VERSION, seed.fen(), moves, step])
    return hashlib.sha256(raw.encode()).hexdigest()


def line_goal(seed: chess.Board, moves: list[str]) -> str:
    moves = legal_line(seed, moves)
    board = seed.copy()
    checks = 0
    for uci in moves:
        board.push_uci(uci)
        checks += board.is_check()
    if board.is_checkmate():
        return ("白方" if not board.turn else "黑方") + "沿这条线路完成将杀。"
    exchange = material_facts(seed, moves, seed.turn)
    if exchange["本人净子力变化"] > 0:
        return f"这条线路涉及争取子力，当前展示的变化净增{exchange['本人净子力变化']}分。"
    if checks:
        return "这条线路围绕将军与应将展开，留意王的安全和进攻节奏。"
    if exchange["吃子记录"]:
        return "这条线路包含交换，留意交换后的棋子位置与控制格。"
    return "留意每步对棋子活动、中心控制和对方威胁的影响。"


@dataclass
class GuideStep:
    index: int
    san: str
    color: bool
    before: chess.Board
    after: chess.Board
    purpose: str
    response: str
    facts: list[str]
    arrows: list[tuple[int, int, str]]
    squares: dict[int, str]


def guide_step(seed: chess.Board, moves: list[str], step: int) -> GuideStep | None:
    moves = legal_line(seed, moves)
    if not 1 <= step <= len(moves):
        return None
    before = seed.copy()
    for uci in moves[:step - 1]:
        before.push_uci(uci)
    move = chess.Move.from_uci(moves[step - 1])
    color, san = before.turn, before.san(move)
    piece = before.piece_at(move.from_square)
    after = before.copy()
    after.push(move)
    targets = [sq for sq in after.attacks(move.to_square)
               if (target := after.piece_at(sq)) and target.color != color
               and target.piece_type not in (chess.PAWN, chess.KING)]
    targets.sort(key=lambda sq: after.piece_at(sq).piece_type, reverse=True)
    names = [PIECE_NAMES[after.piece_at(sq).piece_type] + chess.square_name(sq) for sq in targets[:2]]
    arrows, squares = [], {move.to_square: GREEN}
    if after.is_check():
        king = after.king(after.turn)
        for checker in list(after.checkers())[:1]:
            arrows.append((checker, king, GREEN))
            squares[king] = ORANGE
    elif targets:
        arrows.append((move.to_square, targets[0], GREEN))
        squares[targets[0]] = GREEN
    else:
        arrows.append((move.from_square, move.to_square, GREEN))
    response = ""
    if step < len(moves):
        reply = chess.Move.from_uci(moves[step])
        response = "引擎后续：" + after.san(reply)
        arrows.append((reply.from_square, reply.to_square, ORANGE))
    facts = variation_facts(before, moves[step - 1:], 6)
    exchange = material_facts(before, moves[step - 1:], color, limit=4)
    if after.is_checkmate():
        purpose = "完成将杀，对方的王已没有合法应对。"
    elif move.promotion:
        purpose = "把兵升变成" + PIECE_NAMES[move.promotion] + "，增强子力与活动能力。"
        if after.is_check():
            purpose += "同时将军，让对方先处理王的安全。"
    elif after.is_check():
        purpose = "用将军争取主动，对方必须先处理王的安全。"
        if names:
            purpose += "同时对" + "、".join(names) + "施压。"
        if before.is_capture(move) and exchange["本人吃掉总价值"] == exchange["本人被吃总价值"] and exchange["本人被吃总价值"]:
            purpose += "沿后续进行等值交换。"
    elif before.is_castling(move):
        purpose = "把王移到侧翼，并让车更容易参与后续行动。"
    elif before.is_capture(move):
        if exchange["本人吃掉总价值"] == exchange["本人被吃总价值"] and exchange["本人被吃总价值"]:
            purpose = "沿这条线路进行等值交换，留意交换后的棋子位置。"
        else:
            victim = before.piece_at(move.to_square)
            victim_name = "兵" if before.is_en_passant(move) else PIECE_NAMES[victim.piece_type]
            purpose = "吃掉对方的" + victim_name + "，再结合后续吃回核算交换结果。"
    elif names:
        purpose = "对" + "、".join(names) + "施压，能否获利还要看对方的应对。"
    elif piece.piece_type == chess.PAWN and chess.square_rank(move.to_square) == (6 if color else 1):
        purpose = "把兵推进到接近升变的位置，留意对方是否有更快的威胁。"
    else:
        centers = [sq for sq in (chess.D4, chess.E4, chess.D5, chess.E5) if sq in after.attacks(move.to_square)]
        if centers:
            purpose = "加强对" + "、".join(chess.square_name(sq) for sq in centers[:2]) + "的控制。"
        elif piece.piece_type in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(move.from_square) in (0, 7):
            purpose = "出动轻子，增加棋子参与局面的机会。"
        else:
            purpose = "调整棋子位置；更具体的计划可以点“为什么”请教练解释。"
    ending = after.copy()
    for uci in moves[step:]:
        ending.push_uci(uci)
    if ending.is_checkmate() and ending.turn == color and not after.is_checkmate():
        purpose += "但沿这条后续仍会被将杀，留意王的安全。"
    return GuideStep(step, san, color, before, after, purpose, response, facts, arrows[:2], squares)


def teaching_context(seed: chess.Board, moves: list[str], step: int, view_color: bool) -> dict:
    moves = legal_line(seed, moves)
    guide = guide_step(seed, moves, step)
    board = guide.after if guide else seed.copy()
    color = guide.color if guide else seed.turn
    context = {"模式": "逐步讲解", "分析方": "白方" if color else "黑方",
               "走棋方": "白方" if color else "黑方", "本人": "白方" if color else "黑方",
               "查看视角": "白方" if view_color else "黑方", "当前FEN": board.fen(),
               "当前轮到": "白方" if board.turn else "黑方", "线路起始FEN": seed.fen(),
               "完整推荐线路": pv_san(seed, moves, 14), "线路目标依据": line_goal(seed, moves),
               "线路步数": step, "线路总步数": len(moves),
               "关系说明": "箭头表示棋盘几何攻击或引擎线路中的走法，不保证可以赢子；主变不是唯一应对。",
               "可演示线路": [line_context(board, moves[step:], "continuation", "后续")]}
    if guide:
        context.update({"这一步": guide.san, "走之前FEN": guide.before.fen(),
                        "走这一步之前的线路": pv_san(seed, moves[:step - 1], 14),
                        "本步目的的基础说明": guide.purpose,
                        "本步及后续的具体事实": guide.facts,
                        "本步及后续的子力核算": material_facts(guide.before, moves[step - 1:], color)})
    else:
        context["线路事实"] = variation_facts(seed, moves, 8)
        context["线路子力核算"] = material_facts(seed, moves, color)
    return context


@dataclass
class Comparison:
    fen: str
    recommended: Position
    alternative: Position

    @classmethod
    def load(cls, data):
        result = cls(data["fen"], Position(**data["recommended"]), Position(**data["alternative"]))
        seed = chess.Board(result.fen)
        if not seed.is_valid():
            raise ValueError("无效对比局面。")
        for position in (result.recommended, result.alternative):
            if not position.pv or legal_line(seed, position.pv, len(position.pv)) != position.pv:
                raise ValueError("对比线路包含非法走法。")
        return result

    def to_dict(self):
        return asdict(self)

    def description(self):
        color = chess.Board(self.fen).turn
        best, other = pov_cp(self.recommended, color), pov_cp(self.alternative, color)
        difference = winning_chance(best) - winning_chance(other)
        if self.recommended.mate is not None and self.alternative.mate is not None:
            if best < 0 and other < 0:
                return "两种走法都面临被将杀"
            if best > 0 and other > 0:
                return "两种走法都能将杀"
        if abs(difference) < .035 and (abs(best - other) <= 50 or self.recommended.mate and self.alternative.mate):
            return "两种走法都可考虑"
        if difference >= .10:
            return "推荐走法明显更好"
        if difference <= -.035:
            return "本次搜索中，对比走法更好"
        return "推荐走法略好" if difference > 0 else "两种走法差距较小"

    def context(self):
        board = chess.Board(self.fen)
        color = board.turn
        return {"对比起始FEN": self.fen, "对比评分视角": "白方" if color else "黑方",
                "对比结论": self.description(),
                "推荐走法及后续": pv_san(board, self.recommended.pv),
                "对比走法及后续": pv_san(board, self.alternative.pv),
                "推荐评分": score_text(self.recommended, color),
                "对比评分": score_text(self.alternative, color),
                "推荐线路事实": variation_facts(board, self.recommended.pv),
                "对比线路事实": variation_facts(board, self.alternative.pv),
                "推荐子力核算": material_facts(board, self.recommended.pv, color),
                "对比子力核算": material_facts(board, self.alternative.pv, color),
                "搜索深度": [self.recommended.depth, self.alternative.depth],
                "对比说明": "两条线路来自同一局面的独立限时引擎搜索，不是实战已发生的走法；评分接近时不要说唯一。"}


def comparison_key(seed, recommended, alternative, custom=""):
    raw = json.dumps([TEACHING_VERSION, seed.fen(), recommended.uci(), alternative.uci(), custom])
    return hashlib.sha256(raw.encode()).hexdigest()
