# -*- coding: utf-8 -*-
"""神秘·X — Qt6 视频处理工作台。"""
import os
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject, QSize, QUrl, QTimer
from PySide6.QtGui import QIcon, QFont, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QScrollArea, QFrame, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox,
    QGridLayout, QDialog,
)
import engine
from ui_theme import APP_NAME, QSS, BrandMark, icon

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, '2.png')
RESOLUTIONS = [
    ('720 × 1256（默认）', 720, 1256),
    ('496 × 864', 496, 864),
    ('576 × 1248', 576, 1248),
    ('1080 × 1920', 1080, 1920),
    ('576 × 1024（原参考）', 576, 1024),
    ('自定义', 0, 0),
]
SAR_PRESETS = ['1:1（当前）', '51675:2671（模式三）', '自定义']
DURATION_MODES = ['固定', '随机', '递增']
TOOL_TAGS = ['保持当前（无标签）', 'Wxmm_9020230', 'Wxmm_9020230808']
COMPRESSORS = ['Lavc h264_nvenc', 'Wxmm_9020230', 'Wxmm_9020230808']
MAGIC_MODES = ['固定', '随机']


def app_icon():
    result = QIcon()
    source = QPixmap(ICON_PATH)
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return result


def label(text, style='Muted'):
    widget = QLabel(text)
    widget.setObjectName(style)
    return widget


def line():
    return QFrame(objectName='Line')


def button(text, symbol=None, style='', callback=None):
    widget = QPushButton(text)
    if style:
        widget.setObjectName(style)
    if symbol:
        widget.setIcon(icon(symbol, '#ffffff' if style == 'Primary' else '#6281af'))
        widget.setIconSize(QSize(18, 18))
    widget.setCursor(Qt.PointingHandCursor)
    if callback:
        widget.clicked.connect(callback)
    return widget


def spin(minimum, maximum, value, suffix=''):
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    widget.setSuffix(suffix)
    widget.setMinimumWidth(82)
    return widget


class PathEdit(QLineEdit):
    """Accept local file/directory drops as well as normal text entry."""
    def __init__(self, placeholder, directory=False):
        super().__init__()
        self.directory = directory
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)
        self.setMinimumWidth(100)
        self.textChanged.connect(self.setToolTip)

    def _drop_path(self, mime):
        if not mime.hasUrls() or len(mime.urls()) != 1:
            return ''
        url = mime.urls()[0]
        if not url.isLocalFile():
            return ''
        path = url.toLocalFile()
        if self.directory:
            return path if os.path.isdir(path) else ''
        return path if os.path.isfile(path) and os.path.splitext(path)[1].lower() in engine.VIDEO_EXTS else ''

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if self._drop_path(event.mimeData()):
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            path = self._drop_path(event.mimeData())
            if path:
                self.setText(os.path.normpath(path))
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            super().dropEvent(event)


class Worker(QThread):
    log = Signal(str)
    progress = Signal(float, str)
    completed = Signal(int, int, str)

    def __init__(self, opts, parent=None):
        super().__init__(parent)
        self.opts = opts
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @Slot()
    def run(self):
        try:
            job = engine.AbJob(
                self.opts, log=self.log.emit,
                progress=lambda p, s: self.progress.emit(p, s),
                cancel=lambda: self._cancel,
            )
            ok, fail = job.run()
            message = '已取消' if self._cancel else '已完成：成功 %d，失败 %d' % (ok, fail)
            self.completed.emit(ok, fail, message)
        except KeyboardInterrupt:
            self.completed.emit(0, 0, '已取消')
        except Exception as exc:
            self.completed.emit(0, 0, '错误：%s' % exc)


