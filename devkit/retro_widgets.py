"""Static, resolution independent artwork for an idle emulation display.

This is host interface decoration, drawn only when no guest frame is present.
It never creates device telemetry, starts a guest, or changes a guest image.
"""
from collections.abc import Mapping
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen


def _color(colors: Mapping, role: str, fallback: str, alpha: int = 255) -> QColor:
    result = QColor(colors.get(role, fallback))
    result.setAlpha(alpha)
    return result


def _mono(size: float, weight=QFont.Weight.Normal, tracking: float = 0.0) -> QFont:
    font = QFont()
    font.setFamilies(['Cascadia Mono', 'Consolas', 'DejaVu Sans Mono',
                      'Microsoft YaHei UI', 'sans-serif'])
    font.setPixelSize(round(size))
    font.setWeight(weight)
    if tracking:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    return font


def _line(painter, start, end):
    painter.drawLine(QPointF(*start), QPointF(*end))


def _polygon(painter, points):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    painter.drawPath(path)


def _processor(painter: QPainter, phosphor: QColor, amber: QColor, background: QColor):
    """An original isometric chip, with decorative traces rather than a chart."""
    origin = QPointF(300, 127)

    def plane(x, y, z=0):
        return (origin.x() + x - y, origin.y() + (x + y) * 0.46 - z)

    # Low contrast board plane and hand routed traces.
    faint = QColor(phosphor); faint.setAlpha(32)
    painter.setPen(QPen(faint, 0.8))
    for offset in (-70, -35, 0, 35, 70):
        _line(painter, plane(-82, offset), plane(82, offset))
        _line(painter, plane(offset, -82), plane(offset, 82))
    trace = QColor(phosphor); trace.setAlpha(98)
    painter.setPen(QPen(trace, 1))
    for side in (-1, 1):
        for pin in (-20, 0, 20):
            for swap in (False, True):
                route = [(side * 39, pin), (side * 61, pin),
                         (side * 72, pin + side * 11)]
                points = [plane(y, x) if swap else plane(x, y) for x, y in route]
                _line(painter, points[0], points[1]); _line(painter, points[1], points[2])
                painter.setBrush(background)
                painter.drawEllipse(QPointF(*points[2]), 1.6, 1.6)

    # The front extrusion gives the chip a quiet, physical instrument feel.
    left, rear, right, front = [plane(x, y, 13) for x, y in
                               ((-39, 39), (-39, -39), (39, -39), (39, 39))]
    low_left, low_front, low_right = [plane(x, y) for x, y in
                                    ((-39, 39), (39, 39), (39, -39))]
    painter.setBrush(background)
    painter.setPen(QPen(trace, 1))
    _polygon(painter, (left, front, low_front, low_left))
    _polygon(painter, (front, right, low_right, low_front))
    painter.setPen(QPen(phosphor, 1.35))
    _polygon(painter, (left, rear, right, front))
    inner = [plane(x, y, 13) for x, y in ((-29, 29), (-29, -29), (29, -29), (29, 29))]
    painter.setPen(QPen(trace, 0.9))
    _polygon(painter, inner)

    # A simple native code glyph, not a fabricated running waveform.
    painter.setPen(QPen(phosphor, 1.4))
    for points in (((286, 108), (277, 114), (286, 120)),
                   ((314, 108), (323, 114), (314, 120))):
        _line(painter, points[0], points[1]); _line(painter, points[1], points[2])
    painter.setPen(QPen(amber, 1.7))
    _line(painter, (304, 105), (296, 123))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(amber)
    painter.drawEllipse(QPointF(*plane(-29, -22, 13)), 2, 2)


def paint_idle_screen(painter: QPainter, rect, title: str, caption: str,
                      colors: Mapping | None = None) -> None:
    """Paint a CRT inspired *idle* screen, bounded by ``rect``.

    Coordinates use a 600 × 450 design space and scale uniformly. Optional
    ``crt_*`` semantic colors override the dark phosphor palette. The supplied
    title and caption carry the actual application state; all other artwork is
    decorative. Callers should draw real guest frames without this overlay.
    """
    colors = colors or {}
    bounds = QRectF(rect)
    if bounds.width() <= 0 or bounds.height() <= 0:
        return
    background = _color(colors, 'crt_background', '#0a100e')
    phosphor = _color(colors, 'crt_phosphor', '#b9f18a')
    amber = _color(colors, 'crt_amber', '#e5b765')
    muted = _color(colors, 'crt_muted', '#789583')
    grid = _color(colors, 'crt_grid', '#7cba93', 22)

    painter.save()
    painter.setClipRect(bounds)
    painter.fillRect(bounds, background)
    scale = min(bounds.width() / 600.0, bounds.height() / 450.0)
    painter.translate(bounds.center())
    painter.scale(scale, scale)
    painter.translate(-300, -225)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Sparse intersections remain soft at small sizes; text has no scanline
    # filter so that CJK captions and error messages stay readable.
    painter.setPen(QPen(grid, 0.75))
    for y in range(54, 397, 24):
        for x in range(36, 565, 24):
            _line(painter, (x - 1.5, y), (x + 1.5, y))
            _line(painter, (x, y - 1.5), (x, y + 1.5))
    painter.setPen(QPen(_color(colors, 'crt_grid', '#7cba93', 38), 0.8))
    painter.drawRect(QRectF(15.5, 15.5, 569, 419))
    painter.setPen(QPen(_color(colors, 'crt_phosphor', '#b9f18a', 115), 1.2))
    for x, y, sx, sy in ((16, 16, 1, 1), (584, 16, -1, 1),
                         (16, 434, 1, -1), (584, 434, -1, -1)):
        _line(painter, (x, y), (x + 13 * sx, y))
        _line(painter, (x, y), (x, y + 13 * sy))

    painter.setFont(_mono(9, tracking=1.5))
    painter.setPen(muted)
    painter.drawText(QRectF(33, 29, 350, 15), Qt.AlignmentFlag.AlignLeft,
                     'QEMU / FIRMWARE WORKBENCH')
    painter.setPen(amber)
    painter.drawText(QRectF(400, 29, 167, 15), Qt.AlignmentFlag.AlignRight, '[ DISPLAY ]')

    _processor(painter, phosphor, amber, background)

    # Leave ample room for translated or dynamic status strings. Long titles
    # use two readable lines instead of becoming tiny or crossing the bezel.
    text = str(title or 'QEMU Everything')
    font = _mono(30, QFont.Weight.DemiBold, 0.3)
    if QFontMetricsF(font).horizontalAdvance(text) > 508:
        font = _mono(24, QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(phosphor)
    painter.drawText(QRectF(46, 213, 508, 68),
                     Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    painter.setPen(QPen(_color(colors, 'crt_phosphor', '#b9f18a', 88), 1))
    _line(painter, (255, 290), (291, 290))
    _line(painter, (309, 290), (345, 290))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(amber)
    painter.drawRect(QRectF(297.5, 287.5, 5, 5))

    painter.setPen(muted)
    painter.setFont(_mono(13))
    painter.drawText(QRectF(63, 307, 474, 60),
                     Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop |
                     Qt.TextFlag.TextWordWrap, str(caption or ''))

    painter.setPen(QPen(_color(colors, 'crt_grid', '#7cba93', 48), 0.8))
    _line(painter, (34, 390), (566, 390))
    painter.setFont(_mono(9, tracking=1.2))
    painter.setPen(muted)
    painter.drawText(QRectF(34, 404, 532, 14), Qt.AlignmentFlag.AlignCenter,
                     'FIRMWARE   /   MACHINE   /   DISPLAY')
    painter.restore()
