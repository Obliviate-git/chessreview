from __future__ import annotations

import hashlib
import io
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import chess
import chess.pgn

PIECE_NAMES = {chess.PAWN: "兵", chess.KNIGHT: "马", chess.BISHOP: "象",
               chess.ROOK: "车", chess.QUEEN: "后", chess.KING: "王"}
PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
LABEL_COLORS = {"最佳": "#83b94f", "好棋": "#a5c96a", "可改进": "#efac55",
                "失误": "#ed765e", "严重失误": "#f04f43", "妙招": "#56b8d0", "关键走法": "#83b94f"}
GRADE_SYMBOLS = {"关键走法": "!", "妙招": "!!", "可改进": "?!", "失误": "?", "严重失误": "??"}
GRADING_VERSION = 2
ANNOTATION_COLORS = {"!": "#83b94f", "!!": "#56b8d0", "?": "#ed765e",
                     "??": "#f04f43", "!?": "#56b8d0", "?!": "#efac55"}
NAG_SYMBOLS = {3: "!!", 4: "??", 1: "!", 2: "?", 5: "!?", 6: "?!"}


class ImportErrorPGN(ValueError):
    pass


class StrictVisitor(chess.pgn.GameBuilder):
    def handle_error(self, error: Exception) -> None:
        raise ImportErrorPGN(f"走法无法解析：{error}") from error


@dataclass
class Position:
    cp: int = 0  # Always white's point of view, in centipawns.
    mate: int | None = None
    pv: list[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class Review:
    ply: int
    label: str
    loss: int
    before: Position
    played: Position
    hint: str
    evidence: str = ""


@dataclass
class Document:
    original_pgn: str
    pgn: str
    headers: dict[str, str]
    initial_fen: str
    moves: list[str]
    sans: list[str]
    fens: list[str]
    color: bool
    annotations: dict[int, str] = field(default_factory=dict)
    reviews: dict[int, Review] = field(default_factory=dict)
    positions: list[Position] = field(default_factory=list)
    chats: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    usage: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    engine_name: str = ""
    grading_version: int = 0
    comparisons: dict[str, dict] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.headers.get('White', '白方')} · {self.headers.get('Black', '黑方')}"

    def board(self, ply: int) -> chess.Board:
        board = chess.Board(self.initial_fen)
        for move in self.moves[:ply]:
            board.push_uci(move)
        return board

    def to_dict(self) -> dict:
        return {"format": "chessreview", "version": 1, **asdict(self)}

    def save(self, path: Path) -> None:
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> Document:
        if path.stat().st_size > 20_000_000:
            raise ValueError("复盘文件过大。")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("format") != "chessreview" or data.get("version") != 1:
            raise ValueError("这不是支持的复盘文件。")
        doc = parse_pgn(data["original_pgn"], bool(data["color"]))
        if doc.moves != data.get("moves") or doc.initial_fen != data.get("initial_fen"):
            raise ValueError("保存文件中的棋谱和分析局面不一致。")
        doc.positions = [Position(**p) for p in data.get("positions", [])]
        if doc.positions and len(doc.positions) != len(doc.moves) + 1:
            raise ValueError("保存的分析结果不完整，请重新分析。")
        for key, review in data.get("reviews", {}).items():
            ply = int(key)
            if not 1 <= ply <= len(doc.moves) or review["ply"] != ply:
                raise ValueError("保存的走法编号无效。")
            doc.reviews[ply] = Review(ply, review["label"], int(review["loss"]),
                                      Position(**review["before"]), Position(**review["played"]),
                                      review["hint"], review.get("evidence", ""))
        for key in ("chats", "usage", "elapsed", "engine_name", "grading_version", "comparisons"):
            setattr(doc, key, data.get(key, getattr(doc, key)))
        return doc


