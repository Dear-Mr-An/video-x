# -*- coding: utf-8 -*-
"""Qt6 UI regressions; no GPU or video encoding required."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QMimeData, QUrl, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import main


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.head = self.directory / 'live.mp4'
        self.material = self.directory / 'material.mov'
        self.head.touch()
        self.material.touch()
        with patch.object(main.MainWindow, '_start_gpu_probe'):
            self.window = main.MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        if self.window.thread:
            self.window._cancel()
            self.wait_for(lambda: self.window.thread is None)
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), 'Timed out waiting for Qt signal')

    def valid_paths(self):
        self.window.head_edit.setText(str(self.head))
        self.window.mat_edit.setText(str(self.material))
        self.window.out_edit.setText(str(self.directory))

    def test_brand_icon_and_initial_options(self):
        w = self.window
        self.assertEqual(w.windowTitle(), '神秘·X')
        self.assertFalse(w.windowIcon().isNull())
        self.assertFalse(w.settings_dlg.isVisible())
        o = w._build_opts()
        self.assertEqual((o.width, o.height, o.intro, o.live, o.mat), (720, 1256, 23, 1, 1))
        self.assertFalse(o.duration_enabled or o.magic_enabled)
        self.assertFalse(w.cancel_btn.isEnabled())

    def test_resolution_and_repeat(self):
        w = self.window
        for index, (_, width, height) in enumerate(main.RESOLUTIONS[:-1]):
            w.res_combo.setCurrentIndex(index)
            self.assertEqual((w._build_opts().width, w._build_opts().height), (width, height))
        w.res_combo.setCurrentIndex(len(main.RESOLUTIONS) - 1)
        self.assertTrue(w.cust_w.isEnabled())
        w.cust_w.setValue(1080)
        w.cust_h.setValue(1920)
        self.assertEqual((w._build_opts().width, w._build_opts().height), (1080, 1920))
        self.assertIn('1080 × 1920', w.output_summary.text())
        self.assertFalse(w.repeats_spin.isEnabled())
        w.repeat_chk.setChecked(True)
        w.repeats_spin.setValue(3)
        self.assertTrue(w.repeats_spin.isEnabled())
        self.assertEqual(w._build_opts().repeats, 3)

    def test_advanced_options(self):
        w = self.window
        w.dur_chk.setChecked(True)
        w.dur_sec.setValue(500)
        w.sar_chk.setChecked(True)
        w.sar_preset.setCurrentIndex(2)
        w.sar_w.setValue(4)
        w.sar_h.setValue(3)
        w.sei_chk.setChecked(True)
        w.tag_combo.setEditText('custom-tool')
        w.comp_combo.setEditText('custom-encoder')
        w.magic_chk.setChecked(True)
        w.magic_size.setValue(123.456)
        o = w._build_opts()
        self.assertEqual((o.duration_enabled, o.duration_sec), (True, 500))
        self.assertEqual((o.sar_enabled, o.sar_w, o.sar_h), (True, 4, 3))
        self.assertEqual((o.magic_enabled, o.magic_size), (True, 123.456))
        self.assertEqual((o.tool_tag, o.compressor), ('custom-tool', 'custom-encoder'))
        self.assertTrue(o.strip_sei)
        self.assertTrue(w.dur_sec.isEnabled() and w.sar_w.isEnabled() and w.magic_size.isEnabled())
        w.sar_preset.setCurrentIndex(0)
        self.assertFalse(w.sar_w.isEnabled())

    def test_validation_without_opening_settings(self):
        w = self.window
        w.start_btn.click()
        self.assertIn('实拍视频', w.status_label.text())
        self.assertIsNone(w.thread)
        self.valid_paths()
        w.res_combo.setCurrentIndex(len(main.RESOLUTIONS) - 1)
        w.cust_w.setValue(721)
        w.start_btn.click()
        self.assertIn('偶数', w.status_label.text())
        w.cust_w.setValue(720)
        w.mat_frames.setValue(0)
        w.live_frames.setValue(0)
        w.start_btn.click()
        self.assertIn('不能同时为 0', w.status_label.text())
        self.assertIsNone(w.thread)

    def test_drop_path_validation(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.head))])
        self.assertEqual(Path(self.window.head_edit._drop_path(mime)), self.head)
        self.assertEqual(self.window.out_edit._drop_path(mime), '')
        mime.setUrls([QUrl.fromLocalFile(str(self.directory))])
        self.assertTrue(self.window.out_edit._drop_path(mime))
        self.assertEqual(self.window.mat_edit._drop_path(mime), '')
        mime.setUrls([QUrl('https://example.com/video.mp4')])
        self.assertEqual(self.window.head_edit._drop_path(mime), '')

    def test_small_window_and_navigation(self):
        w = self.window
        w.resize(1080, 700)
        self.app.processEvents()
        for control in (w.start_btn, w.cancel_btn, w.progress, w.log_box):
            point = control.mapTo(w, QPoint(0, 0))
            self.assertGreaterEqual(point.y(), 0)
            self.assertLessEqual(point.y() + control.height(), w.height())
        w.settings_btn.click()
        self.assertTrue(w.settings_dlg.isVisible())
        w.settings_dlg.accept()

    def test_output_folder_action(self):
        w = self.window
        w._open_output()
        self.assertEqual(w.state_badge.property('state'), 'error')
        self.valid_paths()
        with patch.object(main.QDesktopServices, 'openUrl', return_value=True) as opened:
            w._open_output()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), self.directory)

    def test_success_thread_lifecycle(self):
        class SuccessfulJob:
            def __init__(self, opts, log, progress, cancel):
                self.log, self.progress = log, progress

            def run(self):
                self.log('test task')
                self.progress(0.5, 'halfway')
                return 1, 0

        w = self.window
        self.valid_paths()
        with patch.object(main.engine, 'AbJob', SuccessfulJob):
            w.start_btn.click()
            self.assertFalse(w.start_btn.isEnabled())
            self.assertFalse(w.input_card.isEnabled())
            self.wait_for(lambda: w.thread is None)
        self.assertEqual(w.state_badge.property('state'), 'success')
        self.assertEqual(w.progress.value(), 100)
        self.assertIn('test task', w.log_box.toPlainText())
        self.assertTrue(w.start_btn.isEnabled() and w.input_card.isEnabled())
        self.assertFalse(w.cancel_btn.isEnabled())

    def test_cancel_thread_lifecycle(self):
        class CancellableJob:
            def __init__(self, opts, log, progress, cancel):
                self.cancel = cancel

            def run(self):
                while not self.cancel():
                    time.sleep(.01)
                return 0, 0  # Cancellation between files must not appear as success.

        w = self.window
        self.valid_paths()
        with patch.object(main.engine, 'AbJob', CancellableJob):
            w.start_btn.click()
            w.cancel_btn.click()
            self.wait_for(lambda: w.thread is None)
        self.assertEqual(w.state_badge.property('state'), 'cancelled')
        self.assertTrue(w.start_btn.isEnabled())
        self.assertEqual(w.progress.value(), 0)

    def test_failure_restores_controls(self):
        w = self.window
        self.valid_paths()
        with patch.object(main.engine.AbJob, 'run', side_effect=RuntimeError('test error')):
            w.start_btn.click()
            self.wait_for(lambda: w.thread is None)
        self.assertEqual(w.state_badge.property('state'), 'error')
        self.assertIn('test error', w.log_box.toPlainText())
        self.assertTrue(w.start_btn.isEnabled())


if __name__ == '__main__':
    unittest.main()