class GpuProbe(QThread):
    detected = Signal(str)

    @Slot()
    def run(self):
        name = '未检测到 NVIDIA 显卡'
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout.strip():
                name = result.stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            pass
        self.detected.emit(name)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1360, 940)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(QSS)
        self.worker = None
        self.thread = None
        self.gpu_thread = None
        self.gpu_probe = None
        self.settings_dlg = None
        self._close_pending = False
        self._build_ui()
        # Generating must not require opening the settings dialog first.
        self._build_settings_dialog()
        self._update_summary()
        self._start_gpu_probe()

    def _build_ui(self):
        root = QWidget(objectName='Root')
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_panel(), 1)

    def _build_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 20, 28, 16)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(BrandMark(ICON_PATH, 46))
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(label('神秘·X', 'Brand'))
        heading.addWidget(label('用心做好每一次开源', 'PageTitle'))
        header.addLayout(heading)
        header.addStretch()
        self.gpu_label = label('正在检测显卡…', 'Muted')
        header.addWidget(self.gpu_label, 0, Qt.AlignVCenter)
        header.addSpacing(6)
        header.addWidget(button('打开输出目录', 'folder', callback=self._open_output), 0, Qt.AlignVCenter)
        header.addSpacing(6)
        self.settings_btn = button('高级设置', 'sliders', callback=self._open_settings)
        header.addWidget(self.settings_btn, 0, Qt.AlignVCenter)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        content = QWidget(objectName='ScrollContent')
        self.scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(18)
        body.addWidget(self._build_hero())
        columns = QHBoxLayout()
        columns.setSpacing(18)
        self.input_card = self._build_inputs()
        self.params_card = self._build_parameters()
        columns.addWidget(self.input_card, 3)
        columns.addWidget(self.params_card, 2)
        body.addLayout(columns)
        body.addStretch(1)
        self.scroll.setMinimumHeight(180)
        layout.addWidget(self.scroll, 1)
        # Keep task controls visible even on smaller displays.
        self.task_card = self._build_task_card()
        layout.addWidget(self.task_card)
        footer = QHBoxLayout()
        footer.addWidget(label(APP_NAME, 'Muted'))
        footer.addStretch()
        footer.addWidget(label('本地处理  /  素材无需上传', 'Muted'))
        layout.addLayout(footer)
        return panel

    def _build_hero(self):
        hero = QFrame(objectName='Hero')
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(24, 16, 24, 16)
        text = QVBoxLayout()
        text.setSpacing(7)
        text.addWidget(label('VIDEO WORKSPACE', 'BlueText'))
        text.addWidget(label('让每一帧，都有新的可能。', 'HeroTitle'))
        text.addWidget(label('导入素材，调整参数，一键开启视频创作。', 'Subtitle'))
        layout.addLayout(text, 1)
        steps = QHBoxLayout()
        steps.setSpacing(10)
        for index, title in enumerate(('导入素材', '配置参数', '生成视频'), 1):
            if index > 1:
                arrow = QLabel()
                arrow.setPixmap(icon('arrow', '#98b9e9', 18).pixmap(18, 18))
                steps.addWidget(arrow)
            step = QVBoxLayout()
            step.setSpacing(9)
            number = label(f'0{index}', 'StepNumber')
            number.setFixedSize(34, 32)
            number.setAlignment(Qt.AlignCenter)
            step.addWidget(number, 0, Qt.AlignHCenter)
            step.addWidget(label(title, 'Subtitle'), 0, Qt.AlignHCenter)
            steps.addLayout(step)
        layout.addLayout(steps)
        return hero

    def _card(self, title, subtitle, symbol):
        card = QFrame(objectName='Card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(15)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(icon(symbol, '#347bf0', 22).pixmap(22, 22))
        header.addWidget(mark)
        header.addWidget(label(title, 'CardTitle'))
        header.addStretch()
        layout.addLayout(header)
        description = label(subtitle, 'Muted')
        description.setWordWrap(True)
        layout.addWidget(description)
        return card, layout

    def _path_field(self, layout, title, hint, edit, callback, browse_text='选择文件'):
        heading = QHBoxLayout()
        heading.addWidget(label(title, 'FieldTitle'))
        heading.addStretch()
        heading.addWidget(label(hint))
        layout.addLayout(heading)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(edit, 1)
        row.addWidget(button(browse_text, 'folder', callback=callback))
        layout.addLayout(row)

    def _build_inputs(self):
        card, layout = self._card('素材与输出', '支持 MP4 / MOV / MKV 等格式，可直接拖入文件', 'folder')
        layout.setSpacing(9)
        self.head_edit = PathEdit('选择或拖入实拍视频')
        self.mat_edit = PathEdit('选择或拖入素材视频')
        self.out_edit = PathEdit('选择视频保存文件夹', directory=True)
        self._path_field(layout, '实拍视频', '插入片段来源', self.head_edit,
                         lambda: self._pick_file(self.head_edit, '选择实拍视频'))
        layout.addSpacing(2)
        self._path_field(layout, '素材视频', '决定输出时长', self.mat_edit,
                         lambda: self._pick_file(self.mat_edit, '选择素材视频'))
        layout.addSpacing(2)
        self._path_field(layout, '输出目录', '重名自动编号', self.out_edit,
                         self._pick_dir, '选择目录')
        layout.addSpacing(4)
        layout.addWidget(line())
        options = QHBoxLayout()
        options.setSpacing(12)
        self.batch_chk = QCheckBox('批量处理')
        self.batch_chk.setToolTip('处理所选素材所在文件夹中的全部视频')
        self.repeat_chk = QCheckBox('重复处理')
        self.repeats_spin = spin(1, 1000, 1, ' 次')
        self.repeats_spin.setFixedWidth(94)
        self.repeats_spin.setEnabled(False)
        self.repeat_chk.toggled.connect(self.repeats_spin.setEnabled)
        options.addWidget(self.batch_chk)
        options.addWidget(self.repeat_chk)
        options.addWidget(self.repeats_spin)
        options.addStretch()
        layout.addLayout(options)
        note = label('批量模式将处理素材所在目录中的所有视频。')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return card

    def _build_parameters(self):
        card, layout = self._card('输出参数', '精细控制画面尺寸与帧序列', 'sliders')
        card.setMinimumWidth(300)
        layout.setSpacing(9)
        layout.addWidget(label('输出分辨率', 'FieldTitle'))
        self.res_combo = QComboBox()
        self.res_combo.addItems([item[0] for item in RESOLUTIONS])
        layout.addWidget(self.res_combo)
        self.custom_size = QWidget()
        custom = QHBoxLayout(self.custom_size)
        custom.setContentsMargins(0, 0, 0, 0)
        self.cust_w = spin(64, 8192, 720, ' px')
        self.cust_h = spin(64, 8192, 1256, ' px')
        self.cust_w.setSingleStep(2)
        self.cust_h.setSingleStep(2)
        custom.addWidget(self.cust_w, 1)
        custom.addWidget(label('×'))
        custom.addWidget(self.cust_h, 1)
        layout.addWidget(self.custom_size)
        self.custom_size.hide()
        self.cust_w.setEnabled(False)
        self.cust_h.setEnabled(False)
        self.res_combo.currentIndexChanged.connect(self._res_changed)
        self.cust_w.valueChanged.connect(self._update_summary)
        self.cust_h.valueChanged.connect(self._update_summary)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        self.mat_frames = spin(0, 600, 1, ' 帧')
        self.live_frames = spin(0, 600, 1, ' 帧')
        self.intro_frames = spin(0, 6000, 23, ' 帧')
        for row, (title, control) in enumerate((('素材视频', self.mat_frames),
                                               ('插入实拍', self.live_frames),
                                               ('片头保留', self.intro_frames))):
            grid.addWidget(label(title, 'FieldTitle'), row, 0)
            grid.addWidget(control, row, 1)
        layout.addLayout(grid)
        layout.addStretch()
        layout.addWidget(line())
        self.enc_label = label('H.264  /  60 FPS  /  NVENC', 'BlueText')
        layout.addWidget(self.enc_label)
        self.output_summary = label('')
        layout.addWidget(self.output_summary)
        return card

    def _build_task_card(self):
        card = QFrame(objectName='Card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(9)
        top = QHBoxLayout()
        top.addWidget(label('生成任务', 'CardTitle'))
        self.state_badge = label('●  准备就绪', 'StatusBadge')
        top.addWidget(self.state_badge, 0, Qt.AlignVCenter)
        top.addStretch()
        self.cancel_btn = button('取消任务', 'stop', callback=self._cancel)
        self.cancel_btn.setEnabled(False)
        self.start_btn = button('开始生成', 'play', 'Primary', self._start)
        self.start_btn.setMinimumWidth(148)
        top.addWidget(self.cancel_btn)
        top.addWidget(self.start_btn)
        layout.addLayout(top)
        status = QHBoxLayout()
        self.status_label = label('准备就绪，导入视频后即可开始生成。')
        self.status_label.setWordWrap(True)
        self.percent_label = label('0%', 'Percent')
        status.addWidget(self.status_label, 1)
        status.addWidget(self.percent_label)
        layout.addLayout(status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.valueChanged.connect(lambda value: self.percent_label.setText(f'{value}%'))
        layout.addWidget(self.progress)
        logs = QHBoxLayout()
        logs.addWidget(label('运行日志', 'FieldTitle'))
        logs.addWidget(label(' /  实时记录处理过程'))
        logs.addStretch()
        logs.addWidget(button('清空日志', style='TextButton', callback=self._clear_logs))
        layout.addLayout(logs)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(3000)
        self.log_box.setFixedHeight(96)
        self.log_box.setPlaceholderText('等待任务开始…\n选择实拍视频、素材视频与输出目录，处理日志将显示在这里。')
        layout.addWidget(self.log_box)
        return card

    def _build_settings_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('高级设置 · ' + APP_NAME)
        dlg.setWindowIcon(self.windowIcon())
        dlg.setStyleSheet(QSS)
        dlg.resize(720, 740)
        dlg.setMinimumSize(660, 620)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label('高级设置', 'PageTitle'))
        heading.addStretch()
        heading.addWidget(label('当前会话生效', 'Badge'), 0, Qt.AlignVCenter)
        outer.addLayout(heading)
        outer.addWidget(label('保留现有处理能力；修改后自动应用于下一次生成任务。'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget(objectName='ScrollContent')
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 6, 0)
        sections.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        container, layout = self._card('封装与像素比例', '按需启用，未勾选时使用默认处理方式', 'video')
        duration = QHBoxLayout()
        self.dur_chk = QCheckBox('封装时间')
        self.dur_mode = QComboBox()
        self.dur_mode.addItems(DURATION_MODES)
        self.dur_sec = spin(1, 999999, 11880, ' 秒')
        duration.addWidget(self.dur_chk)
        duration.addWidget(self.dur_mode, 1)
        duration.addWidget(self.dur_sec, 1)
        layout.addLayout(duration)
        sar = QHBoxLayout()
        self.sar_chk = QCheckBox('自定义 SPPS')
        self.sar_preset = QComboBox()
        self.sar_preset.addItems(SAR_PRESETS)
        self.sar_w = spin(1, 65535, 1)
        self.sar_h = spin(1, 65535, 1)
        sar.addWidget(self.sar_chk)
        sar.addWidget(self.sar_preset, 1)
        sar.addWidget(self.sar_w)
        sar.addWidget(label(':'))
        sar.addWidget(self.sar_h)
        layout.addLayout(sar)
        self.sei_chk = QCheckBox('清除 SEI 附加信息')
        layout.addWidget(self.sei_chk)
        sections.addWidget(container)

        metadata, layout = self._card('编码标识', '配置输出文件的工具标识和编码器标记', 'chip')
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.addItems(TOOL_TAGS)
        self.comp_combo = QComboBox()
        self.comp_combo.setEditable(True)
        self.comp_combo.addItems(COMPRESSORS)
        grid.addWidget(label('工具标识', 'FieldTitle'), 0, 0)
        grid.addWidget(self.tag_combo, 0, 1)
        grid.addWidget(label('编码器标记', 'FieldTitle'), 1, 0)
        grid.addWidget(self.comp_combo, 1, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        sections.addWidget(metadata)

        experimental, layout = self._card('实验功能', '修改封装大小字段，可能影响播放器兼容性，请谨慎启用。', 'info')
        magic = QHBoxLayout()
        self.magic_chk = QCheckBox('魔法大小')
        self.magic_mode = QComboBox()
        self.magic_mode.addItems(MAGIC_MODES)
        self.magic_size = QDoubleSpinBox()
        self.magic_size.setRange(0.001, 999999.0)
        self.magic_size.setDecimals(3)
        self.magic_size.setValue(670.0)
        self.magic_size.setSuffix(' MB')
        magic.addWidget(self.magic_chk)
        magic.addWidget(self.magic_mode, 1)
        magic.addWidget(self.magic_size, 1)
        layout.addLayout(magic)
        sections.addWidget(experimental)
        sections.addStretch()
        actions = QHBoxLayout()
        actions.addWidget(label('参数在本次打开期间保留，重启后恢复默认值。'))
        actions.addStretch()
        actions.addWidget(button('完成', 'check', 'Primary', dlg.accept))
        outer.addLayout(actions)
        self.dur_chk.toggled.connect(self._sync_advanced)
        self.sar_chk.toggled.connect(self._sync_advanced)
        self.sar_preset.currentIndexChanged.connect(self._sync_advanced)
        self.magic_chk.toggled.connect(self._sync_advanced)
        self.settings_dlg = dlg
        self._sync_advanced()

    def _sync_advanced(self):
        self.dur_mode.setEnabled(self.dur_chk.isChecked())
        self.dur_sec.setEnabled(self.dur_chk.isChecked())
        self.sar_preset.setEnabled(self.sar_chk.isChecked())
        custom = self.sar_chk.isChecked() and self.sar_preset.currentIndex() == 2
        self.sar_w.setEnabled(custom)
        self.sar_h.setEnabled(custom)
        self.magic_mode.setEnabled(self.magic_chk.isChecked())
        self.magic_size.setEnabled(self.magic_chk.isChecked())

    def _start_gpu_probe(self):
        self.gpu_thread = GpuProbe(self)
        self.gpu_thread.detected.connect(self._on_gpu_detected)
        self.gpu_thread.finished.connect(self._gpu_probe_stopped)
        self.gpu_thread.start()

    @Slot(str)
    def _on_gpu_detected(self, name):
        self.gpu_label.setText(name)
        self.gpu_label.setToolTip(name + '\n编码需要可用的 NVIDIA NVENC 环境。')

    @Slot()
    def _gpu_probe_stopped(self):
        thread = self.gpu_thread
        self.gpu_thread = None
        self.gpu_probe = None
        if thread:
            thread.deleteLater()
        self._maybe_close()

    def _show_workspace(self):
        self.scroll.verticalScrollBar().setValue(0)

    def _show_logs(self):
        self.log_box.setFocus()
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _open_settings(self):
        self.settings_dlg.show()
        self.settings_dlg.raise_()
        self.settings_dlg.activateWindow()

    def _open_output(self):
        path = self.out_edit.text().strip()
        if not os.path.isdir(path):
            self._set_status('请先选择有效的输出目录。', 'error')
            self.out_edit.setFocus()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path))):
            self._set_status('无法打开输出目录，请检查文件夹权限。', 'error')

    def _res_changed(self, index):
        custom = RESOLUTIONS[index][1] == 0
        self.custom_size.setVisible(custom)
        self.cust_w.setEnabled(custom)
        self.cust_h.setEnabled(custom)
        self._update_summary()

    def _update_summary(self):
        _, width, height = RESOLUTIONS[self.res_combo.currentIndex()]
        if not width:
            width, height = self.cust_w.value(), self.cust_h.value()
        self.output_summary.setText(f'{width} × {height} px  ·  MP4 视频')

    def _pick_file(self, edit, title):
        patterns = ' '.join('*' + ext for ext in engine.VIDEO_EXTS)
        path, _ = QFileDialog.getOpenFileName(self, title, edit.text(), f'视频 ({patterns});;所有文件 (*)')
        if path:
            edit.setText(os.path.normpath(path))

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择输出目录', self.out_edit.text())
        if path:
            self.out_edit.setText(os.path.normpath(path))

    def _build_opts(self):
        opts = engine.AbOptions()
        opts.head = self.head_edit.text().strip()
        opts.material = self.mat_edit.text().strip()
        opts.out_dir = self.out_edit.text().strip()
        _, width, height = RESOLUTIONS[self.res_combo.currentIndex()]
        if not width:
            width, height = self.cust_w.value(), self.cust_h.value()
        opts.width, opts.height = width, height
        opts.intro = self.intro_frames.value()
        opts.live = self.live_frames.value()
        opts.mat = self.mat_frames.value()
        opts.batch = self.batch_chk.isChecked()
        opts.repeat = self.repeat_chk.isChecked()
        opts.repeats = self.repeats_spin.value()
        opts.duration_enabled = self.dur_chk.isChecked()
        opts.duration_mode = self.dur_mode.currentText()
        opts.duration_sec = self.dur_sec.value()
        opts.strip_sei = self.sei_chk.isChecked()
        opts.sar_enabled = self.sar_chk.isChecked()
        opts.sar_preset = self.sar_preset.currentText()
        opts.sar_w = self.sar_w.value()
        opts.sar_h = self.sar_h.value()
        opts.tool_tag = self.tag_combo.currentText()
        opts.compressor = self.comp_combo.currentText()
        opts.magic_enabled = self.magic_chk.isChecked()
        opts.magic_mode = self.magic_mode.currentText()
        opts.magic_size = self.magic_size.value()
        return opts

    def _set_status(self, text, state='idle'):
        self.status_label.setText(text)
        self.state_badge.setProperty('state', state)
        self.state_badge.setText({
            'idle': '●  准备就绪', 'running': '●  正在处理',
            'success': '●  已完成', 'error': '●  请检查', 'cancelled': '●  已取消',
        }.get(state, '●  准备就绪'))
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)
        self.state_badge.update()

    @Slot(str)
    def _append_log(self, message):
        self.log_box.appendPlainText(f'[{datetime.now():%H:%M:%S}]  {message}')

    def _clear_logs(self):
        self.log_box.clear()

    def _set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.start_btn.setText('正在生成…' if busy else '开始生成')
        self.input_card.setEnabled(not busy)
        self.params_card.setEnabled(not busy)
        self.settings_btn.setEnabled(not busy)
        self.settings_dlg.setEnabled(not busy)

    def _start(self):
        if self.thread is not None:
            return
        opts = self._build_opts()
        for path, control, message in (
            (opts.head, self.head_edit, '请选择有效的实拍视频。'),
            (opts.material, self.mat_edit, '请选择有效的素材视频。'),
        ):
            if not os.path.isfile(path):
                self._set_status(message, 'error')
                control.setFocus()
                self.scroll.ensureWidgetVisible(control)
                return
        if not os.path.isdir(opts.out_dir):
            self._set_status('请选择有效的输出目录。', 'error')
            self.out_edit.setFocus()
            return
        if opts.width % 2 or opts.height % 2:
            self._set_status('输出宽度和高度需为偶数，请调整自定义分辨率。', 'error')
            return
        if opts.live + opts.mat == 0:
            self._set_status('素材视频与插入实拍的帧数不能同时为 0。', 'error')
            return
        self.log_box.clear()
        self.progress.setValue(0)
        self._set_status('正在准备素材与编码器…', 'running')
        self._append_log(f'任务开始  ·  {opts.width} × {opts.height}  ·  60 FPS / NVENC')
        self._set_busy(True)
        self.worker = Worker(opts, self)
        self.thread = self.worker
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_finished)
        self.worker.finished.connect(self._thread_stopped)
        self.worker.start()

    @Slot(float, str)
    def _on_progress(self, progress, message):
        self.progress.setValue(max(0, min(100, int(progress * 100))))
        if message:
            self.status_label.setText(message)

    @Slot(int, int, str)
    def _on_finished(self, ok, fail, message):
        cancelled = message == '已取消'
        state = 'cancelled' if cancelled else ('error' if fail or message.startswith('错误') else 'success')
        self._set_status(message, state)
        self._append_log(message)
        if state == 'success':
            self.progress.setValue(100)
        self.cancel_btn.setEnabled(False)

    @Slot()
    def _thread_stopped(self):
        thread = self.thread
        self.thread = None
        self.worker = None
        if thread:
            thread.deleteLater()
        self._set_busy(False)
        self._maybe_close()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.status_label.setText('正在取消，请等待当前处理步骤结束…')
            self.cancel_btn.setEnabled(False)

    def _maybe_close(self):
        if self._close_pending and self.thread is None and self.gpu_thread is None:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.thread is not None:
            if not self._close_pending:
                choice = QMessageBox.question(
                    self, '任务仍在运行', '视频正在生成，是否取消任务并退出？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if choice != QMessageBox.Yes:
                    event.ignore()
                    return
                self._close_pending = True
                self._cancel()
            event.ignore()
            return
        if self.gpu_thread is not None:
            self._close_pending = True
            self.hide()
            event.ignore()
            return
        self.settings_dlg.close()
        event.accept()


def _startup_log(msg):
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, 'startup.log'), 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (datetime.now().strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def main():
    _startup_log('main start')
    # Give this application a separate Windows taskbar identity.
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CTH.VideoStudio.Roma')
        except (AttributeError, OSError):
            pass
    _startup_log('appuserid done')
    app = QApplication(sys.argv)
    _startup_log('QApplication created')
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName('神秘·X')
    app.setWindowIcon(app_icon())
    _startup_log('icon set')
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 9))
    _startup_log('style/font set')
    window = MainWindow()
    _startup_log('window created')
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(min(1360, available.width() - 24), min(940, available.height() - 48))
    window.show()
    _startup_log('window shown')
    code = app.exec()
    _startup_log('exec exit %d' % code)
    sys.exit(code)


if __name__ == '__main__':
    main()