def parse_pgn(text: str, color: bool) -> Document:
    if not text.strip():
        raise ImportErrorPGN("请先粘贴PGN棋谱。")
    if len(text) > 2_000_000:
        raise ImportErrorPGN("棋谱文本过大，请一次导入一盘。")
    original = text
    text = text.strip().removeprefix("\ufeff")
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    # Preserve tags/comments; the PGN reader already ignores move numbers.
    # Do not silently skip arbitrary unknown tokens accepted by the forgiving reader.
    body = re.sub(r'^\s*\[[^\n]*\]\s*$', '', text, flags=re.M)
    body = re.sub(r'\{[^}]*\}|;[^\n]*', ' ', body, flags=re.S)
    remainder = re.sub(chess.pgn.MOVETEXT_REGEX, ' ', body)
    remainder = re.sub(r'\d+\.(?:\.\.)?|[\s.!?+#]+|\$\d+', ' ', remainder).strip()
    if remainder:
        raise ImportErrorPGN(f"棋谱里有无法识别的文本：{remainder[:80]}")
    stream = io.StringIO(text)
    try:
        game = chess.pgn.read_game(stream, Visitor=StrictVisitor)
    except ValueError as error:
        if isinstance(error, ImportErrorPGN):
            raise
        raise ImportErrorPGN(str(error)) from error
    if game is None:
        raise ImportErrorPGN("没有找到棋谱。")
    if stream.read().strip():
        raise ImportErrorPGN("检测到多盘棋或空行截断，请一次粘贴一盘完整棋谱。")
    board = game.board()
    if not board.is_valid():
        raise ImportErrorPGN("起始局面无效，请检查FEN。")
    moves, sans, fens = [], [], [board.fen()]
    initial = board.fen()
    annotations = {}
    for node in game.mainline():
        move = node.move
        if move not in board.legal_moves:
            raise ImportErrorPGN(f"第{board.fullmove_number}回合包含非法走法：{move.uci()}")
        sans.append(board.san(move))
        moves.append(move.uci())
        annotation = next((symbol for nag, symbol in NAG_SYMBOLS.items() if nag in node.nags), "")
        if annotation:
            annotations[len(moves)] = annotation
        board.push(move)
        fens.append(board.fen())
    if not moves:
        raise ImportErrorPGN("棋谱中没有实际走法。")
    if len(moves) > 600:
        raise ImportErrorPGN("这盘超过300回合，请拆分后导入。")
    pgn = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
    return Document(original, pgn, dict(game.headers), initial, moves, sans, fens, color,
                    annotations=annotations)


def pov_cp(position: Position, color: bool) -> int:
    return position.cp if color == chess.WHITE else -position.cp


def score_text(position: Position, color: bool = chess.WHITE) -> str:
    if position.mate is not None:
        mate = position.mate if color else -position.mate
        if mate == 0:
            return "将杀已完成"
        return f"{abs(mate)}步内{'将杀' if mate > 0 else '被将杀'}"
    return f"{pov_cp(position, color) / 100:+.2f}"


def winning_chance(cp: int) -> float:
    return 1 / (1 + math.exp(-max(-10000, min(10000, cp)) / 330))


def score_description(position: Position, color: bool) -> str:
    cp = pov_cp(position, color)
    if position.mate is not None:
        return "本人可将杀对方" if cp > 0 else "本人面临被将杀"
    if abs(cp) < 50:
        return "局面大致均衡"
    if cp >= 300:
        return "本人明显占优"
    if cp >= 50:
        return "本人略占优"
    if cp <= -300:
        return "本人明显处于劣势"
    return "本人略处下风"


def classify(before: Position, played: Position, color: bool, is_best: bool) -> tuple[str, int]:
    loss = max(0, pov_cp(before, color) - pov_cp(played, color))
    if is_best:
        return "最佳", 0
    chance_loss = winning_chance(pov_cp(before, color)) - winning_chance(pov_cp(played, color))
    if chance_loss >= 0.22 or (before.mate is None and pov_cp(played, color) <= -100000):
        return "严重失误", loss
    if chance_loss >= 0.10:
        return "失误", loss
    if chance_loss >= 0.035:
        return "可改进", loss
    return "好棋", loss


def pv_san(board: chess.Board, pv: list[str], limit: int = 10) -> str:
    board = board.copy()
    parts = []
    for uci in pv[:limit]:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        prefix = f"{board.fullmove_number}." if board.turn else (f"{board.fullmove_number}..." if not parts else "")
        parts.append(prefix + board.san(move))
        board.push(move)
    return " ".join(parts)


def describe_move(board: chess.Board, move: chess.Move) -> str:
    return board.san(move)


def material_snapshot(board: chess.Board) -> tuple[dict[bool, list[chess.Piece]], int]:
    """Actual captures in this board's history and surviving white material lead."""
    captured = {chess.WHITE: [], chess.BLACK: []}
    cursor = board.copy()
    while cursor.move_stack:
        move = cursor.pop()
        if cursor.is_capture(move):
            square = move.to_square
            if cursor.is_en_passant(move):
                square += -8 if cursor.turn else 8
            victim = cursor.piece_at(square)
            if victim:
                captured[cursor.turn].append(victim)
    for pieces in captured.values():
        pieces.sort(key=lambda piece: piece.piece_type)
    balance = sum(PIECE_VALUES.get(piece.piece_type, 0) * (1 if piece.color else -1)
                  for piece in board.piece_map().values())
    return captured, balance


