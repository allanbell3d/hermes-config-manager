"""Colours, fonts, icons and the application style sheet (design tokens)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#ECEEF0", "pane": "#F3F4F6", "page": "#FFFFFF", "ink": "#1B2227", "muted": "#66717B",
    "faint": "#97A1AA", "line": "#CDD3D9", "soft": "#E3E7EA", "hover": "#F1F3F5", "sel": "#DCEDEF",
    "head": "#EEF0F2", "accent": "#0C6C76", "accent_bg": "#DDEFF1", "on_accent": "#FFFFFF",
    "amber": "#A05E00", "amber_edge": "#DE9A1F", "amber_bg": "#FBF0DA", "ok": "#2A7D50", "bad": "#B3342B",
    "bad_bg": "#F9E3E1", "add": "#E0F3E6", "del": "#FAE4E2", "shade": "rgba(20,28,34,97)",
}
DARK = {
    "bg": "#13171A", "pane": "#181D21", "page": "#1D2327", "ink": "#DBE1E5", "muted": "#8E99A3",
    "faint": "#65727C", "line": "#3A444C", "soft": "#2B343A", "hover": "#242B30", "sel": "#1B393E",
    "head": "#222A2F", "accent": "#62C6D1", "accent_bg": "#17373C", "on_accent": "#082125",
    "amber": "#E8AE55", "amber_edge": "#D4952F", "amber_bg": "#372B13", "ok": "#63C58B", "bad": "#EF8C84",
    "bad_bg": "#42211F", "add": "#1B3625", "del": "#41201E", "shade": "rgba(0,0,0,140)",
}

SANS = ["Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"]
MONO = ["Cascadia Mono", "Cascadia Code", "Consolas", "Menlo", "monospace"]


class _Tokens(dict):
    dark = False

    def c(self, name: str) -> QColor:
        return QColor(self[name])


T = _Tokens(LIGHT)

_ICONS = {
    "chev": '<svg viewBox="0 0 10 10"><path d="M3 1.5 7 5 3 8.5" fill="none" stroke="{c}" stroke-width="1.5"/></svg>',
    "chev_open": '<svg viewBox="0 0 10 10"><path d="M1.5 3 5 7 8.5 3" fill="none" stroke="{c}" stroke-width="1.5"/></svg>',
    "eye": '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" fill="none" '
           'stroke="{c}" stroke-width="1.3"/><circle cx="8" cy="8" r="2" fill="{c}"/></svg>',
    "eye_off": '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" '
               'fill="none" stroke="{c}" stroke-width="1.3"/><path d="M3 13.5 13 2.5" stroke="{c}" stroke-width="1.3"/></svg>',
    "up": '<svg viewBox="0 0 16 16"><path d="M8 13V3.5M4 7.5l4-4 4 4" fill="none" stroke="{c}" stroke-width="1.5"/></svg>',
    "down": '<svg viewBox="0 0 16 16"><path d="M8 3v9.5M4 8.5l4 4 4-4" fill="none" stroke="{c}" stroke-width="1.5"/></svg>',
    "x": '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="{c}" stroke-width="1.5"/></svg>',
    "undo": '<svg viewBox="0 0 16 16"><path d="M4 6h5.5a3 3 0 0 1 0 6H6M7 3 4 6l3 3" fill="none" stroke="{c}" '
            'stroke-width="1.5"/></svg>',
    "move": '<svg viewBox="0 0 16 16"><path d="M3 5.5h9M9.5 3 12 5.5 9.5 8M13 10.5H4M6.5 8 4 10.5 6.5 13" '
            'fill="none" stroke="{c}" stroke-width="1.4"/></svg>',
    "more": '<svg viewBox="0 0 16 16"><circle cx="3.5" cy="8" r="1.3" fill="{c}"/><circle cx="8" cy="8" r="1.3" '
            'fill="{c}"/><circle cx="12.5" cy="8" r="1.3" fill="{c}"/></svg>',
}
_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str = "muted", size: int = 14) -> QIcon:
    """An icon in a theme colour (token name or #hex)."""
    hexc = T.get(color, color)
    key = (name, hexc, size)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    svg = _ICONS[name].replace("{c}", hexc)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(QSize(size * scale, size * scale))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    _icon_cache[key] = ic
    return ic


def sans(px: float = 13, weight: int = QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies(SANS)
    f.setPixelSize(round(px))
    f.setWeight(QFont.Weight(weight))
    return f


def mono(px: float = 12.5, weight: int = QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies(MONO)
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(px * 0.75)
    f.setWeight(QFont.Weight(weight))
    return f


def qss(t: dict) -> str:
    return f"""
QWidget {{ color: {t['ink']}; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; }}
QMainWindow, QDialog, QWidget#app {{ background: {t['bg']}; }}
QWidget[pane="true"] {{ background: {t['pane']}; }}
QWidget[surface="page"] {{ background: {t['page']}; }}
QWidget[surface="bg"] {{ background: {t['bg']}; }}
QLabel {{ background: transparent; }}
QLabel[tone="muted"] {{ color: {t['muted']}; }}
QLabel[tone="faint"] {{ color: {t['faint']}; }}
QLabel[tone="amber"] {{ color: {t['amber']}; }}
QLabel[tone="accent"] {{ color: {t['accent']}; font-weight: 600; }}
QLabel[tone="bad"] {{ color: {t['bad']}; }}
QLabel[tone="ok"] {{ color: {t['ok']}; }}
QLabel[small="true"] {{ font-size: 12px; }}
QLabel[h1="true"] {{ font-size: 20px; font-weight: 600; }}
QLabel[h2="true"] {{ font-size: 15px; font-weight: 600; }}
QLabel[bold="true"] {{ font-weight: 600; }}

QPushButton {{ height: 26px; padding: 0 12px; border: 1px solid {t['line']}; border-radius: 5px;
    background: {t['page']}; }}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['soft']}; }}
QPushButton[sm="true"] {{ height: 22px; padding: 0 9px; font-size: 12px; }}
QPushButton[pri="true"] {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']};
    font-weight: 600; }}
QPushButton[pri="true"]:disabled {{ background: {t['soft']}; border-color: {t['soft']}; color: {t['faint']}; }}
QPushButton[has="true"] {{ background: {t['amber_bg']}; border-color: {t['amber_edge']}; color: {t['amber']};
    font-weight: 600; }}
QPushButton[on="true"] {{ background: {t['accent_bg']}; border-color: {t['accent']}; color: {t['accent']};
    font-weight: 600; }}
QPushButton[lnk="true"] {{ border: 0; background: transparent; padding: 0; color: {t['accent']};
    text-align: left; }}
QPushButton[lnk="true"]:hover {{ text-decoration: underline; background: transparent; }}
QPushButton[lnk="true"]:disabled {{ color: {t['faint']}; text-decoration: none; }}
QPushButton[lnk="true"][tone="am"] {{ color: {t['amber']}; }}
QPushButton[lnk="true"][tone="bad"] {{ color: {t['bad']}; }}
QPushButton[pill="true"] {{ border-radius: 11px; padding: 0 10px; height: 20px; font-size: 12px; }}
QPushButton[pill="true"][on="true"] {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; }}
QPushButton[chip="true"] {{ border-radius: 4px; padding: 0 9px; height: 20px; font-family: "Cascadia Mono", Consolas;
    font-size: 12px; text-align: left; }}
QPushButton[chip="true"]:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}
QPushButton[cell="true"] {{ border: 0; border-radius: 0; background: transparent; padding: 4px 8px; text-align: left;
    font-family: "Cascadia Mono", Consolas; font-size: 12.5px; }}
QPushButton[cell="true"]:hover {{ background: {t['accent_bg']}; }}
QPushButton[cell="true"][tone="faint"] {{ color: {t['faint']}; font-family: "Segoe UI"; }}
QPushButton[key="true"] {{ border: 0; background: transparent; padding: 0; text-align: left;
    font-family: "Cascadia Mono", Consolas; font-size: 12.5px; }}
QPushButton[key="true"]:hover {{ color: {t['accent']}; background: transparent; }}

QToolButton {{ border: 0; background: transparent; border-radius: 4px; padding: 0; }}
QToolButton:hover {{ background: {t['soft']}; }}
QToolButton:disabled {{ background: transparent; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{ border: 1px solid {t['line']}; border-radius: 4px;
    background: {t['page']}; padding: 0 8px; selection-background-color: {t['accent_bg']};
    selection-color: {t['ink']}; }}
QLineEdit {{ height: 24px; }}
QLineEdit:hover, QPlainTextEdit:hover {{ border-color: {t['faint']}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit[mono="true"], QPlainTextEdit[mono="true"] {{ font-family: "Cascadia Mono", Consolas; font-size: 12.5px; }}
QLineEdit[edited="true"] {{ border-color: {t['accent']}; }}
QLineEdit[dflt="true"] {{ color: {t['faint']}; }}
QLineEdit[search="true"] {{ height: 28px; }}
QLineEdit[pin="true"] {{ border-color: {t['accent']}; background: {t['accent_bg']}; }}
QPlainTextEdit {{ padding: 8px; }}

QComboBox {{ border: 1px solid {t['line']}; border-radius: 4px; background: {t['page']}; padding: 0 22px 0 6px;
    height: 24px; min-width: 60px; }}
QComboBox:hover {{ border-color: {t['faint']}; }}
QComboBox[dflt="true"] {{ color: {t['faint']}; font-style: italic; }}
QComboBox[flat="true"] {{ border: 0; border-radius: 0; background: transparent; height: 30px; padding: 0 18px 0 6px; }}
QComboBox[flat="true"]:hover {{ background: {t['accent_bg']}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{ background: {t['page']}; border: 1px solid {t['line']}; selection-background-color:
    {t['sel']}; selection-color: {t['ink']}; outline: 0; }}

QCheckBox {{ spacing: 8px; background: transparent; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ width: 12px; background: transparent; margin: 0; }}
QScrollBar:horizontal {{ height: 12px; background: transparent; margin: 0; }}
QScrollBar::handle {{ background: {t['line']}; border-radius: 3px; margin: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {t['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {t['pane']}; border-right: 1px solid {t['line']}; }}
QSplitter::handle:hover {{ background: {t['accent_bg']}; border-right-color: {t['accent']}; }}
QSplitter[aux="true"]::handle {{ background: {t['head']}; border: 1px solid {t['line']}; }}

QToolTip {{ background: {t['page']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 4px 6px; }}
QMenu {{ background: {t['page']}; border: 1px solid {t['line']}; padding: 4px 0; }}
QMenu::item {{ padding: 5px 18px 5px 14px; }}
QMenu::item:selected {{ background: {t['hover']}; color: {t['ink']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['soft']}; margin: 4px 0; }}

QTableView, QTreeView, QListView {{ background: {t['page']}; border: 1px solid {t['line']}; border-radius: 5px;
    gridline-color: {t['soft']}; selection-background-color: {t['sel']}; selection-color: {t['ink']};
    outline: 0; }}
QHeaderView::section {{ background: {t['page']}; color: {t['muted']}; font-size: 12px; border: 0;
    border-bottom: 1px solid {t['line']}; border-left: 1px solid {t['soft']}; padding: 0 8px; height: 26px; }}
QProgressBar {{ border: 1px solid {t['line']}; border-radius: 4px; background: {t['page']}; height: 8px;
    text-align: center; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
"""


def _indicator_images(t: dict) -> dict[str, str]:
    """Checkbox images in the theme colours (Qt style sheets need files)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPen

    from ..core import appdirs

    folder = appdirs.cache_dir() / "style"
    folder.mkdir(parents=True, exist_ok=True)
    tag = "dark" if t is DARK or t.get("bg") == DARK["bg"] else "light"
    out = {}
    specs = {
        "unchecked": (t["line"], t["page"], None), "hover": (t["faint"], t["page"], None),
        "checked": (t["accent"], t["accent"], "check"), "partial": (t["accent"], t["accent"], "dash"),
        "disabled": (t["soft"], t["pane"], None), "disabled_checked": (t["soft"], t["soft"], "check"),
    }
    for name, (edge, fill, mark) in specs.items():
        for scale, suffix in ((1, ""), (2, "@2x")):
            size = 14 * scale
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(edge), 1 * scale))
            p.setBrush(QColor(fill))
            p.drawRoundedRect(QRectF(0.5 * scale, 0.5 * scale, size - scale, size - scale), 3 * scale, 3 * scale)
            if mark:
                p.setPen(QPen(QColor(t["on_accent"] if "disabled" not in name else t["faint"]), 1.8 * scale,
                              Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                path = QPainterPath()
                if mark == "check":
                    path.moveTo(QPointF(3.5 * scale, 7.2 * scale))
                    path.lineTo(QPointF(6 * scale, 9.7 * scale))
                    path.lineTo(QPointF(10.6 * scale, 4.3 * scale))
                else:
                    path.moveTo(QPointF(3.8 * scale, 7 * scale))
                    path.lineTo(QPointF(10.2 * scale, 7 * scale))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            p.end()
            f = folder / f"cb-{tag}-{name}{suffix}.png"
            pm.save(str(f))
        out[name] = str(folder / f"cb-{tag}-{name}.png").replace("\\", "/")
    for name, colour in (("arrow", t["muted"]), ("arrow_off", t["faint"])):
        for scale, suffix in ((1, ""), (2, "@2x")):
            size = 10 * scale
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(colour), 1.5 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath()
            path.moveTo(QPointF(1.5 * scale, 3.2 * scale))
            path.lineTo(QPointF(5 * scale, 6.8 * scale))
            path.lineTo(QPointF(8.5 * scale, 3.2 * scale))
            p.drawPath(path)
            p.end()
            pm.save(str(folder / f"{name}-{tag}{suffix}.png"))
        out[name] = str(folder / f"{name}-{tag}.png").replace("\\", "/")
    return out


def indicator_qss(t: dict) -> str:
    try:
        im = _indicator_images(t)
    except OSError:
        return ""
    return f"""
QCheckBox::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator:unchecked {{ image: url({im['unchecked']}); }}
QCheckBox::indicator:unchecked:hover {{ image: url({im['hover']}); }}
QCheckBox::indicator:checked {{ image: url({im['checked']}); }}
QCheckBox::indicator:indeterminate {{ image: url({im['partial']}); }}
QCheckBox::indicator:unchecked:disabled {{ image: url({im['disabled']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({im['disabled_checked']}); }}
QComboBox::down-arrow {{ image: url({im['arrow']}); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({im['arrow_off']}); }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 16px; border: 0; }}
"""


def apply_theme(app: QApplication, dark: bool) -> None:
    T.clear()
    T.update(DARK if dark else LIGHT)
    T.dark = dark
    _icon_cache.clear()
    app.setFont(sans(13))
    pal = app.palette()
    for role, name in ((QPalette.Window, "bg"), (QPalette.Base, "page"), (QPalette.AlternateBase, "pane"),
                       (QPalette.Text, "ink"), (QPalette.WindowText, "ink"), (QPalette.ButtonText, "ink"),
                       (QPalette.Button, "page"), (QPalette.Highlight, "sel"), (QPalette.HighlightedText, "ink"),
                       (QPalette.ToolTipBase, "page"), (QPalette.ToolTipText, "ink"),
                       (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent")):
        pal.setColor(role, QColor(T[name]))
    app.setPalette(pal)
    app.setStyleSheet(qss(T) + indicator_qss(T))
