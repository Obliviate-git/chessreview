from __future__ import annotations

import math
from dataclasses import dataclass

import chess
import chess.svg
from PySide6.QtCore import QByteArray, QEasingCurve, QPointF, QRectF, QSize, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QInputDialog, QSizePolicy, QWidget

from .core import GRADE_SYMBOLS, LABEL_COLORS
from .visuals import paint_grade


@dataclass
class PieceMotion:
    piece: chess.Piece
    source: int
    target: int
    start: QPointF
    end: QPointF


def adjacent_motion(before: chess.Board, after: chess.Board):
    """Return moving pieces and capture overlays for a single legal step or undo."""
    forward = False
    move = None
    if after.move_stack:
        candidate = after.peek()
        if candidate in before.legal_moves:
            probe = before.copy()
            probe.push(candidate)
            if probe.fen() == after.fen():
                move, forward = candidate, True
    if move is None and before.move_stack:
        probe = before.copy()
        candidate = probe.pop()
        if probe.fen() == after.fen():
            move = candidate
    if move is None:
        return [], {}, set()
    initial, final = (before, after) if forward else (after, before)
    pieces = [(initial.piece_at(move.from_square), move.from_square, move.to_square)]
    if initial.is_castling(move):
        rank = chess.square_rank(move.from_square)
        kingside = initial.is_kingside_castling(move)
        pieces.append((initial.piece_at(chess.square(7 if kingside else 0, rank)),
                       chess.square(7 if kingside else 0, rank), chess.square(5 if kingside else 3, rank)))
    if not forward:
        pieces = [(before.piece_at(target), target, source) for _, source, target in pieces]
    moving_sources = {source for _, source, _ in pieces}
    moving_targets = {target for _, _, target in pieces}
    captures = {square: piece for square, piece in before.piece_map().items()
                if square not in moving_sources and after.piece_at(square) != piece}
    restores = {square for square, piece in after.piece_map().items()
                if square not in moving_targets and before.piece_at(square) != piece}
    return pieces, captures, restores