def material_facts(board: chess.Board, pv: list[str], color: bool, limit: int = 10) -> dict:
    """Count actual captures, including en passant and promotion, without asking the model."""
    board = board.copy()
    gained, lost, promotion = 0, 0, 0
    events = []
    last_capture, last_square = False, None
    for index, uci in enumerate(pv[:limit + 8]):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        capture = board.is_capture(move)
        if index >= limit and not (last_capture and capture):
            break
        san = board.san(move)
        victim_square = move.to_square
        if board.is_en_passant(move):
            victim_square += -8 if board.turn else 8
        victim = board.piece_at(victim_square) if board.is_capture(move) else None
        if victim:
            value = PIECE_VALUES[victim.piece_type]
            if board.turn == color:
                gained += value
            else:
                lost += value
            events.append({"走法": san, "吃子方": "本人" if board.turn == color else "对方",
                           "被吃棋子": PIECE_NAMES[victim.piece_type], "子力值": value})
        if move.promotion:
            promotion += (PIECE_VALUES[move.promotion] - 1) * (1 if board.turn == color else -1)
        board.push(move)
        last_capture, last_square = capture, move.to_square
    net = gained - lost + promotion
    conclusion = "没有吃子"
    if events or promotion:
        conclusion = "等值换子，双方都没有净损失子力" if net == 0 and gained and lost else (
            f"本人净增加{net}点子力" if net > 0 else f"本人净损失{-net}点子力" if net < 0 else "净子力不变")
    recaptures = [board.san(move) for move in board.legal_moves
                 if last_capture and move.to_square == last_square and board.is_capture(move)]
    if recaptures:
        conclusion = "截至线路末尾：" + conclusion + "；仍有吃回可能，不能据此断言完整交换输赢"
    return {"范围": "仅核算给定线路，不代表唯一或必然应对", "吃子记录": events,
            "本人吃掉总价值": gained, "本人被吃总价值": lost,
            "升变净增值": promotion, "本人净子力变化": net,
            "线路末尾可吃回": recaptures, "结论": conclusion}


def real_exchange(doc: Document, ply: int, color: bool | None = None) -> dict:
    """Include the initiating capture when the reviewed move is a recapture."""
    start = ply - 1
    color = doc.color if color is None else color
    if not doc.board(start).is_capture(chess.Move.from_uci(doc.moves[start])):
        return material_facts(doc.board(start), [doc.moves[start]], color)
    while start > max(0, ply - 5):
        previous = doc.board(start - 1)
        if not previous.is_capture(chess.Move.from_uci(doc.moves[start - 1])):
            break
        start -= 1
    end = ply
    while end < min(len(doc.moves), start + 8):
        if not doc.board(end).is_capture(chess.Move.from_uci(doc.moves[end])):
            break
        end += 1
    facts = material_facts(doc.board(start), doc.moves[start:end], color)
    facts["范围"] = "实战中本步附近的连续吃子，包含此前吃子和随后吃回；不能当作引擎强制线路"
    facts["交换走法"] = pv_san(doc.board(start), doc.moves[start:end], 8)
    return facts


def line_context(board: chess.Board, pv: list[str], line_id: str, name: str) -> dict:
    legal = []
    cursor = board.copy()
    for uci in pv[:10]:
        move = chess.Move.from_uci(uci)
        if move not in cursor.legal_moves:
            break
        legal.append(uci)
        cursor.push(move)
    return {"id": line_id, "名称": name, "起始FEN": board.fen(), "uci": legal,
            "走法": pv_san(board, legal)}


def variation_facts(board: chess.Board, pv: list[str], limit: int = 6) -> list[str]:
    board = board.copy()
    facts = []
    for uci in pv[:limit]:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        fact = ("白方" if board.turn else "黑方") + describe_move(board, move)
        victim = board.piece_at(move.to_square)
        if victim:
            fact += "，吃掉" + PIECE_NAMES[victim.piece_type]
        elif board.is_en_passant(move):
            fact += "，吃过路兵"
        board.push(move)
        attacker = board.piece_at(move.to_square)
        if attacker:
            targets = [PIECE_NAMES[piece.piece_type] + chess.square_name(square)
                       for square in board.attacks(move.to_square)
                       if (piece := board.piece_at(square)) and piece.color != attacker.color and piece.piece_type != chess.PAWN]
            if targets:
                fact += "，该棋子直接攻击" + "、".join(targets)
        if board.is_checkmate():
            fact += "，将杀"
        elif board.is_check():
            fact += "，将军"
        facts.append(fact)
    return facts


