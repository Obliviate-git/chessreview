from __future__ import annotations

import html
import re
from dataclasses import dataclass

import chess

MARKER = re.compile(r"\[\[line:([a-z]+):(\d{1,2})\]\]")
SAN_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:O-O-O|O-O|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])")


@dataclass
class CoachLine:
    name: str
    fen: str
    moves: list[str]


def validated_line(candidate: dict, count: int) -> CoachLine | None:
    try:
        board = chess.Board(candidate["起始FEN"])
        moves = candidate["uci"][:max(1, min(10, count))]
        if not board.is_valid() or not moves:
            return None
        for uci in moves:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                return None
            board.push(move)
        return CoachLine(candidate["名称"], candidate["起始FEN"], moves)
    except (ValueError, KeyError, TypeError):
        return None


def explanation_lines(text: str, context: dict) -> tuple[str, list[CoachLine]]:
    candidates = {line["id"]: line for line in context.get("可演示线路", [])}
    lines = []
    selected = set()
    for match in MARKER.finditer(text):
        line_id, count = match.groups()
        if line_id in candidates and line_id not in selected:
            line = validated_line(candidates[line_id], int(count))
            if line:
                lines.append(line)
                selected.add(line_id)
    clean = re.sub(r"\[\[line:[^\]]*(?:\]\]|$)", "", text).strip()
    # Also make untagged replies usable; match SAN to an engine-backed line.
    if not lines:
        mentioned = [match.group().rstrip("+#") for match in SAN_TOKEN.finditer(clean)]
        for candidate in candidates.values():
            line = validated_line(candidate, 6)
            if not line:
                continue
            board = chess.Board(line.fen)
            sans = []
            for uci in line.moves:
                sans.append(board.san(chess.Move.from_uci(uci)).rstrip("+#"))
                board.push_uci(uci)
            if not sans or sans[0] not in mentioned:
                continue
            cursor = mentioned.index(sans[0])
            count = 1
            for san in sans[1:]:
                try:
                    cursor = mentioned.index(san, cursor + 1)
                except ValueError:
                    break
                count += 1
            # Show a useful continuation even if prose mentions only its first move.
            line.moves = line.moves[:max(count, min(3, len(line.moves)))]
            lines.append(line)
    return clean, lines[:2]


def explanation_html(text: str, context: dict, start_index: int = 0, theme: str = "dark") -> tuple[str, list[CoachLine]]:
    clean, lines = explanation_lines(text, context)
    body = html.escape(clean).replace("\n", "<br>")
    link_color = "#527d2d" if theme == "light" else "#b5d77b"
    link_background = "#e9f0df" if theme == "light" else "#404a33"
    for offset, line in enumerate(lines, start_index):
        board = chess.Board(line.fen)
        links = []
        for step, uci in enumerate(line.moves, 1):
            prefix = f"{board.fullmove_number}." if board.turn else (f"{board.fullmove_number}..." if step == 1 else "")
            san = prefix + board.san(chess.Move.from_uci(uci))
            links.append(f'<a href="coach://line/{offset}/{step}" style="color:{link_color};text-decoration:none">{html.escape(san)}</a>')
            board.push_uci(uci)
        body += (f'<p style="margin-top:9px;margin-bottom:0;color:#a9a7a2">'
                 f'<a href="coach://line/{offset}/0" style="color:{link_color};text-decoration:none;font-weight:bold;background-color:{link_background};">演示{html.escape("推荐" if line.name == "后续" else line.name)}</a>'
                 f'　{" &nbsp; ".join(links)}</p>')
    return body, lines
