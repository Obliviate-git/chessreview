"""Shared vector review marks and compact move-list rendering."""
from functools import lru_cache
import math

import chess
import chess.svg
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStyledItemDelegate, QStyle, QWidget

from .core import ANNOTATION_COLORS, GRADE_SYMBOLS, LABEL_COLORS, PIECE_NAMES

MOVE_META = Qt.UserRole + 1


class CapturedPieces(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pieces = []
        self.balance = 0
        self.color = chess.WHITE
        self.renderers = {}
        self.balance_font = QFont("Segoe UI")
        self.balance_font.setPixelSize(16)
        self.balance_font.setBold(True)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setFixedHeight(26)

    def set_material(self, pieces, balance, color):
        self.pieces = list(pieces)
        self.balance = balance
        self.color = color
        side = "白方" if color else "黑方"
        counts = [f"{PIECE_NAMES[kind]}×{sum(p.piece_type == kind for p in self.pieces)}"
                  for kind in range(1, 6) if any(p.piece_type == kind for p in self.pieces)]
        captures = "已吃：" + "、".join(counts) if counts else "尚未吃子"
        status = (f"{side}子力领先{balance}分" if balance > 0 else
                  f"{side}子力落后{-balance}分" if balance < 0 else "子力均衡")
        self.setToolTip(captures + "\n" + status)
        self.setAccessibleName(side + "，" + captures + "，" + status)
        self.updateGeometry()
        self.update()

    def icon_positions(self):
        x, previous = 0, None
        positions = []
        for piece in self.pieces:
            if previous is not None:
                x += 11 if piece.piece_type == previous else 24
            positions.append((piece, x))
            previous = piece.piece_type
        return positions

    def sizeHint(self):
        positions = self.icon_positions()
        width = positions[-1][1] + 22 if positions else 0
        if self.balance > 0:
            width += (7 if positions else 0) + QFontMetrics(self.balance_font).horizontalAdvance(f"+{self.balance}") + 2
        return QSize(width, 26)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        positions = self.icon_positions()
        for piece, x in positions:
            key = piece.symbol()
            if key not in self.renderers:
                svg = chess.svg.piece(piece)
                if not piece.color:
                    svg = svg.replace("#000000", "#aaa99e").replace('fill="#000"', 'fill="#aaa99e"')
                self.renderers[key] = QSvgRenderer(QByteArray(svg.encode()), self)
            self.renderers[key].render(painter, QRectF(x, 2, 22, 22))
        if self.balance > 0:
            painter.setPen(QColor("#94c85a"))
            painter.setFont(self.balance_font)
            x = positions[-1][1] + 29 if positions else 0
            painter.drawText(QRectF(x, 0, max(0, self.width() - x), 26), Qt.AlignVCenter, f"+{self.balance}")


def paint_grade(painter, rect, grade, annotation=""):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(ANNOTATION_COLORS.get(annotation, LABEL_COLORS.get(grade, "#81b64c"))))
    painter.drawEllipse(rect)
    painter.setPen(QPen(QColor("#ffffff"), max(1.5, rect.width() * .09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    center, radius = rect.center(), rect.width() * .34
    symbol = annotation or GRADE_SYMBOLS.get(grade, "")
    if grade == "最佳" and not symbol:
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            r = radius if i % 2 == 0 else radius * .46
            points.append(QPointF(center.x() + r * math.cos(angle), center.y() + r * math.sin(angle)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPolygon(QPolygonF(points))
    elif grade == "好棋" and not symbol:
        painter.drawPolyline(QPolygonF([
            QPointF(center.x() - radius * .7, center.y()),
            QPointF(center.x() - radius * .15, center.y() + radius * .5),
            QPointF(center.x() + radius * .8, center.y() - radius * .55)]))
    else:
        text = symbol or "?"
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setPixelSize(round(rect.height() * (.54 if len(text) > 1 else .68)))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, text)
    painter.restore()


@lru_cache(maxsize=16)
def grade_icon(grade, size=28):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    paint_grade(painter, QRectF(0, 0, size * 2, size * 2), grade)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


@lru_cache(maxsize=32)
def action_icon(name, color="#d5d3d0"):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2, 2)
    p.setPen(QPen(QColor(color), 2.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name in ("prev", "next", "start", "end"):
        if name in ("next", "end"):
            p.translate(32, 0)
            p.scale(-1, 1)
        p.drawPolyline(QPolygonF([QPointF(20, 7), QPointF(11, 16), QPointF(20, 25)]))
        if name in ("start", "end"):
            p.drawLine(QPointF(6, 7), QPointF(6, 25))
    elif name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF([QPointF(11, 6), QPointF(25, 16), QPointF(11, 26)]))
    elif name == "pause":
        p.drawLine(QPointF(12, 7), QPointF(12, 25))
        p.drawLine(QPointF(21, 7), QPointF(21, 25))
    elif name == "flip":
        p.drawPolyline(QPolygonF([QPointF(7, 11), QPointF(25, 11), QPointF(21, 7)]))
        p.drawPolyline(QPolygonF([QPointF(25, 21), QPointF(7, 21), QPointF(11, 25)]))
    elif name == "settings":
        p.drawEllipse(QRectF(8, 8, 16, 16))
        p.drawEllipse(QRectF(13, 13, 6, 6))
        for i in range(8):
            angle = i * math.pi / 4
            p.drawLine(QPointF(16 + 8 * math.cos(angle), 16 + 8 * math.sin(angle)),
                       QPointF(16 + 12 * math.cos(angle), 16 + 12 * math.sin(angle)))
    elif name == "bulb":
        p.drawEllipse(QRectF(9, 5, 14, 15))
        p.drawLine(QPointF(13, 21), QPointF(19, 21))
        p.drawLine(QPointF(14, 25), QPointF(18, 25))
    elif name == "refresh":
        p.drawArc(QRectF(6, 6, 20, 20), 40 * 16, 285 * 16)
        p.drawPolyline(QPolygonF([QPointF(23, 5), QPointF(25, 12), QPointF(18, 11)]))
    p.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class IconButton(QPushButton):
    def __init__(self, name, tooltip, callback):
        super().__init__()
        self.name = name
        self.setObjectName("navigation")
        self.setIconSize(QSize(26, 26))
        self.setMinimumHeight(44)
        self.setText(tooltip)
        self.clicked.connect(callback)

    def setText(self, text):
        self.setToolTip(text)
        icon = "pause" if self.name == "play" and text == "暂停" else self.name
        self.setIcon(action_icon(icon))
        super().setText("")


class MoveDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.renderers = {}

    def paint(self, painter, option, index):
        meta = index.data(MOVE_META) or {}
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        branch = meta.get("kind") in ("variation", "branch_header", "branch_number")
        painter.fillRect(rect, QColor("#333a2d" if branch else ("#292825" if index.row() % 2 else "#2e2d2a")))
        if branch and index.column() == 0:
            painter.setPen(QPen(QColor("#739f48"), 2))
            painter.drawLine(QPointF(rect.left() + 5, rect.top()), QPointF(rect.left() + 5, rect.bottom()))
        if option.state & QStyle.State_Selected and meta.get("kind") in ("real", "variation"):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#525c42" if branch else "#4b4a45"))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
        grade = meta.get("grade", "")
        annotation = meta.get("annotation", "")
        color = LABEL_COLORS.get(grade, "#dbe8ca" if branch else "#f2f0eb")
        painter.setPen(QColor(color))
        font = QFont("Segoe UI")
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        if index.column() == 0 or meta.get("kind") == "branch_header":
            painter.setPen(QColor("#b7cf9a" if branch else "#95928b"))
            font.setPixelSize(12)
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(rect.adjusted(13 if branch else 6, 0, -4, 0), Qt.AlignVCenter, str(index.data() or ""))
        elif meta:
            x = rect.left() + 10
            if grade or annotation:
                paint_grade(painter, QRectF(x, rect.center().y() - 9, 18, 18), grade, annotation)
            x += 25
            san = str(index.data() or "")
            if san and san[0] in "KQRBN":
                key = (san[0], color)
                if key not in self.renderers:
                    piece_type = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT}[san[0]]
                    svg = chess.svg.piece(chess.Piece(piece_type, chess.BLACK))
                    background = "#333a2d" if branch else "#2e2d2a"
                    svg = svg.replace("#000000", color).replace("#000", color)
                    svg = svg.replace("#ffffff", background).replace("#ececec", background).replace("#fff", background)
                    self.renderers[key] = QSvgRenderer(QByteArray(svg.encode()), self)
                self.renderers[key].render(painter, QRectF(x - 1, rect.center().y() - 10, 20, 20))
                x += 22
                san = san[1:]
            painter.drawText(QRectF(x, rect.top(), max(0, rect.right() - x - 5), rect.height()), Qt.AlignVCenter, san)
        painter.restore()