def basic_hint(board: chess.Board, move: chess.Move, label: str, before: Position, played: Position) -> str:
    if label == "最佳":
        lead = "这步与当前引擎首选一致。"
    elif label == "好棋":
        lead = "这步保持了局面，和引擎首选的差距较小。"
    elif label == "关键走法":
        lead = "这步是维持局势的关键，其他走法会明显变差。"
    elif label == "妙招":
        lead = "这步有有效的弃子战术，引擎确认可以取得补偿。"
    else:
        lead = f"这步{'明显削弱' if label == '严重失误' else '削弱'}了{'白方' if board.turn else '黑方'}的局面。"
    after = board.copy()
    after.push(move)
    if after.is_checkmate():
        return "这步直接将杀，比赛结束。"
    if before.pv and before.pv[0] != move.uci():
        best = chess.Move.from_uci(before.pv[0])
        lead += f"更值得考虑：{describe_move(board, best)}。"
    if played.pv:
        response = chess.Move.from_uci(played.pv[0])
        if response in after.legal_moves:
            lead += f"对方最强应对是{describe_move(after, response)}。"
    return lead


def context_for(doc: Document, ply: int, board: chess.Board, trial: bool = False) -> dict:
    color = doc.board(ply - 1).turn if not trial and ply in doc.reviews else doc.color
    pieces = {"白方": [], "黑方": []}
    for square, piece in sorted(board.piece_map().items()):
        pieces["白方" if piece.color else "黑方"].append(PIECE_NAMES[piece.piece_type] + chess.square_name(square))
    context = {"本人": "白方" if color else "黑方", "分析方": "白方" if color else "黑方",
               "查看视角": "白方" if doc.color else "黑方", "模式": "试走" if trial else "实战复盘",
               "当前FEN": board.fen(), "当前轮到": "白方" if board.turn else "黑方",
               "棋子位置": pieces, "是否将军": board.is_check(), "棋局是否结束": board.is_game_over()}
    context["评分说明"] = "全部评分与子力核算是分析方视角，兼容字段‘本人’也指分析方，并不代表用户身份。正分=分析方优势，负分=分析方劣势。实战每一步分析当步走棋方；主变不是唯一合法应对。"
    if not trial and ply in doc.reviews:
        review = doc.reviews[ply]
        before = doc.board(ply - 1)
        context.update({"走棋方": "白方" if before.turn else "黑方",
                        "刚走的棋": describe_move(before, chess.Move.from_uci(doc.moves[ply - 1])),
                        "实战这一步的具体事实": variation_facts(before, [doc.moves[ply - 1]], 1),
                        "走法评价": review.label,
                        "特殊评价依据": review.evidence,
                        "本人视角评分之前": score_text(review.before, color),
                        "本人视角评分之后": score_text(review.played, color),
                        "走之前的局势": score_description(review.before, color),
                        "走之后的局势": score_description(review.played, color),
                        "推荐走法及后续": pv_san(before, review.before.pv),
                        "推荐变化的具体事实": variation_facts(before, review.before.pv),
                        "实战走法之后的最佳应对": pv_san(board, review.played.pv),
                        "实战之后变化的具体事实": variation_facts(board, review.played.pv),
                        "实战交换核算": real_exchange(doc, ply, color),
                        "实战这步及引擎后续的子力核算": material_facts(before, [doc.moves[ply - 1]] + review.played.pv, color),
                        "推荐线路的子力核算": material_facts(before, review.before.pv, color),
                        "分析深度": review.before.depth})
        context["可演示线路"] = [line_context(before, review.before.pv, "recommend", "推荐"),
                                  line_context(board, review.played.pv, "reply", "对方应对")]
    elif not trial and doc.positions:
        position = doc.positions[ply]
        context.update({"本人视角评分": score_text(position, doc.color), "最佳后续": pv_san(board, position.pv),
                        "后续变化的具体事实": variation_facts(board, position.pv),
                        "后续线路的子力核算": material_facts(board, position.pv, doc.color),
                        "可演示线路": [line_context(board, position.pv, "continuation", "后续") ]})
    return context


def cache_key(board: chess.Board, ply: int, trial: bool, provider: str, model: str, endpoint: str = "", color: bool | None = None) -> str:
    raw = json.dumps([board.fen(), ply, trial, provider, model, endpoint, color], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def summary_context(doc: Document) -> dict:
    sides = {"白方": [], "黑方": []}
    for review in doc.reviews.values():
        sides["白方" if doc.board(review.ply - 1).turn else "黑方"].append(review)
    important = [review for reviews in sides.values() for review in sorted(reviews, key=lambda r: r.loss, reverse=True)[:3]]
    return {"查看视角": "白方" if doc.color else "黑方", "模式": "双方复盘总结", "结果": doc.headers.get("Result", "*"),
            "走法数量": len(doc.reviews),
            "评价统计": {side: {label: sum(r.label == label for r in reviews) for label in LABEL_COLORS}
                         for side, reviews in sides.items()},
            "重点局面": [context_for(doc, r.ply, doc.board(r.ply)) for r in important]}