class ChessBoard(QWidget):
    move_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = chess.Board()
        self.orientation = chess.WHITE
        self.interactive = False
        self.selected: int | None = None
        self.last_move: chess.Move | None = None
        self.arrow: chess.Move | None = None
        self.grade = ""
        self.annotation = ""
        self.teaching_arrows = []
        self.teaching_squares = {}
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(220)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.animate_frame)
        self.animation.finished.connect(self.finish_animation)
        self.animation_progress = 1.0
        self.motions: list[PieceMotion] = []
        self.capture_overlays = {}
        self.restore_squares = set()
        self.animation_orientation = self.orientation
        self.renderers = {}
        for color in (chess.WHITE, chess.BLACK):
            for kind in range(1, 7):
                piece = chess.Piece(kind, color)
                svg = chess.svg.piece(piece)
                if not color:
                    svg = svg.replace("#000000", "#363a3b").replace('fill="#000"', 'fill="#363a3b"')
                self.renderers[piece.symbol()] = QSvgRenderer(QByteArray(svg.encode()), self)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(640, 640)

    def geometry_board(self) -> tuple[float, float, float]:
        size = min(self.width(), self.height()) - 4
        return (self.width() - size) / 2, (self.height() - size) / 2, size / 8

    def square_rect(self, square: int) -> QRectF:
        x, y, side = self.geometry_board()
        file, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (file, 7 - rank) if self.orientation else (7 - file, rank)
        return QRectF(x + col * side, y + row * side, side, side)

    def cell_point(self, square):
        file, rank = chess.square_file(square), chess.square_rank(square)
        return QPointF(file, 7 - rank) if self.orientation else QPointF(7 - file, rank)

    def motion_point(self, motion):
        return motion.start + (motion.end - motion.start) * self.animation_progress

    def animate_frame(self, progress):
        self.animation_progress = float(progress)
        self.update()

    def finish_animation(self):
        self.motions.clear()
        self.capture_overlays.clear()
        self.restore_squares.clear()
        self.animation_progress = 1.0
        self.update()

    def stop_animation(self):
        self.animation.stop()
        self.finish_animation()

    def set_position(self, position: chess.Board, last_move=None, arrow=None, grade="", animate=True, annotation=""):
        changed = position.fen() != self.position.fen()
        flipped = self.animation_orientation != self.orientation
        if changed or flipped or not animate:
            prior_points = {(motion.target, motion.piece.symbol()): self.motion_point(motion)
                            for motion in self.motions} if not flipped else {}
            pieces, captures, restores = adjacent_motion(self.position, position) if animate and changed and not flipped else ([], {}, set())
            self.stop_animation()
            self.animation_orientation = self.orientation
            self.motions = [PieceMotion(piece, source, target,
                prior_points.get((source, piece.symbol()), self.cell_point(source)), self.cell_point(target))
                for piece, source, target in pieces if piece]
            self.capture_overlays = captures
            self.restore_squares = restores
        self.position = position.copy()
        self.last_move = last_move
        self.arrow = arrow
        self.grade = grade
        self.annotation = annotation
        self.selected = None
        if (changed or flipped) and self.motions:
            self.animation_progress = 0.0
            self.animation.start()
        self.update()

    def draw_piece(self, painter, piece, rect):
        pad = rect.width() * .10
        self.renderers[piece.symbol()].render(painter, rect.adjusted(pad, pad + 2, -pad, -pad + 2))

    def set_teaching_marks(self, arrows=(), squares=None):
        self.teaching_arrows = [(source, target, color) for source, target, color in arrows
                                if source in chess.SQUARES and target in chess.SQUARES and source != target]
        self.teaching_squares = dict(squares or {})
        self.update()

    def draw_arrow(self, painter, source, target, color):
        start, end = self.square_rect(source).center(), self.square_rect(target).center()
        side = self.square_rect(0).width()
        shade = QColor(color)
        shade.setAlpha(205)
        painter.setPen(QPen(shade, side * .095, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        tip = side * .29
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        painter.drawPolygon(QPolygonF([end,
            QPointF(end.x() - tip * math.cos(angle - .55), end.y() - tip * math.sin(angle - .55)),
            QPointF(end.x() - tip * math.cos(angle + .55), end.y() - tip * math.sin(angle + .55))]))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        moving_targets = {motion.target for motion in self.motions}
        for square in chess.SQUARES:
            rect = self.square_rect(square)
            light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            painter.fillRect(rect, QColor("#e9edcc" if light else "#779556"))
            if self.last_move and square in (self.last_move.from_square, self.last_move.to_square):
                painter.fillRect(rect, QColor(218, 200, 97, 110))
            if square == self.selected:
                painter.fillRect(rect, QColor(126, 164, 89, 160))
            if self.position.is_check() and self.position.king(self.position.turn) == square:
                painter.fillRect(rect, QColor(195, 66, 58, 130))
            if square in self.teaching_squares and not self.motions:
                shade = QColor(self.teaching_squares[square])
                shade.setAlpha(70)
                painter.fillRect(rect, shade)
            # Full square coordinates in the upper-left corner, away from the piece body.
            painter.setPen(QColor("#94a277" if light else "#b9cc9f"))
            painter.setFont(QFont("Segoe UI", max(7, int(rect.width() * .11))))
            painter.drawText(rect.adjusted(4, 2, -2, -2), Qt.AlignTop | Qt.AlignLeft,
                             chess.square_name(square).upper())
            piece = self.position.piece_at(square)
            if piece and square not in moving_targets:
                painter.save()
                if square in self.restore_squares:
                    painter.setOpacity(self.animation_progress)
                self.draw_piece(painter, piece, rect)
                painter.restore()
            if square in self.capture_overlays:
                painter.save()
                painter.setOpacity(min(1.0, max(0.0, (1 - self.animation_progress) * 4)))
                self.draw_piece(painter, self.capture_overlays[square], rect)
                painter.restore()
        x, y, side = self.geometry_board()
        for motion in self.motions:
            point = self.motion_point(motion)
            self.draw_piece(painter, motion.piece, QRectF(x + point.x() * side, y + point.y() * side, side, side))
        if self.selected is not None:
            for move in self.position.legal_moves:
                if move.from_square == self.selected:
                    rect = self.square_rect(move.to_square)
                    painter.setBrush(QColor(40, 69, 51, 95))
                    painter.setPen(Qt.NoPen)
                    painter.drawEllipse(rect.center(), rect.width() * .10, rect.width() * .10)
        if self.teaching_arrows and not self.motions:
            for source, target, color in self.teaching_arrows:
                self.draw_arrow(painter, source, target, color)
        elif self.arrow and not self.motions:
            start = self.square_rect(self.arrow.from_square).center()
            end = self.square_rect(self.arrow.to_square).center()
            side = self.square_rect(0).width()
            painter.setPen(QPen(QColor(89, 148, 69, 210), side * .105, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(start, end)
            angle = math.atan2(end.y() - start.y(), end.x() - start.x())
            tip = side * .30
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(89, 148, 69, 225))
            painter.drawPolygon(QPolygonF([end, QPointF(end.x() - tip * math.cos(angle - .55), end.y() - tip * math.sin(angle - .55)),
                                         QPointF(end.x() - tip * math.cos(angle + .55), end.y() - tip * math.sin(angle + .55))]))
        badge_rect = self.move_badge_rect()
        if not badge_rect.isEmpty():
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 65))
            painter.drawEllipse(badge_rect.translated(1, 2))
            paint_grade(painter, badge_rect, self.grade, self.annotation)
            painter.restore()

    def move_badge_rect(self):
        symbol = self.annotation or GRADE_SYMBOLS.get(self.grade, "")
        if symbol not in {"!", "!!", "?", "??"} or not self.last_move or self.motions:
            return QRectF()
        rect = self.square_rect(self.last_move.to_square)
        size = max(26, min(40, rect.width() * .40))
        x, y, side = self.geometry_board()
        left = max(x + 2, min(rect.right() - size * .86, x + side * 8 - size - 2))
        top = max(y + 2, min(rect.top() - size * .14, y + side * 8 - size - 2))
        return QRectF(left, top, size, size)

    def mousePressEvent(self, event):
        if not self.interactive or self.motions or event.button() != Qt.LeftButton:
            return
        x, y, side = self.geometry_board()
        col = int((event.position().x() - x) // side)
        row = int((event.position().y() - y) // side)
        if not 0 <= col < 8 or not 0 <= row < 8:
            return
        square = chess.square(col, 7 - row) if self.orientation else chess.square(7 - col, row)
        if self.selected is not None:
            candidates = [move for move in self.position.legal_moves if move.from_square == self.selected and move.to_square == square]
            if candidates:
                chosen = candidates[0]
                if any(move.promotion for move in candidates):
                    names = {chess.QUEEN: "后", chess.ROOK: "车", chess.BISHOP: "象", chess.KNIGHT: "马"}
                    name, ok = QInputDialog.getItem(self, "兵的升变", "选择升变棋子", list(names.values()), 0, False)
                    if not ok:
                        return
                    chosen = next(move for move in candidates if names.get(move.promotion) == name)
                self.selected = None
                self.move_requested.emit(chosen)
                return
        piece = self.position.piece_at(square)
        self.selected = square if piece and piece.color == self.position.turn else None
        self.update()


class EvaluationGraph(QWidget):
    seek = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scores: list[int] = []
        self.ply = 0
        self.color = chess.WHITE
        self.annotations: dict[int, str] = {}
        self.hover_ply: int | None = None
        self.setFixedHeight(72)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击跳转走法；上方为你的优势。")

    def point_at(self, ply):
        cp = self.scores[ply] if self.color else -self.scores[ply]
        middle = self.height() / 2
        value = math.tanh(cp / 600)
        return QPointF(ply / max(1, len(self.scores) - 1) * (self.width() - 12) + 6,
                       middle - value * (middle - 8))

    def index_at(self, x):
        return max(0, min(len(self.scores) - 1, round((x - 6) / max(1, self.width() - 12) * (len(self.scores) - 1))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#44433f"))
        painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        middle = self.height() / 2
        if len(self.scores) < 2:
            return
        points = [self.point_at(i) for i in range(len(self.scores))]
        area = QPainterPath(points[0])
        for point in points[1:]:
            area.lineTo(point)
        area.lineTo(points[-1].x(), self.height() - 3)
        area.lineTo(points[0].x(), self.height() - 3)
        area.closeSubpath()
        painter.fillPath(area, QColor("#f6f5f0"))
        painter.setPen(QPen(QColor("#89877f"), 1))
        painter.drawLine(6, int(middle), self.width() - 6, int(middle))
        painter.setPen(QPen(QColor("#ffffff"), 1.2))
        painter.drawPolyline(QPolygonF(points))
        selected = max(0, min(len(points) - 1, self.ply))
        painter.setPen(QPen(QColor("#b4b0a4"), 1, Qt.DashLine))
        painter.drawLine(QPointF(points[selected].x(), 3), QPointF(points[selected].x(), self.height() - 3))
        for ply, grade in self.annotations.items():
            if not 0 <= ply < len(points):
                continue
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(LABEL_COLORS.get(grade, "#81b64c")))
            painter.drawEllipse(points[ply], 4.5, 4.5)
        painter.setPen(QPen(QColor("#252522"), 2))
        painter.setBrush(QColor(LABEL_COLORS.get(self.annotations.get(selected), "#ffffff")))
        painter.drawEllipse(points[selected], 5.5, 5.5)
        if self.hover_ply is not None:
            painter.setPen(QPen(QColor("#aaa89f"), 1))
            point = points[self.hover_ply]
            painter.drawLine(QPointF(point.x(), 3), QPointF(point.x(), self.height() - 3))

    def mousePressEvent(self, event):
        if self.scores and event.button() == Qt.LeftButton:
            self.seek.emit(self.index_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if self.scores:
            self.hover_ply = self.index_at(event.position().x())
            ply = self.hover_ply
            cp = self.scores[ply] if self.color else -self.scores[ply]
            move = "起始局面" if ply == 0 else f"{(ply + 1) // 2}{'.' if ply % 2 else '...'}"
            self.setToolTip(f"{move}　{self.annotations.get(ply, '')}　{cp / 100:+.2f}")
            self.update()

    def leaveEvent(self, event):
        self.hover_ply = None
        self.update()
