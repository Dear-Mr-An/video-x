# -*- coding: utf-8 -*-
"""Light / technology-blue visual system for the Qt6 application."""
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

APP_NAME = '神秘·X'

QSS = """
QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 13px; color: #233550; }
QMainWindow, QWidget#Root, QDialog { background: #f3f6fc; }
QLabel { background: transparent; border: none; }
QLabel#Brand { font-size: 18px; font-weight: 700; color: #152b50; }
QLabel#Overline { font-size: 10px; font-weight: 700; color: #8293ad; }
QLabel#PageTitle { font-size: 24px; font-weight: 700; color: #132b50; }
QLabel#HeroTitle { font-size: 22px; font-weight: 700; color: #153d78; }
QLabel#CardTitle { font-size: 16px; font-weight: 700; color: #1b3154; }
QLabel#FieldTitle { font-size: 13px; font-weight: 600; color: #294367; }
QLabel#Muted { font-size: 12px; color: #8190a8; }
QLabel#Subtitle { font-size: 12px; color: #6883a8; }
QLabel#BlueText { color: #2671ed; font-size: 12px; font-weight: 600; }
QLabel#Badge { background: #edf4ff; color: #3275db; border: 1px solid #dce9ff; border-radius: 11px; padding: 3px 10px; font-size: 11px; }
QLabel#StatusBadge { background: #eef5ff; color: #2e72df; border-radius: 11px; padding: 4px 10px; font-size: 11px; }
QLabel#StatusBadge[state="running"] { background: #e7f0ff; color: #1769e8; }
QLabel#StatusBadge[state="success"] { background: #e6f7f0; color: #198761; }
QLabel#StatusBadge[state="error"] { background: #fff0ee; color: #ce5a4c; }
QLabel#StatusBadge[state="cancelled"] { background: #fff4e4; color: #ad791e; }
QLabel#Percent { font-size: 26px; font-weight: 700; color: #276de5; }
QLabel#StepNumber { background: #edf4ff; color: #3678e6; border-radius: 8px; font-size: 12px; font-weight: 700; }
QFrame#Sidebar { background: #ffffff; border-right: 1px solid #e5ebf5; }
QFrame#Card { background: #ffffff; border: 1px solid #e2e9f4; border-radius: 16px; }
QFrame#Hero { background: qlineargradient(x1:0, y1:0, x2:1, y2:0.8, stop:0 #e7f0ff, stop:0.65 #edf5ff, stop:1 #e2f4ff); border: 1px solid #d9e7fc; border-radius: 16px; }
QFrame#Hardware { background: #f5f8fe; border: 1px solid #e6edf8; border-radius: 12px; }
QFrame#Line { background: #edf1f7; border: none; min-height: 1px; max-height: 1px; }
QPushButton { background: #ffffff; color: #506989; border: 1px solid #dce5f2; border-radius: 8px; padding: 8px 14px; font-weight: 500; }
QPushButton:hover { background: #f1f6ff; color: #246eea; border-color: #accafa; }
QPushButton:pressed { background: #e7f0ff; }
QPushButton:disabled { background: #f4f6fa; color: #a8b4c5; border-color: #e9edf4; }
QPushButton#Primary { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #347dff, stop:1 #2462e8); border: none; color: #ffffff; font-weight: 700; padding: 10px 22px; }
QPushButton#Primary:hover { background: #4288ff; }
QPushButton#Primary:pressed { background: #1b58d6; }
QPushButton#Primary:disabled { background: #b6cff7; color: #f5f8ff; }
QPushButton#Nav { background: transparent; border: 1px solid transparent; color: #71829d; text-align: left; padding: 12px 15px; border-radius: 9px; }
QPushButton#Nav:hover { background: #f5f8ff; color: #286fe5; }
QPushButton#Nav:checked { background: #edf4ff; border-color: #e0ecff; color: #246ce5; font-weight: 700; }
QPushButton#TextButton { background: transparent; border: none; color: #4080e8; padding: 3px 4px; font-size: 12px; }
QPushButton#TextButton:hover { color: #155ad3; background: #f0f5ff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #f9fbff; color: #304665; border: 1px solid #e0e7f2; border-radius: 8px; padding: 8px 10px; min-height: 18px; selection-background-color: #3279f0; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #b7cdf0; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { background: #ffffff; border: 1px solid #4388f5; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f3f5f9; color: #a1aec0; border-color: #e9edf3; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 25px; border: none; }
QComboBox QAbstractItemView { background: #ffffff; color: #304665; border: 1px solid #dbe5f3; selection-background-color: #edf4ff; selection-color: #276ddd; padding: 5px; outline: none; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 21px; border: none; border-left: 1px solid #e3eaf5; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 21px; border: none; border-left: 1px solid #e3eaf5; }
QCheckBox { spacing: 8px; color: #526782; font-size: 12px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QProgressBar { background: #edf2fb; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #316ef0, stop:1 #39b4ff); border-radius: 4px; }
QPlainTextEdit { background: #f7f9fd; border: 1px solid #eaf0f8; border-radius: 10px; color: #667a96; font-family: 'Consolas', 'Microsoft YaHei UI'; font-size: 12px; padding: 10px; selection-background-color: #d7e8ff; selection-color: #25436c; }
QScrollArea, QWidget#ScrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #d2deef; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a5bde1; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 7px; }
QScrollBar::handle:horizontal { background: #d2deef; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QToolTip { background: #ffffff; color: #365374; border: 1px solid #dce6f4; padding: 7px; }
"""

_ASSETS = (Path(__file__).resolve().parent / 'assets').as_posix()
QSS += """
QComboBox::down-arrow { image: url("%s/chevron-down.svg"); width: 12px; height: 12px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("%s/chevron-up.svg"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("%s/chevron-down.svg"); width: 10px; height: 10px; }
QCheckBox::indicator { background: #ffffff; border: 1px solid #c8d5e9; border-radius: 4px; }
QCheckBox::indicator:hover { border-color: #4388f5; }
QCheckBox::indicator:checked { background: #327af2; border-color: #327af2; image: url("%s/check.svg"); }
QCheckBox::indicator:disabled { background: #edf1f7; border-color: #dce4f0; }
QCheckBox::indicator:checked:disabled { background: #abc8f1; }
""" % (_ASSETS, _ASSETS, _ASSETS, _ASSETS)

ICON_PATHS = {
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'video': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10H3z"/><path d="M3 10h18"/>',
    'sliders': '<path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    'log': '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    'play': '<path d="m8 4 12 8-12 8z"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    'chip': '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
}


def icon(name, color='#6682a8', size=20):
    body = ICON_PATHS.get(name, ICON_PATHS['grid'])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
           f'<g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
           f'stroke-linejoin="round">{body}</g></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class BrandMark(QWidget):
    """Display the supplied brand image without altering the original file."""
    def __init__(self, path, size=48, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.pixmap = QPixmap(path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, self.width(), self.height(), 11, 11)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QColor('#12305a'))
        if not self.pixmap.isNull():
            painter.drawPixmap(self.rect(), self.pixmap)
        painter.end()
