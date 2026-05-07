"""
Main application window for GradientGUI v2.

Adds: Undo/Redo, multi-line support, multi-curve display,
      frame-accurate preview, proper curve→gradient wiring.
"""

from __future__ import annotations

import sys
import os
import copy
import json
import math
import re
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QComboBox, QDoubleSpinBox, QSpinBox,
    QPushButton, QFrame, QStatusBar, QMessageBox, QTabBar,
    QTabWidget, QFileDialog, QMenu, QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QPoint
from PySide6.QtGui import QFont, QColor, QAction, QShortcut, QKeySequence, QDesktopServices

from engine.ass_parser import ASSFile, ASSEvent, parse_ass_file, time_to_seconds
from engine.tag_parser import (
    parse_tags_from_text,
    get_tag_value,
    strip_tags,
    TAG_INFO,
    extract_clip_bounds,
    extract_clip_tags,
)
from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes
from engine.api import (
    GradientSettings,
    build_path_color_preview_stops_from_sampled_colors,
    generate_gradient,
    sample_path_points_from_path,
)
from engine.frame_sampler import FrameSampler
from engine.path_model import (
    PathSet,
    normalize_path_state,
    serialize_path_state,
)

from gui.video_preview import VideoPreview
from gui.curve_editor import CurveEditorWidget
from gui.color_sample_editor import ColorSampleEditorDialog
from gui.tag_panel import TagPanel
from gui.path_sampler import PathSamplerDialog
from gui.curve_keys import (
    curve_key,
    curve_key_axis,
    curve_key_tag,
    curve_keys_for_tag,
    curve_label,
)
from gui.group_range_dialog import GroupRangeSettingsDialog
from gui.range_debug_dialog import RangeDebugDialog
from gui.line_selection_panel import LineSelectionPanel
from gui.animation_panel import AnimationPanel
from gui.animation_overview import AnimationOverviewRow, AnimationOverviewWidget
from gui.tag_overview import TagOverviewRow, TagOverviewWidget
from gui.bounds_controller import BoundsController, BoundsRect
from gui.i18n import group_label, is_english, set_button_text, tag_label, toggle_language, tr
from gui.settings_builder import (
    build_base_settings,
    build_event_settings,
    current_video_time,
    resolve_video_path,
    sampling_paths_for_event,
)
from gui.gradient_generation import (
    GradientGenerationError,
    TRANSFORMABLE_ANIMATION_TAGS,
    build_preview_ass,
    generate_gradient_events,
)
from gui.preview_cache import PreviewGenerationCache, stable_preview_key
from gui import preset_io
from gui import state_codec
from gui import startup_profile
from gui.app_version import RELEASES_URL
from gui.dependency_health import HealthReport, run_startup_health_check
from gui.debug_overlay import debug_overlay_ass_events, rect_item, sampled_clip_shapes
from gui.undo_manager import UndoManager, UndoState
from gui.update_checker import UpdateInfo, check_for_updates_async
from engine.range_calc import (
    GEOMETRY_TAGS,
    RangeDebug,
    build_geometry_context as _range_build_geometry_context,
    calculate_range_plan,
    compute_dynamic_geometry_bounds as _range_compute_dynamic_geometry_bounds,
    coord_sample_points as _range_coord_sample_points,
    get_interpolated_value as _range_get_interpolated_value,
)


TAG_OVERVIEW_TAB = "__tag_overview__"
ANIMATION_OVERVIEW_TAB = "__animation_overview__"


class UpwardComboBox(QComboBox):
    """Combo box whose popup opens above the control."""

    def showPopup(self) -> None:
        super().showPopup()
        QTimer.singleShot(0, self._move_popup_above)

    def _move_popup_above(self) -> None:
        view = self.view()
        popup = view.window() if view is not None else None
        if popup is None or not popup.isVisible():
            return
        popup_height = max(popup.height(), popup.sizeHint().height(), self.height())
        popup_width = max(popup.width(), self.width())
        popup.resize(popup_width, popup_height)
        popup.move(self.mapToGlobal(QPoint(0, -popup_height)))


def _format_overview_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _clamp_byte(value: object) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(255, number))


# ── Style ─────────────────────────────────────────────────────────────────────

DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #0f0f23;
    color: #e0e0e0;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}
QLabel { color: #c0c0d0; }
QPushButton {
    background-color: #2a2a4a;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 4px 12px;
    color: #ddd;
}
QPushButton:hover { background-color: #3a3a6a; border-color: #666; }
QPushButton:pressed { background-color: #4a4aba; }
QPushButton:disabled { background-color: #1a1a2a; color: #666; }
QPushButton#applyBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4a90d9, stop:1 #6366f1);
    border: none; color: white; font-weight: bold; padding: 6px 20px;
}
QPushButton#applyBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #5aa0e9, stop:1 #7376ff);
}
QComboBox {
    background-color: #2a2a4a; border: 1px solid #444;
    border-radius: 3px; padding: 2px 6px; color: #ddd;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #2a2a4a; selection-background-color: #4a4a8a; color: #ddd;
}
QDoubleSpinBox, QSpinBox, QLineEdit {
    background-color: #1a1a3a; border: 1px solid #444;
    border-radius: 3px; padding: 2px 4px; color: #ddd;
}
QScrollBar:vertical { background: #1a1a2e; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 30px; }
QCheckBox { spacing: 4px; color: #c0c0d0; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #555;
    border-radius: 3px; background: #1a1a3a;
}
QCheckBox::indicator:checked { background: #6366f1; border-color: #6366f1; }
QFrame { border: none; }
QFrame#videoFrame { background: #000; border: 1px solid #111; }
QFrame#linePanel {
    background-color: #0f0f23;
    border-top: 1px solid #333;
}
QSplitter::handle { background: #333; }
QStatusBar { background: #0f0f23; color: #888; border-top: 1px solid #333; }
QListWidget {
    background: #1a1a3a; border: 1px solid #333; color: #ddd;
    font-size: 12px;
}
QListWidget::item:selected { background: #4a4a8a; }
"""


class MainWindow(QMainWindow):
    """Main GradientGUI window."""

    _update_available = Signal(object)

    def __init__(
        self,
        input_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ):
        super().__init__()
        self.setWindowTitle(tr("GradientGUI — 实时渐变编辑器"))
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)
        self.setStyleSheet(DARK_STYLE)

        self._input_path = input_path
        self._output_path = output_path
        self._ass_file: Optional[ASSFile] = None
        self._source_events: list[ASSEvent] = []  # multi-line
        self._active_event_idx: int = 0
        self._active_tag: Optional[str] = None
        self._active_curve_key: Optional[str] = None
        self._bounds = BoundsController()
        self._sampling_paths: dict[int, dict[str, PathSet]] = {}
        self._group_range_tags: set[str] = set(TAG_INFO.keys())
        self._path_frame_sampler = FrameSampler()
        self._startup_health: Optional[HealthReport] = None
        self._active_path_dialog: Optional[PathSamplerDialog] = None
        self._debug_overlay_enabled = False
        self._last_preview_debug_data = None
        self._last_preview_summary: dict[str, object] = {}
        self._last_preview_result_events: list[ASSEvent] = []
        self._preview_cache = PreviewGenerationCache(max_entries=48)
        self._last_preview_cache_hit = False
        self._loop_preview_native = False
        self._current_color_preview_stops: list[tuple[float, str]] = []
        self._current_color_sample_colors: list[tuple[int, str]] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._do_preview)
        self._loop_preview_timer = QTimer(self)
        self._loop_preview_timer.timeout.connect(self._advance_loop_preview_frame)
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(350)
        self._undo_timer.timeout.connect(self._push_pending_undo)
        self._pending_undo_description = "编辑"
        self._last_undo_signature: Optional[str] = None
        self._undo_suspended = True

        # Per-tag curve data
        self._tag_curves: dict[str, list[CurveNode]] = {}
        self._tag_modes: dict[str, InterpolationMode] = {}
        self._curve_mirrors: dict[str, tuple[bool, bool]] = {}
        self._animation_curves: dict[str, list[CurveNode]] = {}
        self._animation_modes: dict[str, InterpolationMode] = {}
        self._animation_curve_mirrors: dict[str, tuple[bool, bool]] = {}
        self._animation_enabled_tags: set[str] = set()
        self._animation_frame_steps: dict[str, int] = {}
        self._animation_seam_blend_lengths: dict[str, int] = {}
        self._overview_tab_active: Optional[str] = None
        self._syncing_curve_editor = False
        self._syncing_animation_editor = False
        self._update_check_thread = None
        self._update_dialog_shown = False
        self._update_dialog = None

        # Undo/Redo
        self._undo = UndoManager()
        self._undo.set_change_callback(self._update_undo_buttons)
        self._update_available.connect(self._on_update_available)

        with startup_profile.block("MainWindow.build_ui"):
            self._build_ui()
        self._bounds.set_status_callback(self._status.showMessage)
        self._setup_shortcuts()
        startup_profile.mark("MainWindow.shortcuts ready")
        with startup_profile.block("MainWindow.load_input"):
            self._load_input()
        self._undo_suspended = False
        QTimer.singleShot(1500, self._start_update_check)

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        startup_profile.mark("build_ui.header")

        # Top splitter: video | tag panel
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: video + line selector
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        video_frame = QFrame()
        video_frame.setObjectName("videoFrame")
        video_frame_layout = QVBoxLayout(video_frame)
        video_frame_layout.setContentsMargins(0, 0, 0, 0)
        video_frame_layout.setSpacing(0)

        self._video = VideoPreview()
        video_frame_layout.addWidget(self._video)
        left_layout.addWidget(video_frame, stretch=1)

        self._line_panel = LineSelectionPanel()
        self._line_panel.active_line_changed.connect(self._on_active_line_changed)
        self._line_panel.selection_changed.connect(self._on_line_selection_changed)
        self._line_panel.merge_changed.connect(self._schedule_preview)
        self._line_panel.settings_requested.connect(self._open_group_range_settings)
        left_layout.addWidget(self._line_panel, stretch=0)

        top_splitter.addWidget(left_widget)
        startup_profile.mark("build_ui.video_and_line_panel")

        self._tag_panel = TagPanel()
        self._tag_panel.setMinimumWidth(350)
        self._tag_panel.setMaximumWidth(500)
        self._language_btn = QPushButton(tr("EN / 中"))
        self._language_btn.setToolTip(tr("切换界面语言"))
        self._language_btn.clicked.connect(self._toggle_language)
        self._tag_panel.set_color_space_trailing_widget(self._language_btn)
        self._tag_panel.tag_changed.connect(self._on_tag_changed)
        self._tag_panel.tag_selected.connect(self._on_tag_selected)
        self._tag_panel.path_sample_requested.connect(self._on_path_sample_requested)
        top_splitter.addWidget(self._tag_panel)

        top_splitter.setChildrenCollapsible(False)
        top_splitter.setSizes([750, 400])
        startup_profile.mark("build_ui.tag_panel")

        # Curve editor
        curve_layout = QVBoxLayout()
        curve_layout.setContentsMargins(0, 0, 0, 0)
        curve_layout.setSpacing(4)
        
        self._curve_tabs = QTabBar()
        self._curve_tabs.setExpanding(False)
        self._curve_tabs.setFixedHeight(30)
        self._curve_tabs.currentChanged.connect(self._on_tab_changed_ui)
        curve_layout.addWidget(self._curve_tabs)

        self._editor_tabs = QTabWidget()
        self._editor_tabs.setDocumentMode(True)

        curve_page = QWidget()
        curve_page_layout = QVBoxLayout(curve_page)
        curve_page_layout.setContentsMargins(0, 0, 0, 0)
        curve_page_layout.setSpacing(4)
        self._curve_editor = CurveEditorWidget()
        self._curve_editor.curve_changed.connect(self._on_curve_changed)
        self._curve_editor.mirror_changed.connect(self._on_curve_mirror_changed)
        self._curve_editor.sample_editor_requested.connect(self._edit_sampled_colors)
        curve_page_layout.addWidget(self._curve_editor, stretch=1)
        self._editor_tabs.addTab(curve_page, tr("曲线编辑器"))
        startup_profile.mark("build_ui.curve_editor")

        animation_page = QWidget()
        animation_page_layout = QVBoxLayout(animation_page)
        animation_page_layout.setContentsMargins(0, 0, 0, 0)
        animation_page_layout.setSpacing(4)

        self._animation_panel = AnimationPanel()
        self._animation_panel.settings_changed.connect(self._on_animation_settings_changed)
        self._animation_panel.preview_frame_changed.connect(self._on_preview_frame_changed)
        self._animation_panel.loop_play_toggled.connect(self._on_loop_play_toggled)
        self._line_panel.set_header_trailing_widget(
            self._animation_panel.preview_controls_widget()
        )
        animation_page_layout.addWidget(self._animation_panel, stretch=0)

        self._animation_curve_editor = CurveEditorWidget(
            title="动画移动曲线",
            show_sample_editor=False,
            integer_x=True,
        )
        self._animation_curve_editor.curve_changed.connect(self._on_animation_curve_changed)
        self._animation_curve_editor.mirror_changed.connect(self._on_animation_curve_mirror_changed)
        animation_page_layout.addWidget(self._animation_curve_editor, stretch=1)
        self._editor_tabs.addTab(animation_page, tr("动画编辑器"))
        startup_profile.mark("build_ui.animation_editor")

        self._editor_stack = QStackedWidget()
        self._editor_stack.addWidget(self._editor_tabs)

        self._tag_overview_page = QWidget()
        tag_overview_layout = QVBoxLayout(self._tag_overview_page)
        tag_overview_layout.setContentsMargins(0, 0, 0, 0)
        tag_overview_layout.setSpacing(4)
        self._tag_overview = TagOverviewWidget()
        tag_overview_layout.addWidget(self._tag_overview, stretch=1)
        self._editor_stack.addWidget(self._tag_overview_page)

        self._overview_page = QWidget()
        overview_page_layout = QVBoxLayout(self._overview_page)
        overview_page_layout.setContentsMargins(0, 0, 0, 0)
        overview_page_layout.setSpacing(4)
        self._animation_overview = AnimationOverviewWidget()
        overview_page_layout.addWidget(self._animation_overview, stretch=1)
        self._editor_stack.addWidget(self._overview_page)
        startup_profile.mark("build_ui.overview_pages")

        curve_layout.addWidget(self._editor_stack, stretch=1)
        
        curve_container = QWidget()
        curve_container.setLayout(curve_layout)
        self._curve_container = curve_container

        self._work_splitter = QSplitter(Qt.Orientation.Vertical)
        self._work_splitter.setChildrenCollapsible(False)
        self._work_splitter.addWidget(top_splitter)
        self._work_splitter.addWidget(self._curve_container)
        self._work_splitter.setSizes([560, 330])
        main_layout.addWidget(self._work_splitter, stretch=1)

        self._sync_tabs()
        startup_profile.mark("build_ui.splitters_and_tabs")

        # Bottom controls
        controls = QHBoxLayout()
        controls.setSpacing(12)

        self._mode_label = QLabel(tr("模式:"))
        controls.addWidget(self._mode_label)
        self._mode_combo = UpwardComboBox()
        self._mode_combo.addItems(["Horizontal", "Vertical", "Angled", "GBC"])
        self._mode_combo.setFixedWidth(110)
        self._mode_combo.currentTextChanged.connect(self._on_direction_changed)
        controls.addWidget(self._mode_combo)

        self._angle_label = QLabel(tr("角度 (°):"))
        controls.addWidget(self._angle_label)
        self._angle_spin = QDoubleSpinBox()
        self._angle_spin.setRange(-360, 360)
        self._angle_spin.setValue(0)
        self._angle_spin.setFixedWidth(80)
        self._angle_spin.valueChanged.connect(self._on_direction_changed)
        controls.addWidget(self._angle_spin)

        self._step_label = QLabel(tr("步长 (px):"))
        controls.addWidget(self._step_label)
        self._step_spin = QDoubleSpinBox()
        self._step_spin.setRange(0.05, 100)
        self._step_spin.setValue(1.0)
        self._step_spin.setSingleStep(0.5)
        self._step_spin.setFixedWidth(70)
        self._step_spin.valueChanged.connect(self._on_step_changed)
        controls.addWidget(self._step_spin)

        controls.addStretch()

        # Presets + Undo / Redo
        self._save_preset_btn = QPushButton(tr("保存预设"))
        self._save_preset_btn.setFixedWidth(82)
        self._save_preset_btn.clicked.connect(self._save_preset)
        controls.addWidget(self._save_preset_btn)

        self._load_preset_btn = QPushButton(tr("加载预设"))
        self._load_preset_btn.setFixedWidth(82)
        self._load_preset_btn.clicked.connect(self._load_preset)
        controls.addWidget(self._load_preset_btn)

        self._recent_preset_btn = QPushButton(tr("最近预设"))
        self._recent_preset_btn.setFixedWidth(108)
        self._recent_preset_menu = QMenu(self)
        self._recent_preset_btn.setMenu(self._recent_preset_menu)
        controls.addWidget(self._recent_preset_btn)

        self._undo_btn = QPushButton(tr("↩ 撤销"))
        self._undo_btn.setFixedWidth(82)
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._do_undo)
        controls.addWidget(self._undo_btn)

        self._redo_btn = QPushButton(tr("↪ 重做"))
        self._redo_btn.setFixedWidth(82)
        self._redo_btn.setEnabled(False)
        self._redo_btn.clicked.connect(self._do_redo)
        controls.addWidget(self._redo_btn)

        self._debug_overlay_btn = QPushButton(tr("调试覆盖"))
        self._debug_overlay_btn.setCheckable(True)
        self._debug_overlay_btn.setFixedWidth(86)
        self._debug_overlay_btn.toggled.connect(self._on_debug_overlay_toggled)
        controls.addWidget(self._debug_overlay_btn)

        self._range_debug_btn = QPushButton(tr("范围调试"))
        self._range_debug_btn.setFixedWidth(82)
        self._range_debug_btn.clicked.connect(self._open_range_debug_dialog)
        controls.addWidget(self._range_debug_btn)

        self._preview_btn = QPushButton(tr("预览刷新"))
        self._preview_btn.setFixedWidth(86)
        self._preview_btn.clicked.connect(self._do_preview)
        controls.addWidget(self._preview_btn)

        self._report_btn = QPushButton(tr("导出调试包"))
        self._report_btn.setFixedWidth(86)
        self._report_btn.clicked.connect(self._export_debug_report)
        controls.addWidget(self._report_btn)

        self._apply_btn = QPushButton(tr("应用并关闭"))
        self._apply_btn.setObjectName("applyBtn")
        self._apply_btn.setFixedWidth(126)
        self._apply_btn.clicked.connect(self._apply_and_close)
        controls.addWidget(self._apply_btn)

        self._cancel_btn = QPushButton(tr("取消"))
        self._cancel_btn.setFixedWidth(82)
        self._cancel_btn.clicked.connect(self._cancel)
        controls.addWidget(self._cancel_btn)
        self._retranslate_command_buttons()

        main_layout.addLayout(controls)
        startup_profile.mark("build_ui.bottom_controls")

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage(tr("就绪"))
        self._refresh_recent_presets_menu()
        startup_profile.mark("build_ui.status_and_recent")

    def _toggle_language(self) -> None:
        toggle_language()
        self._retranslate_ui()

    def _start_update_check(self) -> None:
        if self._update_check_thread is not None:
            return
        self._update_check_thread = check_for_updates_async(
            lambda update: self._update_available.emit(update)
        )

    def _on_update_available(self, update: object) -> None:
        if self._update_dialog_shown or not isinstance(update, UpdateInfo):
            return
        self._update_dialog_shown = True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("发现新版本"))
        box.setText(tr("发现 GradientGUI 新版本。"))
        box.setInformativeText(
            f"{tr('最新版本')}: {update.version}\n{tr('发布页面')}: {update.url}"
        )
        open_button = box.addButton(tr("打开发布页面"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("稍后"), QMessageBox.ButtonRole.RejectRole)
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(update.url or RELEASES_URL))
        )
        box.finished.connect(lambda _result: setattr(self, "_update_dialog", None))
        box.setWindowModality(Qt.WindowModality.NonModal)
        self._update_dialog = box
        box.show()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(tr("GradientGUI — 实时渐变编辑器"))
        self._language_btn.setText(tr("EN / 中"))
        self._language_btn.setToolTip(tr("切换界面语言"))
        self._editor_tabs.setTabText(0, tr("曲线编辑器"))
        self._editor_tabs.setTabText(1, tr("动画编辑器"))
        self._mode_label.setText(tr("模式:"))
        self._angle_label.setText(tr("角度 (°):"))
        self._step_label.setText(tr("步长 (px):"))
        self._retranslate_command_buttons()
        self._video.retranslate_ui()
        self._line_panel.retranslate_ui()
        self._tag_panel.retranslate_ui()
        self._curve_editor.retranslate_ui()
        self._animation_panel.retranslate_ui()
        self._animation_curve_editor.retranslate_ui()
        self._tag_overview.retranslate_ui()
        self._animation_overview.retranslate_ui()
        self._sync_tabs()
        self._sync_tag_overview(save_active=False)
        self._sync_animation_overview(save_active=False)
        self._refresh_recent_presets_menu()
        if self._last_preview_summary:
            self._show_preview_status()
        else:
            self._status.showMessage(tr("就绪"))

    def _retranslate_command_buttons(self) -> None:
        set_button_text(self._language_btn, "EN / 中", minimum=70, padding=22)
        set_button_text(self._save_preset_btn, "保存预设", minimum=64, padding=28)
        set_button_text(self._load_preset_btn, "加载预设", minimum=64, padding=28)
        set_button_text(self._recent_preset_btn, "最近预设", minimum=104, padding=44)
        set_button_text(self._undo_btn, "↩ 撤销", minimum=72, padding=28)
        set_button_text(self._redo_btn, "↪ 重做", minimum=72, padding=28)
        set_button_text(self._debug_overlay_btn, "调试覆盖", minimum=76, padding=28)
        set_button_text(self._range_debug_btn, "范围调试", minimum=72, padding=28)
        set_button_text(self._preview_btn, "预览刷新", minimum=72, padding=28)
        set_button_text(self._report_btn, "导出调试包", minimum=72, padding=28)
        set_button_text(self._apply_btn, "应用并关闭", minimum=104, padding=32)
        set_button_text(self._cancel_btn, "取消", minimum=64, padding=28)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self, self._do_undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._do_redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self._do_redo)
        QShortcut(QKeySequence("Alt+Left"), self, self._step_preview_frame_backward)
        QShortcut(QKeySequence("Alt+Right"), self, self._step_preview_frame_forward)

    # ── Data loading ─────────────────────────────────────────────────────

    def _load_input(self):
        if not self._input_path or not os.path.exists(self._input_path):
            self._status.showMessage(tr("未找到输入文件"))
            return

        try:
            with startup_profile.block("load_input.parse_ass"):
                self._ass_file = parse_ass_file(self._input_path)
        except Exception as e:
            self._status.showMessage(f"{tr('解析 ASS 失败')}: {e}")
            return

        # Collect all dialogue events (multi-line support)
        self._source_events = [evt for evt in self._ass_file.events if not evt.comment]
        if not self._source_events:
            self._status.showMessage(tr("未找到 Dialogue 行"))
            return

        self._preview_cache.clear()
        with startup_profile.block("load_input.bounds_and_lines"):
            self._bounds.set_source(self._ass_file, self._source_events)
            self._line_panel.set_events(self._source_events)

        # Load first line
        self._active_event_idx = 0
        with startup_profile.block("load_input.load_event_tags"):
            self._load_event_tags(0)

        # Load video with frame number
        video_path = self._ass_file.video_file
        frame_num = self._ass_file.video_position
        evt = self._source_events[0]
        seek_time = time_to_seconds(evt.start)
        resolved_video_path = self._resolve_video_path()

        with startup_profile.block("load_input.health_check"):
            health = run_startup_health_check(
                video_path=resolved_video_path,
                video_time=seek_time,
                video_frame=frame_num,
                frame_sampler=self._path_frame_sampler,
                check_video_frame=False,
            )
        self._startup_health = health
        self._status.showMessage(health.to_status_text())
        if not health.ok:
            QMessageBox.warning(self, tr("依赖健康检查"), health.to_message())

        def _try_load(path: str):
            with startup_profile.block("load_input.mpv_init"):
                self._video.init_mpv()
            with startup_profile.block("load_input.mpv_load_video"):
                self._video.load_video(path, seek_time=seek_time, frame_number=frame_num)

        if resolved_video_path:
            _try_load(resolved_video_path)
        elif video_path:
            self._status.showMessage(f"{tr('视频文件未找到')}: {video_path}")
        with startup_profile.block("load_input.preview_frame_setup"):
            self._update_preview_frame_range()
            self._reset_preview_frame_to_aegisub_position()

        # Push initial undo state
        with startup_profile.block("load_input.initial_undo"):
            self._push_undo(tr("初始状态"))
    def _load_event_tags(self, idx: int):
        """Load tag defaults from event at given index."""
        if idx < 0 or idx >= len(self._source_events):
            return
        evt = self._source_events[idx]
        parsed = parse_tags_from_text(evt.text)
        style = self._ass_file.get_style(evt.style) if self._ass_file else None
        self._tag_panel.set_defaults_from_parsed(parsed, style)
        if idx not in self._sampling_paths:
            _, clips = extract_clip_tags(evt.text)
            if clips:
                self._sampling_paths[idx] = {
                    tag: PathSet.from_ass_path(path)
                    for tag, path in clips.items()
                }
        self._refresh_path_buttons()
        if self._last_preview_summary:
            self._show_preview_status()
        else:
            self._status.showMessage(
                f"{tr('已加载行')} {idx+1}: {evt.style} | {evt.start} → {evt.end}"
            )

    # ── Event handlers ───────────────────────────────────────────────────

    def _on_active_line_changed(self, idx: int):
        if idx < 0:
            return
        if idx == self._active_event_idx:
            return
        self._active_event_idx = idx
        self._load_event_tags(idx)
        if self._loop_preview_timer.isActive():
            self._loop_preview_timer.stop()
            if self._loop_preview_native:
                self._video.stop_loop_playback(pause=True)
            self._loop_preview_native = False
            self._animation_panel.set_loop_playing(False)
        self._update_preview_frame_range()
        self._reset_preview_frame_to_aegisub_position()
        self._schedule_preview()

    def _on_line_selection_changed(self):
        if not self._selected_event_indices(fallback=False):
            self._line_panel.select_current(self._active_event_idx, emit=False)
        self._schedule_preview()

    def _open_group_range_settings(self):
        enabled_tags = self._tag_panel.get_enabled_tags()
        if not enabled_tags:
            QMessageBox.information(
                self,
                tr("整体范围设置"),
                tr("当前没有启用的 tag。"),
            )
            return

        dialog = GroupRangeSettingsDialog(
            enabled_tags=enabled_tags,
            selected_tags=self._group_range_tags,
            parent=self,
        )
        if dialog.exec() != GroupRangeSettingsDialog.DialogCode.Accepted:
            return

        selected_tags = dialog.selected_tags()
        enabled_tag_set = set(enabled_tags)
        if selected_tags == (self._group_range_tags & enabled_tag_set):
            return

        self._group_range_tags = selected_tags | (self._group_range_tags - enabled_tag_set)
        self._push_undo(tr("整体范围设置"))
        self._schedule_preview()

    def _sync_cached_curve_from_row(self, tag: str, row) -> None:
        cfg = row.get_config()
        if row.tag_type == "coord":
            v_start = row._inputs[0].value()
            v_end = row._inputs[-1].value()
            start_xy = v_start if isinstance(v_start, tuple) else (0.0, 0.0)
            end_xy = v_end if isinstance(v_end, tuple) else start_xy
            for axis, idx in (("x", 0), ("y", 1)):
                key = curve_key(tag, axis)
                cached_nodes = self._tag_curves.get(key)
                if cached_nodes and len(cached_nodes) >= 2:
                    nodes = self._clone_nodes(cached_nodes)
                else:
                    source_nodes = cfg.nodes if axis == "x" else cfg.coord_y_nodes
                    nodes = (
                        self._clone_nodes(source_nodes)
                        if source_nodes and len(source_nodes) >= 2
                        else make_default_nodes(
                            start_y=float(start_xy[idx]),
                            end_y=float(end_xy[idx]),
                        )
                    )
                nodes[0].y = float(start_xy[idx])
                nodes[-1].y = float(end_xy[idx])
                self._tag_curves[key] = nodes
                self._tag_modes[key] = cfg.coord_y_mode if axis == "y" else cfg.mode
            return

        v_start = row._inputs[0].value()
        v_end = row._inputs[-1].value()
        key = curve_key(tag)
        if row.tag_type == "color":
            nodes = make_default_nodes(
                start_color=str(v_start),
                end_color=str(v_end),
            )
        else:
            cached_nodes = self._tag_curves.get(key)
            if cached_nodes and len(cached_nodes) >= 2:
                nodes = self._clone_nodes(cached_nodes)
            else:
                nodes = self._clone_nodes(cfg.nodes) if cfg.nodes and len(cfg.nodes) >= 2 else []
                if len(nodes) < 2:
                    if row.tag_type == "text":
                        nodes = make_default_nodes(start_y=0.0, end_y=0.0)
                    else:
                        nodes = make_default_nodes(
                            start_y=float(v_start),
                            end_y=float(v_end),
                        )

        if row.tag_type in {"color", "text"}:
            nodes[0].value_str = str(v_start)
            nodes[-1].value_str = str(v_end)
        else:
            nodes[0].y = float(v_start)
            nodes[-1].y = float(v_end)

        self._tag_curves[key] = nodes
        self._tag_modes[key] = cfg.mode

    def _on_tag_changed(self, tag: str):
        self._sync_tabs()
        row = self._tag_panel.get_row(tag)
        if row and not getattr(row, '_syncing', False):
            self._sync_cached_curve_from_row(tag, row)

            if tag == self._active_tag and self._active_curve_key:
                nodes = self._tag_curves.get(self._active_curve_key)
                self._syncing_curve_editor = True
                try:
                    if nodes:
                        tag_type = TAG_INFO.get(tag, {}).get("type")
                        self._curve_editor.set_nodes(
                            nodes,
                            tag_type == "color",
                            tag_type == "text",
                        )
                        self._curve_editor.set_mode(
                            self._tag_modes.get(
                                self._active_curve_key,
                                InterpolationMode.LINEAR,
                            )
                        )
                        self._curve_editor.set_mirror_state(
                            *self._curve_mirrors.get(
                                self._active_curve_key,
                                (False, False),
                            )
                        )
                finally:
                    self._syncing_curve_editor = False
        if tag in {"1c", "2c", "3c", "4c"} and tag == curve_key_tag(self._active_curve_key):
            self._update_curve_color_preview()
        self._sync_tag_overview(save_active=False)
        self._schedule_preview()

    def _on_tag_selected(self, tag: str):
        axis = curve_key_axis(self._active_curve_key)
        if self._active_tag != tag or axis not in {"x", "y"}:
            axis = "x" if TAG_INFO.get(tag, {}).get("type") == "coord" else None
        key = curve_key(tag, axis)
        self._show_editor_tabs()
        idx = self._tab_index_for_key(key)
        if idx >= 0 and self._curve_tabs.currentIndex() != idx:
            self._curve_tabs.blockSignals(True)
            self._curve_tabs.setCurrentIndex(idx)
            self._curve_tabs.blockSignals(False)
        self._on_curve_key_selected(key)

    def _on_curve_key_selected(self, key: str):
        # Save current curve
        if self._active_curve_key:
            self._save_active_curve()
            self._save_active_animation_curve()
            self._save_active_animation_panel_state()

        tag = curve_key_tag(key) or key
        axis = curve_key_axis(key)
        self._active_tag = tag
        self._active_curve_key = key
        for t, row_widget in self._tag_panel._rows.items():
            row_widget.set_active(t == tag)

        tag_type = TAG_INFO.get(tag, {}).get("type")
        is_color = tag_type == "color"
        is_text = tag_type == "text"

        # Load this tag's curve
        self._syncing_curve_editor = True
        try:
            if key in self._tag_curves:
                self._curve_editor.set_nodes(self._tag_curves[key], is_color, is_text)
                self._curve_editor.set_mode(
                    self._tag_modes.get(key, InterpolationMode.LINEAR)
                )
            else:
                row = self._tag_panel.get_row(tag)
                if row:
                    cfg = row.get_config()
                    if row.tag_type == "coord" and axis == "y":
                        nodes = cfg.coord_y_nodes or make_default_nodes()
                        mode = cfg.coord_y_mode
                    else:
                        nodes = cfg.nodes
                        mode = cfg.mode
                else:
                    nodes = make_default_nodes()
                    mode = InterpolationMode.LINEAR
                self._curve_editor.set_nodes(nodes, is_color, is_text)
                self._curve_editor.set_mode(mode)
            self._curve_editor.set_mirror_state(*self._curve_mirrors.get(key, (False, False)))
        finally:
            self._syncing_curve_editor = False
        self._update_curve_color_preview()
        self._sync_animation_curve_editor()

        if self._last_preview_summary:
            self._show_preview_status()
        else:
            self._status.showMessage(f"曲线编辑: {curve_label(key)}")

    def _on_curve_changed(self):
        if self._syncing_curve_editor:
            return
        if self._active_curve_key:
            self._save_active_curve()
            # Sync values back to tag panel
            nodes = self._tag_curves.get(self._active_curve_key)
            if nodes and len(nodes) >= 2:
                tag = curve_key_tag(self._active_curve_key) or self._active_curve_key
                axis = curve_key_axis(self._active_curve_key)
                row = self._tag_panel.get_row(tag)
                if row:
                    row._syncing = True
                    if row.tag_type == "color":
                        row.set_start_value(nodes[0].value_str)
                        row.set_end_value(nodes[-1].value_str)
                    elif row.tag_type == "text":
                        row.set_start_value(nodes[0].value_str)
                        row.set_end_value(nodes[-1].value_str)
                    elif row.tag_type == "coord":
                        start_xy = row._inputs[0].value()
                        end_xy = row._inputs[-1].value()
                        if not isinstance(start_xy, tuple):
                            start_xy = (0.0, 0.0)
                        if not isinstance(end_xy, tuple):
                            end_xy = start_xy
                        if axis == "y":
                            row.set_start_value((start_xy[0], nodes[0].y))
                            row.set_end_value((end_xy[0], nodes[-1].y))
                        else:
                            row.set_start_value((nodes[0].y, start_xy[1]))
                            row.set_end_value((nodes[-1].y, end_xy[1]))
                    else:
                        row.set_start_value(nodes[0].y)
                        row.set_end_value(nodes[-1].y)
                    row._syncing = False
        self._sync_tag_overview(save_active=False)
        self._schedule_preview()

    def _on_curve_mirror_changed(self, horizontal: bool, vertical: bool):
        if not self._active_curve_key:
            return
        self._curve_mirrors[self._active_curve_key] = (horizontal, vertical)
        self._save_active_curve()
        self._update_curve_color_preview()
        self._sync_tag_overview(save_active=False)
        self._schedule_preview()

    def _on_path_sample_requested(self, tag: str):
        if tag not in {"1c", "2c", "3c", "4c"}:
            return
        if not self._ass_file:
            QMessageBox.warning(self, tr("路径采色"), tr("未加载 ASS 文件。"))
            return

        video_path = self._resolve_video_path()
        evt = self._get_active_event()
        frame_image = None
        loaded_frame_key = None
        preview_frame = self._current_preview_video_frame()
        if video_path and evt:
            if self._load_path_preview_frame(video_path, evt):
                frame_image = self._path_frame_sampler.get_image_copy()
                loaded_frame_key = self._path_frame_sampler.frame_cache_key()

        active_paths = self._sampling_paths.setdefault(self._active_event_idx, {})
        if tag in active_paths:
            initial_path = active_paths[tag].to_ass_path()
        elif evt:
            _, clips = extract_clip_tags(evt.text)
            initial_path = clips.get(tag, "")
        else:
            initial_path = ""

        dialog = PathSamplerDialog(
            tag=tag,
            frame_image=frame_image,
            canvas_size=(self._ass_file.play_res_x, self._ass_file.play_res_y),
            initial_path=initial_path,
            frame_number=preview_frame if frame_image is not None else None,
            parent=self,
        )
        self._active_path_dialog = dialog
        try:
            result = dialog.exec()
        finally:
            if self._active_path_dialog is dialog:
                self._active_path_dialog = None
        if result != PathSamplerDialog.DialogCode.Accepted:
            return

        if dialog.remove_requested:
            # This explicit removal suppresses any original xlip path that may
            # already exist in the source ASS line.
            active_paths[tag] = PathSet.removed()
            self._reset_color_tag_after_path_removed(tag)
            if self._last_preview_summary:
                self._show_preview_status()
            else:
                self._status.showMessage(f"{tr('已移除')} \\{tag} {tr('路径采色')}")
        else:
            sample_frame = dialog.sampling_frame_number
            if sample_frame is None or sample_frame < 0:
                sample_frame = preview_frame if preview_frame >= 0 else None
            sampled_path = dialog.path()
            if not sampled_path.strip():
                active_paths[tag] = PathSet.removed()
                self._reset_color_tag_after_path_removed(tag)
                if self._last_preview_summary:
                    self._show_preview_status()
                else:
                    self._status.showMessage(f"{tr('已清空')} \\{tag} {tr('路径采色')}")
                self._refresh_path_buttons()
                self._sync_tag_overview(save_active=False)
                self._push_undo(f"路径采色 \\{tag}")
                self._schedule_preview()
                return
            path_state = PathSet.from_ass_path(
                sampled_path,
                sampling_frame=sample_frame,
            )
            path_state.sampled_points = self._sample_path_points(
                sampled_path,
                sample_frame,
                frame_image=frame_image,
                frame_key=loaded_frame_key,
            )
            active_paths[tag] = path_state
            self._reset_color_tag_curve_to_source_default(tag)
            for target_tag, target_path in dialog.applied_paths.items():
                target_state = PathSet.from_ass_path(
                    target_path,
                    sampling_frame=sample_frame,
                )
                target_state.sampled_points = self._sample_path_points(
                    target_path,
                    sample_frame,
                    frame_image=frame_image,
                    frame_key=loaded_frame_key,
                )
                active_paths[target_tag] = target_state
                self._reset_color_tag_curve_to_source_default(target_tag)
                target_row = self._tag_panel.get_row(target_tag)
                if target_row:
                    target_row.set_enabled_checked(True)
            row = self._tag_panel.get_row(tag)
            if row:
                row.set_enabled_checked(True)
            self._on_tag_selected(tag)
            if self._last_preview_summary:
                self._show_preview_status()
            else:
                self._status.showMessage(f"{tr('已设置')} \\{tag} {tr('路径采色')}")

        self._refresh_path_buttons()
        self._sync_tag_overview(save_active=False)
        self._push_undo(f"路径采色 \\{tag}")
        self._schedule_preview()

    def _source_default_for_tag(self, tag: str):
        evt = self._get_active_event()
        if not evt or not self._ass_file:
            return "FFFFFF" if tag in {"1c", "2c", "3c", "4c"} else 0
        parsed = parse_tags_from_text(evt.text)
        style = self._ass_file.get_style(evt.style)
        return get_tag_value(tag, parsed, style)

    def _reset_color_tag_curve_to_source_default(self, tag: str):
        if tag not in {"1c", "2c", "3c", "4c"}:
            return
        default = self._source_default_for_tag(tag)
        if not isinstance(default, str):
            default = "FFFFFF"
        nodes = make_default_nodes(start_color=default, end_color=default)
        key = curve_key(tag)
        self._tag_curves[key] = nodes
        self._tag_modes[key] = InterpolationMode.LINEAR
        row = self._tag_panel.get_row(tag)
        if row:
            row._syncing = True
            row.set_start_value(default)
            row.set_end_value(default)
            row._config.nodes = [copy.copy(node) for node in nodes]
            row._config.mode = InterpolationMode.LINEAR
            row._syncing = False
        if self._active_curve_key == key:
            self._curve_editor.set_nodes(nodes, True)
            self._curve_editor.set_mode(InterpolationMode.LINEAR)

    def _reset_color_tag_after_path_removed(self, tag: str):
        self._reset_color_tag_curve_to_source_default(tag)
        row = self._tag_panel.get_row(tag)
        if row:
            row.set_enabled_checked(False)
        key = curve_key(tag)
        if self._active_curve_key == key:
            self._current_color_preview_stops = []
            self._current_color_sample_colors = []
            self._curve_editor.set_color_preview_stops(None)

    def _refresh_path_buttons(self):
        active_paths = self._sampling_paths.get(self._active_event_idx, {})
        for tag in ("1c", "2c", "3c", "4c"):
            row = self._tag_panel.get_row(tag)
            if row:
                path_set = active_paths.get(tag)
                row.set_path_active(bool(path_set and path_set.is_active))

    def _current_gradient_direction(self) -> tuple[float, float]:
        if not hasattr(self, "_mode_combo") or not hasattr(self, "_angle_spin"):
            return 1.0, 0.0
        mode = self._mode_combo.currentText()
        angle = self._angle_spin.value()
        if mode == "Vertical":
            angle += 90.0
        elif mode not in {"Horizontal", "Angled"}:
            angle = 0.0
        rad = math.radians(angle)
        return math.cos(rad), math.sin(rad)

    def _sample_path_points(
        self,
        path: str,
        frame_number: int | None,
        *,
        frame_image=None,
        frame_key=None,
    ) -> list[tuple[int, int, int, str]]:
        video_path = self._resolve_video_path()
        if not video_path or not path.strip():
            return []
        pixel_getter = self._path_frame_sampler.get_pixel_bgr
        if frame_image is not None:
            width, height = frame_image.size

            def pixel_getter(x: int, y: int) -> str | None:
                x = max(0, min(width - 1, int(x)))
                y = max(0, min(height - 1, int(y)))
                try:
                    r, g, b = frame_image.getpixel((x, y))
                    return f"{b:02X}{g:02X}{r:02X}"
                except Exception:
                    return None

        else:
            if frame_number is None:
                return []
            frame_number = int(frame_number)
            fast_time = frame_number / max(self._preview_fps(), 1.0)
            if not (
                self._path_frame_sampler.load_frame(video_path, fast_time)
                or self._path_frame_sampler.load_frame_number(video_path, frame_number)
            ):
                return []
        return sample_path_points_from_path(path, pixel_getter)

    def _project_path_sample_colors(self, path_set: PathSet) -> list[tuple[int, str]]:
        if path_set.sampled_points:
            cos_a, sin_a = self._current_gradient_direction()
            color_map, keys = path_set.sampled_color_result(cos_a, sin_a)
            return [(int(key), color_map[key]) for key in keys if key in color_map]
        if path_set.sampled_colors:
            return path_set.sampled_colors
        return []

    def _update_curve_color_preview(self):
        key = self._active_curve_key
        tag = curve_key_tag(key)
        if tag not in {"1c", "2c", "3c", "4c"}:
            self._current_color_preview_stops = []
            self._current_color_sample_colors = []
            self._curve_editor.set_color_preview_stops(None)
            return

        path_set = self._sampling_paths.get(self._active_event_idx, {}).get(tag)
        if not path_set or not path_set.is_active:
            self._current_color_preview_stops = []
            self._current_color_sample_colors = []
            self._curve_editor.set_color_preview_stops(None)
            return

        sampled_colors = self._project_path_sample_colors(path_set)
        if not sampled_colors:
            self._current_color_preview_stops = []
            self._current_color_sample_colors = []
            self._curve_editor.set_color_preview_stops(None)
            return

        mirrored = bool(self._curve_mirrors.get(tag, (False, False))[0])
        row = self._tag_panel.get_row(tag)
        smooth = bool(row and row.path_smooth_enabled())
        smooth_strength = row.path_smooth_strength() if row else 1.0
        stops = build_path_color_preview_stops_from_sampled_colors(
            sampled_colors,
            mirrored,
            max_stops=256,
            smooth=smooth,
            smooth_strength=smooth_strength,
        )
        self._current_color_preview_stops = stops or []
        self._current_color_sample_colors = sampled_colors
        self._curve_editor.set_color_preview_stops(stops or None, len(sampled_colors))

    def _edit_sampled_colors(self):
        tag = curve_key_tag(self._active_curve_key)
        if tag not in {"1c", "2c", "3c", "4c"}:
            return
        if not self._current_color_sample_colors:
            QMessageBox.information(
                self,
                tr("采色结果"),
                tr("当前颜色 tag 没有可编辑的路径采色结果。"),
            )
            return

        dialog = ColorSampleEditorDialog(
            tag,
            self._current_color_preview_stops,
            sampled_colors=self._current_color_sample_colors,
            parent=self,
        )
        if dialog.exec() != ColorSampleEditorDialog.DialogCode.Accepted:
            return

        stops = dialog.stops()
        if dialog.action == "confirm":
            self._apply_edited_sample_colors(tag, dialog.sampled_colors())
            return

        nodes = self._color_stops_to_nodes(stops)
        key = self._active_curve_key or tag
        self._tag_curves[key] = nodes
        self._tag_modes[key] = InterpolationMode.LINEAR

        active_paths = self._sampling_paths.setdefault(self._active_event_idx, {})
        active_paths[tag] = PathSet.removed()
        self._refresh_path_buttons()

        row = self._tag_panel.get_row(tag)
        if row:
            row.set_enabled_checked(True)
            row.set_start_value(nodes[0].value_str)
            row.set_end_value(nodes[-1].value_str)

        self._current_color_preview_stops = []
        self._current_color_sample_colors = []
        self._curve_editor.set_color_preview_stops(None)
        self._curve_editor.set_nodes(nodes, True)
        self._curve_editor.set_mode(InterpolationMode.LINEAR)
        self._save_active_curve()
        self._sync_tag_overview(save_active=False)
        self._push_undo(f"采色结果应用为曲线 \\{tag}")
        self._schedule_preview()

    def _apply_edited_sample_colors(
        self,
        tag: str,
        edited_colors: list[tuple[int, str]],
    ) -> None:
        active_paths = self._sampling_paths.setdefault(self._active_event_idx, {})
        path_set = active_paths.get(tag)
        if not path_set or not path_set.is_active:
            return

        if not edited_colors:
            return

        path_state = path_set.copy()
        # Edited sample colors are now the saved source for this path.  Drop the
        # raw sampled pixels so generation does not rebuild the old colors.
        path_state.sampled_points = []
        path_state.sampled_colors = edited_colors
        active_paths[tag] = path_state

        self._update_curve_color_preview()
        self._sync_tag_overview(save_active=False)
        self._push_undo(f"采色结果确认 \\{tag}")
        self._schedule_preview()

    def _color_stops_to_nodes(self, stops: list[tuple[float, str]]) -> list[CurveNode]:
        clean: list[tuple[float, str]] = []
        for pos, color in sorted(stops, key=lambda item: item[0]):
            x = max(0.0, min(100.0, float(pos) * 100.0))
            if clean and abs(clean[-1][0] - x) < 0.001:
                clean[-1] = (x, color)
            else:
                clean.append((x, color))
        if not clean:
            clean = [(0.0, "FFFFFF"), (100.0, "FFFFFF")]
        if clean[0][0] > 0.001:
            clean.insert(0, (0.0, clean[0][1]))
        if clean[-1][0] < 99.999:
            clean.append((100.0, clean[-1][1]))

        nodes: list[CurveNode] = []
        for x, color in clean:
            node = CurveNode(x=x, y=0.0, value_str=color)
            node.handle_in_x = max(0.0, x - 5.0)
            node.handle_in_y = 0.0
            node.handle_out_x = min(100.0, x + 5.0)
            node.handle_out_y = 0.0
            node.segment_mode = InterpolationMode.LINEAR
            nodes.append(node)
        return nodes

    def _on_direction_changed(self, *args):
        self._update_curve_color_preview()
        self._sync_animation_curve_y_range()
        self._sync_animation_overview()
        self._schedule_preview()

    def _on_step_changed(self, *args):
        self._sync_animation_curve_y_range()
        self._schedule_preview()

    def _default_path_points(self) -> list[tuple[float, float]]:
        base_meta = self._get_bounds_meta(self._active_event_idx)
        if base_meta:
            try:
                x1, y1, x2, y2 = (float(v) for v in base_meta.split(",")[:4])
                y = (y1 + y2) / 2.0
                return [(x1, y), (x2, y)]
            except (ValueError, IndexError):
                pass
        if self._ass_file:
            w = float(self._ass_file.play_res_x)
            h = float(self._ass_file.play_res_y)
            return [(w * 0.25, h * 0.5), (w * 0.75, h * 0.5)]
        return [(480.0, 540.0), (1440.0, 540.0)]

    def _resolve_video_path(self) -> Optional[str]:
        return resolve_video_path(self._ass_file, self._input_path)

    def _current_video_time(self, evt: ASSEvent) -> float:
        frame = self._current_preview_video_frame()
        if frame >= 0:
            frame_info = self._event_animation_frame_info(self._active_event_idx)
            frame_times = frame_info.get("frame_time_ms", {}) if frame_info else {}
            if frame in frame_times:
                return int(frame_times[frame]) / 1000.0
            return frame / self._preview_fps()
        try:
            fps = self._video._player.container_fps
        except Exception:
            fps = None
        return current_video_time(self._ass_file, evt, fps)

    def _current_preview_video_frame(self) -> int:
        evt = self._get_active_event()
        if evt and hasattr(self, "_animation_panel"):
            first_frame, last_frame = self._active_event_frame_bounds()
            frame_offset = self._animation_panel.frame_offset()
            return max(first_frame, min(last_frame, first_frame + frame_offset))
        return self._ass_file.video_position if self._ass_file else -1

    def _load_path_preview_frame(self, video_path: str, evt: ASSEvent) -> bool:
        frame = self._current_preview_video_frame()
        frame_info = self._event_animation_frame_info(self._active_event_idx)
        frame_times = frame_info.get("frame_time_ms", {}) if frame_info else {}
        if frame >= 0 and frame in frame_times:
            if self._path_frame_sampler.load_frame(video_path, int(frame_times[frame]) / 1000.0):
                return True
        if frame >= 0 and self._path_frame_sampler.load_frame(
            video_path,
            frame / max(self._preview_fps(), 1.0),
        ):
            return True
        if frame >= 0 and self._path_frame_sampler.load_frame_number(video_path, frame):
            return True
        return self._path_frame_sampler.load_frame(video_path, self._current_video_time(evt))

    def _preview_fps(self) -> float:
        try:
            fps = float(self._video._player.container_fps)
            if fps > 0:
                return fps
        except Exception:
            pass
        return 23.976

    def _active_event_frame_bounds(self) -> tuple[int, int]:
        evt = self._get_active_event()
        if not evt:
            return 0, 0
        frame_info = self._event_animation_frame_info(self._active_event_idx)
        if frame_info:
            return int(frame_info["first_frame"]), int(frame_info["last_frame"])
        fps = self._preview_fps()
        start_sec = time_to_seconds(evt.start)
        end_sec = time_to_seconds(evt.end)
        first_frame = max(0, int(math.ceil(start_sec * fps - 1e-6)))
        last_frame = max(first_frame, int(math.ceil(end_sec * fps - 1e-6)))
        return first_frame, last_frame

    def _event_animation_frame_info(self, idx: int) -> Optional[dict[str, object]]:
        if not self._ass_file:
            return None
        try:
            return self._ass_file.animation_frame_info(idx)
        except Exception:
            return None

    def _apply_animation_frame_info(self, settings: GradientSettings, idx: int) -> GradientSettings:
        animation = settings.animation
        animation.event_first_frame = None
        animation.event_last_frame = None
        animation.event_start_ms = None
        animation.event_end_ms = None
        animation.frame_time_ms = {}
        frame_info = self._event_animation_frame_info(idx)
        if not frame_info:
            return settings
        animation.event_first_frame = int(frame_info["first_frame"])
        animation.event_last_frame = int(frame_info["last_frame"])
        event_start_ms = frame_info.get("event_start_ms")
        event_end_ms = frame_info.get("event_end_ms")
        animation.event_start_ms = int(event_start_ms) if event_start_ms is not None else None
        animation.event_end_ms = int(event_end_ms) if event_end_ms is not None else None
        animation.frame_time_ms = {
            int(frame): int(ms)
            for frame, ms in dict(frame_info.get("frame_time_ms", {}) or {}).items()
        }
        return settings

    def _active_event_total_frames(self) -> int:
        first_frame, last_frame = self._active_event_frame_bounds()
        return max(1, last_frame - first_frame + 1)

    def _active_event_loop_times(self) -> tuple[float, float]:
        evt = self._get_active_event()
        if not evt:
            return 0.0, 0.0
        frame_info = self._event_animation_frame_info(self._active_event_idx)
        if frame_info:
            frame_times = {
                int(frame): int(ms)
                for frame, ms in dict(frame_info.get("frame_time_ms", {}) or {}).items()
            }
            first_frame, last_frame = self._active_event_frame_bounds()
            start_ms = frame_info.get("event_start_ms")
            end_ms = frame_info.get("event_end_ms")
            if start_ms is None:
                start_ms = frame_times.get(first_frame)
            if end_ms is None:
                end_ms = frame_times.get(last_frame + 1)
            if start_ms is not None and end_ms is not None:
                start = max(0.0, int(start_ms) / 1000.0)
                end = max(start + 1.0 / max(self._preview_fps(), 1.0), int(end_ms) / 1000.0)
                return start, end
        start = time_to_seconds(evt.start)
        end = max(start + 1.0 / max(self._preview_fps(), 1.0), time_to_seconds(evt.end))
        return start, end

    def _preview_time_for_frame_offset(self, frame_offset: int) -> float:
        fps = self._preview_fps()
        first_frame, last_frame = self._active_event_frame_bounds()
        target_frame = min(last_frame, first_frame + max(0, int(frame_offset)))
        frame_info = self._event_animation_frame_info(self._active_event_idx)
        frame_times = (
            {
                int(frame): int(ms)
                for frame, ms in dict(frame_info.get("frame_time_ms", {}) or {}).items()
            }
            if frame_info
            else {}
        )
        if target_frame in frame_times:
            return max(0.0, int(frame_times[target_frame]) / 1000.0)
        return max(0.0, target_frame / max(fps, 1.0))

    def _frame_offset_for_preview_time(self, time_sec: float) -> int:
        first_frame, last_frame = self._active_event_frame_bounds()
        frame_info = self._event_animation_frame_info(self._active_event_idx)
        frame_times = (
            {
                int(frame): int(ms)
                for frame, ms in dict(frame_info.get("frame_time_ms", {}) or {}).items()
            }
            if frame_info
            else {}
        )
        if frame_times:
            current_ms = float(time_sec) * 1000.0
            target_frame = first_frame
            for frame, ms in sorted((int(k), int(v)) for k, v in frame_times.items()):
                if frame < first_frame or frame > last_frame:
                    continue
                if ms <= current_ms + 0.5:
                    target_frame = frame
                else:
                    break
            return max(0, min(last_frame - first_frame, target_frame - first_frame))
        fps = self._preview_fps()
        frame = int(math.floor(max(0.0, float(time_sec)) * max(fps, 1.0) + 1e-6))
        return max(0, min(last_frame - first_frame, frame - first_frame))

    def _reset_preview_frame_to_aegisub_position(self):
        if not hasattr(self, "_animation_panel"):
            return
        first_frame, last_frame = self._active_event_frame_bounds()
        frame_num = self._ass_file.video_position if self._ass_file else -1
        if first_frame <= frame_num <= last_frame:
            offset = frame_num - first_frame
        else:
            offset = 0
        self._animation_panel.set_frame_offset(offset, emit=False)

    def _update_preview_frame_range(self, *, sync_animation_editor: bool = True):
        if hasattr(self, "_animation_panel"):
            self._animation_panel.set_frame_range(self._active_event_total_frames())
            if sync_animation_editor:
                self._sync_animation_curve_editor()
            else:
                self._sync_animation_overview(save_active=False)
            if self._loop_preview_timer.isActive():
                self._update_loop_preview_interval()

    def _seek_preview_frame(self):
        evt = self._get_active_event()
        if not evt or not hasattr(self, "_animation_panel"):
            return
        self._video.seek_time(self._preview_time_for_frame_offset(
            self._animation_panel.frame_offset()
        ))

    def _on_preview_frame_changed(self, frame_offset: int):
        self._seek_preview_frame()
        self._sync_animation_overview(save_active=False)

    def _step_preview_frame_backward(self):
        if hasattr(self, "_animation_panel"):
            self._animation_panel.step_preview_frame(-1)

    def _step_preview_frame_forward(self):
        if hasattr(self, "_animation_panel"):
            self._animation_panel.step_preview_frame(1)

    def _on_animation_settings_changed(self):
        self._save_active_animation_panel_state()
        self._update_preview_frame_range()
        self._sync_animation_overview()
        self._schedule_preview()

    def _on_loop_play_toggled(self, checked: bool):
        if checked:
            self._update_preview_frame_range()
            loop_start, loop_end = self._active_event_loop_times()
            seek_time = self._preview_time_for_frame_offset(
                self._animation_panel.frame_offset()
            )
            self._loop_preview_native = self._video.start_loop_playback(
                loop_start,
                loop_end,
                seek_time,
            )
            self._update_loop_preview_interval()
            self._loop_preview_timer.start()
        else:
            self._loop_preview_timer.stop()
            if self._loop_preview_native:
                self._video.stop_loop_playback(pause=True)
            self._loop_preview_native = False
        self._animation_panel.set_loop_playing(checked)

    def _update_loop_preview_interval(self):
        fps = self._preview_fps()
        if self._loop_preview_native:
            self._loop_preview_timer.setInterval(max(80, int(round(1000.0 / max(fps, 1.0)))))
        else:
            self._loop_preview_timer.setInterval(max(15, int(round(1000.0 / max(fps, 1.0)))))

    def _advance_loop_preview_frame(self):
        if not hasattr(self, "_animation_panel"):
            return
        if self._loop_preview_native:
            time_sec = self._video.current_time()
            if time_sec is None:
                return
            loop_start, loop_end = self._active_event_loop_times()
            if time_sec < loop_start - 0.25 or time_sec > loop_end + 0.25:
                self._video.start_loop_playback(loop_start, loop_end, loop_start)
                time_sec = loop_start
            frame_offset = self._frame_offset_for_preview_time(time_sec)
            if frame_offset != self._animation_panel.frame_offset():
                self._animation_panel.set_frame_offset(frame_offset, emit=False)
                self._sync_animation_overview(save_active=False)
            return
        total = self._active_event_total_frames()
        if total <= 1:
            self._animation_panel.set_frame_offset(0, emit=True)
            return
        next_frame = self._animation_panel.frame_offset() + 1
        if next_frame >= total:
            next_frame = 0
        self._animation_panel.set_frame_offset(next_frame, emit=True)

    def _selected_event_indices(self, fallback: bool = True) -> list[int]:
        return self._line_panel.selected_indices(self._active_event_idx, fallback)

    def _merged_bounds_rect(self, indices: list[int]) -> Optional[BoundsRect]:
        return self._bounds.merged_rect(indices)

    def _line_group_enabled(self, indices: list[int]) -> bool:
        return self._line_panel.merge_range_enabled() and len(indices) > 1

    def _sampling_paths_for_event(self, idx: int) -> dict[str, str]:
        return sampling_paths_for_event(
            self._sampling_paths,
            self._active_event_idx,
            idx,
        )

    def _settings_for_event(
        self,
        base_settings: GradientSettings,
        idx: int,
        group_range_bounds: Optional[BoundsRect] = None,
    ) -> GradientSettings:
        settings = build_event_settings(
            base_settings=base_settings,
            idx=idx,
            active_event_idx=self._active_event_idx,
            sampling_paths=self._sampling_paths,
            bounds=self._bounds,
            group_range_bounds=group_range_bounds,
            group_range_tags=self._group_range_tags,
        )
        return self._apply_animation_frame_info(settings, idx)

    def _sync_tabs(self):
        enabled_tags = self._tag_panel.get_enabled_tags()
        current_key = self._active_curve_key
        enabled_curve_keys: list[str] = []
        for tag in enabled_tags:
            enabled_curve_keys.extend(curve_keys_for_tag(tag))
        overview_keys = [TAG_OVERVIEW_TAB, ANIMATION_OVERVIEW_TAB]
        tab_keys = enabled_curve_keys + overview_keys

        existing_keys = [
            self._curve_tabs.tabData(i)
            for i in range(self._curve_tabs.count())
        ]
        if existing_keys != tab_keys:
            self._curve_tabs.blockSignals(True)
            while self._curve_tabs.count() > 0:
                self._curve_tabs.removeTab(0)
            for key in enabled_curve_keys:
                self._curve_tabs.addTab(curve_label(key))
                self._curve_tabs.setTabData(self._curve_tabs.count() - 1, key)
            self._curve_tabs.addTab(tr("标签总览"))
            self._curve_tabs.setTabData(
                self._curve_tabs.count() - 1,
                TAG_OVERVIEW_TAB,
            )
            self._curve_tabs.addTab(tr("动画总览"))
            self._curve_tabs.setTabData(
                self._curve_tabs.count() - 1,
                ANIMATION_OVERVIEW_TAB,
            )
            self._curve_tabs.blockSignals(False)

        for i in range(self._curve_tabs.count()):
            key = self._curve_tabs.tabData(i)
            if key == TAG_OVERVIEW_TAB:
                self._curve_tabs.setTabText(i, tr("标签总览"))
            elif key == ANIMATION_OVERVIEW_TAB:
                self._curve_tabs.setTabText(i, tr("动画总览"))
            elif key:
                self._curve_tabs.setTabText(i, curve_label(str(key)))
            
        idx = -1
        if self._overview_tab_active in overview_keys:
            idx = self._tab_index_for_key(self._overview_tab_active)
        elif current_key in enabled_curve_keys:
            for i in range(self._curve_tabs.count()):
                if self._curve_tabs.tabData(i) == current_key:
                    idx = i
                    break
        elif enabled_curve_keys:
            idx = 0
        else:
            idx = self._tab_index_for_key(TAG_OVERVIEW_TAB)

        if self._curve_tabs.currentIndex() != idx:
            self._curve_tabs.blockSignals(True)
            self._curve_tabs.setCurrentIndex(idx)
            self._curve_tabs.blockSignals(False)
        
        if idx >= 0:
            key = self._curve_tabs.tabData(idx)
            if key == TAG_OVERVIEW_TAB:
                if not enabled_curve_keys:
                    self._active_tag = None
                    self._active_curve_key = None
                    self._sync_animation_panel_for_active_tag(None)
                self._show_tag_overview_tab()
                self._curve_container.setVisible(True)
                self._curve_tabs.setVisible(True)
                self._editor_stack.setEnabled(True)
                return
            if key == ANIMATION_OVERVIEW_TAB:
                if not enabled_curve_keys:
                    self._active_tag = None
                    self._active_curve_key = None
                    self._sync_animation_panel_for_active_tag(None)
                self._show_animation_overview_tab()
                self._curve_container.setVisible(True)
                self._curve_tabs.setVisible(True)
                self._editor_stack.setEnabled(True)
                return
            self._show_editor_tabs()
            if key != self._active_curve_key:
                self._on_curve_key_selected(key)
            else:
                self._sync_animation_curve_editor()
            self._curve_container.setVisible(True)
            self._curve_editor.setVisible(True)
            self._curve_tabs.setVisible(True)
            self._curve_editor.setEnabled(True)
            self._editor_tabs.setEnabled(True)
            self._editor_stack.setEnabled(True)
        else:
            self._active_tag = None
            self._active_curve_key = None
            self._curve_container.setVisible(True)
            self._curve_editor.setVisible(True)
            self._curve_tabs.setVisible(True)
            self._curve_editor.setEnabled(False)
            self._editor_tabs.setEnabled(False)
            self._animation_curve_editor.setEnabled(False)
            self._sync_animation_panel_for_active_tag(None)
            self._sync_animation_overview(save_active=False)

    def _on_tab_changed_ui(self, index: int):
        if index >= 0:
            key = self._curve_tabs.tabData(index)
            if key == TAG_OVERVIEW_TAB:
                self._show_tag_overview_tab()
            elif key == ANIMATION_OVERVIEW_TAB:
                self._show_animation_overview_tab()
            else:
                self._show_editor_tabs()
                self._on_curve_key_selected(key)

    def _tab_index_for_key(self, key: str) -> int:
        for i in range(self._curve_tabs.count()):
            if self._curve_tabs.tabData(i) == key:
                return i
        return -1

    def _show_editor_tabs(self):
        self._overview_tab_active = None
        if hasattr(self, "_editor_stack"):
            self._editor_stack.setCurrentWidget(self._editor_tabs)

    def _show_tag_overview_tab(self):
        if self._active_curve_key:
            self._save_active_curve()
            self._save_active_animation_curve()
            self._save_active_animation_panel_state()
        self._overview_tab_active = TAG_OVERVIEW_TAB
        if hasattr(self, "_editor_stack"):
            self._editor_stack.setCurrentWidget(self._tag_overview_page)
        self._sync_tag_overview(save_active=False)

    def _show_animation_overview_tab(self):
        if self._active_curve_key:
            self._save_active_curve()
            self._save_active_animation_curve()
            self._save_active_animation_panel_state()
        self._overview_tab_active = ANIMATION_OVERVIEW_TAB
        if hasattr(self, "_editor_stack"):
            self._editor_stack.setCurrentWidget(self._overview_page)
        self._sync_animation_overview(save_active=False)

    def _sync_tag_overview(self, *, save_active: bool = True):
        if not hasattr(self, "_tag_overview"):
            return
        if save_active and self._active_curve_key and not self._syncing_curve_editor:
            self._save_active_curve()

        enabled_tags = [
            tag for tag in TAG_INFO.keys()
            if tag in set(self._tag_panel.get_enabled_tags())
        ]
        color_space = self._tag_panel.get_color_space()
        color_space_label = getattr(color_space, "value", str(color_space))
        rows: list[TagOverviewRow] = []
        for tag in enabled_tags:
            for key in curve_keys_for_tag(tag):
                nodes, mode = self._tag_overview_curve_state(key)
                info = TAG_INFO.get(tag, {})
                rows.append(
                    TagOverviewRow(
                        key=key,
                        tag=tag,
                        label=curve_label(key),
                        group=group_label(str(info.get("group", ""))),
                        tag_type=str(info.get("type", "numeric")),
                        value_text=self._tag_overview_value_text(key, nodes),
                        source_text=self._tag_overview_source_text(tag),
                        nodes=nodes,
                        mode=mode,
                        color_space=color_space,
                        active=(key == self._active_curve_key),
                    )
                )
        self._tag_overview.set_rows(rows, color_space_label=color_space_label)

    def _tag_overview_curve_state(
        self,
        key: str,
    ) -> tuple[list[CurveNode], InterpolationMode]:
        tag = curve_key_tag(key) or key
        axis = curve_key_axis(key)
        nodes = self._tag_curves.get(key)
        mode = self._tag_modes.get(key, InterpolationMode.LINEAR)
        if nodes:
            return self._clone_nodes(nodes), mode

        row = self._tag_panel.get_row(tag)
        if not row:
            return make_default_nodes(), InterpolationMode.LINEAR
        cfg = row.get_config()
        if axis == "y":
            return (
                self._clone_nodes(cfg.coord_y_nodes),
                cfg.coord_y_mode,
            )
        return self._clone_nodes(cfg.nodes), cfg.mode

    def _tag_overview_value_text(self, key: str, nodes: list[CurveNode]) -> str:
        if not nodes:
            return ""
        tag = curve_key_tag(key) or key
        axis = curve_key_axis(key)
        tag_type = TAG_INFO.get(tag, {}).get("type")
        first = nodes[0]
        last = nodes[-1]
        if tag_type == "color":
            return f"&H{first.value_str or 'FFFFFF'}& → &H{last.value_str or 'FFFFFF'}&"
        if tag_type == "text":
            left = first.value_str or tr("(空)")
            right = last.value_str or tr("(空)")
            return f"{left} → {right}"
        if tag_type == "alpha":
            return f"&H{_clamp_byte(first.y):02X}& → &H{_clamp_byte(last.y):02X}&"
        prefix = f"{axis.upper()} " if axis else ""
        return f"{prefix}{_format_overview_number(first.y)} → {_format_overview_number(last.y)}"

    def _tag_overview_source_text(self, tag: str) -> str:
        if tag not in {"1c", "2c", "3c", "4c"}:
            return tr("手动")
        active_paths = self._sampling_paths.get(self._active_event_idx, {})
        path_set = active_paths.get(tag)
        if path_set:
            if path_set.removed_original:
                return tr("路径已移除")
            if path_set.is_active:
                count = sum(1 for path in path_set.paths if path.is_valid)
                return f"{tr('路径')} {max(1, count)}"
        evt = self._get_active_event()
        if evt:
            try:
                _, clips = extract_clip_tags(evt.text)
            except Exception:
                clips = {}
            if clips.get(tag):
                return tr("原路径")
        return tr("手动")

    def _save_active_curve(self):
        """Save the current curve editor state to the active tag."""
        if not self._active_curve_key:
            return
        nodes = self._curve_editor.get_nodes()
        self._tag_curves[self._active_curve_key] = [
            CurveNode(
                x=n.x, y=n.y, value_str=n.value_str,
                handle_in_x=n.handle_in_x, handle_in_y=n.handle_in_y,
                handle_out_x=n.handle_out_x, handle_out_y=n.handle_out_y,
                segment_mode=n.segment_mode,
            )
            for n in nodes
        ]
        self._tag_modes[self._active_curve_key] = self._curve_editor.get_mode()

    def _clone_nodes(self, nodes: list[CurveNode]) -> list[CurveNode]:
        return [
            CurveNode(
                x=n.x, y=n.y, value_str=n.value_str,
                handle_in_x=n.handle_in_x, handle_in_y=n.handle_in_y,
                handle_out_x=n.handle_out_x, handle_out_y=n.handle_out_y,
                segment_mode=n.segment_mode,
            )
            for n in nodes
        ]

    def _active_animation_tag(self) -> Optional[str]:
        if not self._active_curve_key:
            return None
        tag = curve_key_tag(self._active_curve_key) or self._active_curve_key
        return tag if tag in TAG_INFO else None

    def _sync_animation_panel_for_active_tag(self, tag: Optional[str] = None):
        if not hasattr(self, "_animation_panel"):
            return
        tag = tag if tag is not None else self._active_animation_tag()
        enabled_tags = set(self._tag_panel.get_enabled_tags())
        if not tag or tag not in enabled_tags:
            self._animation_panel.set_active_tag(None, False)
            return
        label = TAG_INFO.get(tag, {}).get("label", f"\\{tag}")
        self._animation_panel.set_active_tag(
            label,
            tag in self._animation_enabled_tags,
            self._animation_seam_blend_lengths.get(tag, 0),
            self._animation_frame_step_for_tag(tag),
        )

    def _active_animation_enabled_tags(
        self,
        enabled_tags: Optional[list[str]] = None,
    ) -> list[str]:
        enabled_tag_set = (
            set(enabled_tags)
            if enabled_tags is not None
            else set(self._tag_panel.get_enabled_tags())
        )
        return [
            tag for tag in TAG_INFO.keys()
            if tag in enabled_tag_set and tag in self._animation_enabled_tags
        ]

    def _animation_panel_state(self) -> dict[str, object]:
        self._save_active_animation_panel_state()
        state = self._animation_panel.state()
        state["enabled"] = bool(self._animation_enabled_tags)
        state["enabled_tags"] = [
            tag for tag in TAG_INFO.keys()
            if tag in self._animation_enabled_tags
        ]
        state["frame_steps"] = {
            tag: self._animation_frame_step_for_tag(tag)
            for tag in TAG_INFO.keys()
            if tag in self._animation_frame_steps
        }
        state["seam_blend_lengths"] = {
            tag: int(self._animation_seam_blend_lengths.get(tag, 0))
            for tag in TAG_INFO.keys()
            if int(self._animation_seam_blend_lengths.get(tag, 0)) > 0
        }
        return state

    def _save_active_animation_panel_state(self) -> None:
        if not hasattr(self, "_animation_panel"):
            return
        tag = self._active_animation_tag()
        if not tag:
            return
        if self._animation_panel.active_tag_animation_enabled():
            self._animation_enabled_tags.add(tag)
        else:
            self._animation_enabled_tags.discard(tag)
        self._animation_frame_steps[tag] = self._animation_panel.frame_step()
        seam_length = self._animation_panel.seam_blend_length()
        if seam_length > 0:
            self._animation_seam_blend_lengths[tag] = seam_length
        else:
            self._animation_seam_blend_lengths.pop(tag, None)

    def _animation_frame_step_for_tag(self, tag: str) -> int:
        try:
            return max(1, int(self._animation_frame_steps.get(tag, 1) or 1))
        except (TypeError, ValueError):
            return 1

    def _animation_curve_x_max(self) -> float:
        return float(max(1, self._active_event_total_frames() - 1))

    def _animation_curve_y_extent(self) -> float:
        """Visible movement range for the animation editor, in strip cells."""
        return 10.0

    def _sync_animation_curve_y_range(self):
        if not hasattr(self, "_animation_curve_editor"):
            return
        if not self._active_animation_tag():
            return
        extent = self._animation_curve_y_extent()
        previous = self._syncing_animation_editor
        self._syncing_animation_editor = True
        try:
            self._animation_curve_editor.set_y_range(-extent, extent)
        finally:
            self._syncing_animation_editor = previous

    def _default_animation_curve_nodes(self) -> list[CurveNode]:
        x_max = self._animation_curve_x_max()
        n0 = CurveNode(x=0.0, y=1.0)
        n1 = CurveNode(x=x_max, y=1.0)
        n0.handle_in_x = -max(1.0, x_max * 0.1)
        n0.handle_in_y = 1.0
        n0.handle_out_x = x_max / 3.0
        n0.handle_out_y = 1.0
        n1.handle_in_x = x_max * 2.0 / 3.0
        n1.handle_in_y = 1.0
        n1.handle_out_x = x_max + max(1.0, x_max * 0.1)
        n1.handle_out_y = 1.0
        return [n0, n1]

    def _normalize_animation_curve_nodes(self, nodes: list[CurveNode]) -> list[CurveNode]:
        x_max = self._animation_curve_x_max()
        cloned = self._clone_nodes(nodes) if nodes else self._default_animation_curve_nodes()
        cloned.sort(key=lambda n: n.x)
        if len(cloned) < 2:
            return self._default_animation_curve_nodes()
        old_max = max(cloned[-1].x, 1.0)
        if abs(old_max - x_max) > 1e-6:
            factor = x_max / old_max
            for node in cloned:
                node.x *= factor
                node.handle_in_x *= factor
                node.handle_out_x *= factor
        for node in cloned:
            node.x = float(round(max(0.0, min(x_max, node.x))))
            node.handle_in_x = float(round(max(0.0, min(x_max, node.handle_in_x))))
            node.handle_out_x = float(round(max(0.0, min(x_max, node.handle_out_x))))
        cloned[0].x = 0.0
        cloned[-1].x = x_max
        return cloned

    def _sync_animation_curve_editor(self):
        if not hasattr(self, "_animation_curve_editor"):
            return
        tag = self._active_animation_tag()
        has_animation_tag = tag is not None
        self._editor_tabs.setTabEnabled(1, has_animation_tag)
        self._sync_animation_panel_for_active_tag(tag)
        if not has_animation_tag:
            if self._editor_tabs.currentIndex() == 1:
                self._editor_tabs.setCurrentIndex(0)
            self._animation_curve_editor.setEnabled(False)
            self._sync_animation_overview(save_active=False)
            return

        nodes = self._normalize_animation_curve_nodes(
            self._animation_curves.get(tag, [])
        )
        mode = self._animation_modes.get(tag, InterpolationMode.LINEAR)
        self._syncing_animation_editor = True
        try:
            self._animation_curve_editor.setEnabled(True)
            self._animation_curve_editor.set_x_max(self._animation_curve_x_max())
            extent = self._animation_curve_y_extent()
            self._animation_curve_editor.set_y_range(-extent, extent)
            self._animation_curve_editor.set_nodes(nodes, is_color=False, is_text=False)
            self._animation_curve_editor.set_mode(mode)
            self._animation_curve_editor.set_mirror_state(
                *self._animation_curve_mirrors.get(tag, (False, False))
            )
        finally:
            self._syncing_animation_editor = False
        self._sync_animation_overview(save_active=False)

    def _sync_animation_overview(self, *, save_active: bool = True):
        if not hasattr(self, "_animation_overview"):
            return
        if save_active and not self._syncing_animation_editor:
            self._save_active_animation_curve()

        enabled_tags = [
            tag for tag in TAG_INFO.keys()
            if tag in set(self._tag_panel.get_enabled_tags())
        ]
        animated_tags = self._active_animation_enabled_tags(enabled_tags)
        x_max = self._animation_curve_x_max()
        animation = self._animation_panel.to_settings(self._preview_fps())
        animation.enabled = bool(animated_tags)
        animation.enabled_tags = set(animated_tags)
        mode_text = self._mode_combo.currentText() if hasattr(self, "_mode_combo") else "Horizontal"
        can_use_transform = (
            bool(animation.enabled)
            and bool(animation.use_transform)
            and mode_text != "GBC"
            and bool(animated_tags)
            and all(tag in TRANSFORMABLE_ANIMATION_TAGS for tag in animated_tags)
        )
        animated_tag_set = set(animated_tags)
        if is_english():
            frame_range = f"0-{int(round(x_max))} frames"
        else:
            frame_range = f"0-{int(round(x_max))} 帧"
        active_tag = self._active_animation_tag()
        rows = [
            AnimationOverviewRow(
                tag=tag,
                label=tag_label(tag, str(TAG_INFO.get(tag, {}).get("label", tag))),
                frame_range=(
                    f"{frame_range} · Every {self._animation_frame_step_for_tag(tag)} frames"
                    if is_english()
                    else f"{frame_range} · 每 {self._animation_frame_step_for_tag(tag)} 帧"
                ),
                output_mode=(
                    tr("关闭")
                    if tag not in animated_tag_set
                    else (r"\t" if can_use_transform else "split")
                ),
                nodes=self._normalize_animation_curve_nodes(
                    self._animation_curves.get(tag, [])
                ),
                mode=self._animation_modes.get(tag, InterpolationMode.LINEAR),
                active=(tag == active_tag),
            )
            for tag in enabled_tags
        ]
        self._animation_overview.set_rows(
            rows,
            x_max=x_max,
            animation_enabled=bool(animated_tags),
            use_transform=can_use_transform,
            preview_frame=(
                self._animation_panel.frame_offset()
                if hasattr(self, "_animation_panel")
                else 0
            ),
        )

    def _save_active_animation_curve(self):
        tag = self._active_animation_tag()
        if not tag or not hasattr(self, "_animation_curve_editor"):
            return
        self._animation_curves[tag] = self._clone_nodes(
            self._animation_curve_editor.get_nodes()
        )
        self._animation_modes[tag] = self._animation_curve_editor.get_mode()

    def _on_animation_curve_changed(self):
        if self._syncing_animation_editor:
            return
        self._save_active_animation_curve()
        self._sync_animation_overview(save_active=False)
        self._schedule_preview()

    def _on_animation_curve_mirror_changed(self, horizontal: bool, vertical: bool):
        if self._syncing_animation_editor:
            return
        tag = self._active_animation_tag()
        if not tag:
            return
        self._animation_curve_mirrors[tag] = (horizontal, vertical)
        self._save_active_animation_curve()
        self._sync_animation_overview(save_active=False)
        self._schedule_preview()

    def _animation_settings(self):
        self._save_active_animation_curve()
        self._save_active_animation_panel_state()
        animation = self._animation_panel.to_settings(self._preview_fps())
        animated_tags = self._active_animation_enabled_tags()
        animation.enabled = bool(animated_tags)
        animation.enabled_tags = set(animated_tags)
        animation.frame_steps = {
            tag: self._animation_frame_step_for_tag(tag)
            for tag in animated_tags
        }
        animation.seam_blend_length = 0
        animation.seam_blend_lengths = {
            tag: int(self._animation_seam_blend_lengths.get(tag, 0))
            for tag in animated_tags
            if int(self._animation_seam_blend_lengths.get(tag, 0)) > 0
        }
        animation.shift_curves = {
            tag: self._clone_nodes(nodes)
            for tag, nodes in self._animation_curves.items()
        }
        animation.shift_modes = copy.deepcopy(self._animation_modes)
        return animation

    def _schedule_preview(self, *args):
        self._preview_timer.start()
        self._queue_undo("编辑")

    def _queue_undo(self, description: str = "编辑"):
        if self._undo_suspended:
            return
        self._pending_undo_description = description
        self._undo_timer.start()

    def _push_pending_undo(self):
        description = self._pending_undo_description or "编辑"
        self._pending_undo_description = "编辑"
        self._push_undo(description)

    def _flush_pending_undo(self):
        if self._undo_timer.isActive():
            self._undo_timer.stop()
            self._push_pending_undo()

    # ── Gradient generation + preview ────────────────────────────────────

    def _get_active_event(self) -> Optional[ASSEvent]:
        if 0 <= self._active_event_idx < len(self._source_events):
            return self._source_events[self._active_event_idx]
        return None

    def _build_settings(self) -> Optional[GradientSettings]:
        evt = self._get_active_event()
        if not evt or not self._ass_file:
            return None

        try:
            fps = self._video._player.container_fps
        except Exception:
            fps = None

        settings = build_base_settings(
            ass_file=self._ass_file,
            active_event=evt,
            active_event_idx=self._active_event_idx,
            input_path=self._input_path,
            video_fps=fps,
            mode_text=self._mode_combo.currentText(),
            angle=self._angle_spin.value(),
            step=self._step_spin.value(),
            tag_panel=self._tag_panel,
            tag_curves=self._tag_curves,
            tag_modes=self._tag_modes,
            curve_mirrors=self._curve_mirrors,
            sampling_paths=self._sampling_paths,
            bounds=self._bounds,
            animation_settings=self._animation_settings(),
        )
        video_frame = self._current_preview_video_frame()
        if settings.video_path and video_frame >= 0:
            settings.video_frame = video_frame
            settings.video_time = self._current_video_time(evt)
        return self._apply_animation_frame_info(settings, self._active_event_idx)

    def _get_bounds_meta(self, idx: int) -> Optional[str]:
        return self._bounds.get_meta(idx)

    def _preview_cache_key(
        self,
        base_settings: GradientSettings,
        selected_indices: list[int],
        group_range_bounds: Optional[BoundsRect],
    ) -> str:
        state = self._capture_state("预览缓存")
        state_data = dict(state.__dict__)
        state_data["description"] = ""
        source_events = []
        for idx in selected_indices:
            if 0 <= idx < len(self._source_events):
                evt = self._source_events[idx]
                source_events.append(
                    {
                        "index": idx,
                        "layer": evt.layer,
                        "start": evt.start,
                        "end": evt.end,
                        "style": evt.style,
                        "name": evt.name,
                        "margin_l": evt.margin_l,
                        "margin_r": evt.margin_r,
                        "margin_v": evt.margin_v,
                        "effect": evt.effect,
                        "text": evt.text,
                        "bounds_meta": self._get_bounds_meta(idx),
                    }
                )
        return stable_preview_key(
            {
                "version": 1,
                "input_path": self._input_path,
                "play_res": (
                    (self._ass_file.play_res_x, self._ass_file.play_res_y)
                    if self._ass_file else None
                ),
                "video_path": base_settings.video_path,
                "video_frame": base_settings.video_frame,
                "video_time": base_settings.video_time,
                "selected_indices": selected_indices,
                "source_events": source_events,
                "group_range_bounds": group_range_bounds,
                "state": state_data,
            }
        )

    def _do_preview(self):
        base_settings = self._build_settings()
        if not base_settings or not self._ass_file:
            return

        selected_indices = self._selected_event_indices()
        use_group_bounds = self._line_group_enabled(selected_indices)
        group_range_bounds = self._merged_bounds_rect(selected_indices) if use_group_bounds else None
        cache_key = self._preview_cache_key(
            base_settings,
            selected_indices,
            group_range_bounds,
        )
        cached_events = self._preview_cache.get(cache_key)
        cache_hit = cached_events is not None

        try:
            if cache_hit:
                result_events = cached_events
            else:
                result_events = generate_gradient_events(
                    self._source_events,
                    selected_indices,
                    self._ass_file,
                    base_settings,
                    lambda settings, idx: self._settings_for_event(
                        settings, idx, group_range_bounds
                    ),
                    self._get_bounds_meta,
                )
                self._preview_cache.put(cache_key, result_events)
        except GradientGenerationError as e:
            traceback.print_exception(
                type(e.original), e.original, e.original.__traceback__
            )
            self._show_generation_error(
                e,
                base_settings,
                selected_indices,
                group_range_bounds,
            )
            return

        self._last_preview_cache_hit = cache_hit
        self._last_preview_result_events = result_events
        self._last_preview_debug_data = self._build_preview_debug_data(
            selected_indices,
            result_events,
            group_range_bounds,
            base_settings,
        )
        preview_events = list(result_events)
        if self._last_preview_debug_data:
            base_debug_event = (
                self._source_events[selected_indices[0]]
                if selected_indices and 0 <= selected_indices[0] < len(self._source_events)
                else None
            )
            preview_events.extend(
                debug_overlay_ass_events(self._last_preview_debug_data, base_debug_event)
            )
        ass_content = build_preview_ass(self._ass_file, preview_events)
        self._video.update_subtitle(ass_content)
        self._update_preview_frame_range(sync_animation_editor=False)
        self._seek_preview_frame()
        self._video.set_debug_overlay_data(None)
        mode_text = "整体范围" if group_range_bounds and self._group_range_tags else "逐行范围"
        self._last_preview_summary = {
            "source_line_count": len(selected_indices),
            "output_line_count": len(result_events),
            "range_mode": mode_text,
            "selected_indices": selected_indices,
            "cache_hit": cache_hit,
            "preview_cache_size": self._preview_cache.size,
        }
        self._show_preview_status()

    def _show_preview_status(self) -> None:
        summary = self._last_preview_summary or {}
        try:
            source_count = int(summary.get("source_line_count", 0) or 0)
            output_count = int(summary.get("output_line_count", 0) or 0)
        except (TypeError, ValueError):
            source_count = 0
            output_count = 0
        range_mode = str(summary.get("range_mode", "") or "逐行范围")
        range_mode = tr(range_mode)
        if is_english():
            message = (
                f"{tr('预览已更新')} ({source_count} {tr('行源字幕')} / "
                f"{output_count} {tr('行输出')}, {range_mode})"
            )
        else:
            message = (
                f"{tr('预览已更新')} ({source_count} 行源字幕 / "
                f"{output_count} 行输出, {range_mode})"
            )
        self._status.showMessage(message)

    def _show_generation_error(
        self,
        error: GradientGenerationError,
        base_settings: GradientSettings,
        selected_indices: list[int],
        group_range_bounds: Optional[BoundsRect],
    ) -> None:
        context = dict(getattr(error, "context", {}) or {})
        debug_json = self._generation_error_debug_json(
            error,
            base_settings,
            selected_indices,
            group_range_bounds,
            context,
        )
        tag_text = self._format_error_tag(context)
        value_text = self._format_error_value(context)
        clip_text = self._format_error_clip(context)
        line_number = context.get("line_number", error.line_index + 1)
        if is_english():
            message = (
                f"Gradient generation failed on line {line_number}.\n\n"
                f"Tag: {tag_text}\n"
                f"Current value: {value_text}\n"
                f"clip/range: {clip_text}\n\n"
                f"Error: {error.original}\n\n"
                "The previous successful preview has been kept. Expand details to copy the debug JSON."
            )
        else:
            message = (
                f"第 {line_number} 行渐变生成失败。\n\n"
                f"Tag: {tag_text}\n"
                f"当前值: {value_text}\n"
                f"clip/范围: {clip_text}\n\n"
                f"错误: {error.original}\n\n"
                "预览已保留上一次成功结果。展开详细信息可复制 debug JSON。"
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(tr("渐变生成失败"))
        box.setText(tr("渐变生成失败"))
        box.setInformativeText(message)
        box.setDetailedText(debug_json)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        if self._last_preview_summary:
            self._show_preview_status()
        else:
            self._status.showMessage(
                f"渐变生成错误: 第 {line_number} 行 {tag_text}: {error.original}"
            )

    def _generation_error_debug_json(
        self,
        error: GradientGenerationError,
        base_settings: GradientSettings,
        selected_indices: list[int],
        group_range_bounds: Optional[BoundsRect],
        context: dict[str, object],
    ) -> str:
        fallback = {
            "format": "GradientGUI Generation Error",
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_path": self._input_path,
            "selected_indices": selected_indices,
            "generation_error": context,
        }
        try:
            report = self._build_debug_report()
        except Exception as report_exc:
            report = fallback
            report["debug_report_error"] = str(report_exc)
        report["generation_error"] = context
        report["generation_error"]["exception"] = {
            "type": type(error.original).__name__,
            "message": str(error.original),
            "traceback": "".join(
                traceback.format_exception(
                    type(error.original),
                    error.original,
                    error.original.__traceback__,
                )
            ),
        }
        report["generation_error"]["selected_indices"] = selected_indices
        report["generation_error"]["group_range_bounds"] = group_range_bounds
        report["generation_error"]["preview_cache"] = {
            "size": self._preview_cache.size,
            "last_hit": self._last_preview_cache_hit,
        }
        return json.dumps(report, ensure_ascii=False, indent=2, default=str)

    def _format_error_tag(self, context: dict[str, object]) -> str:
        tag = context.get("tag")
        label = context.get("tag_label")
        if tag and label:
            return f"\\{tag} ({label})"
        if tag:
            return f"\\{tag}"
        enabled = context.get("enabled_tags") or []
        if enabled:
            return "未精确定位；启用 tag: " + ", ".join(f"\\{tag}" for tag in enabled)
        return "未定位"

    def _format_error_value(self, context: dict[str, object]) -> str:
        value = context.get("tag_value")
        if value is not None:
            return str(value)
        tag = context.get("tag")
        tag_values = context.get("tag_values")
        if isinstance(tag_values, dict) and tag in tag_values:
            entry = tag_values.get(tag) or {}
            if isinstance(entry, dict):
                return str(entry.get("current", "未知"))
        return "未知"

    def _format_error_clip(self, context: dict[str, object]) -> str:
        clip = context.get("clip_range")
        if not isinstance(clip, dict):
            return "未知"
        parts = []
        bounds = clip.get("generated_bounds")
        if bounds:
            parts.append(f"生成范围 {bounds}")
        source_clip = clip.get("source_clip_bounds")
        if source_clip:
            parts.append(f"原 clip {source_clip}")
        tag_clip = clip.get("tag_clip")
        if tag_clip:
            parts.append(f"tag clip {tag_clip}")
        return "；".join(parts) if parts else "未知"

    def _on_debug_overlay_toggled(self, checked: bool):
        self._debug_overlay_enabled = bool(checked)
        self._do_preview()

    def _build_preview_debug_data(
        self,
        selected_indices: list[int],
        result_events: list[ASSEvent],
        group_range_bounds: Optional[BoundsRect],
        base_settings: GradientSettings,
        force: bool = False,
    ):
        if (not self._debug_overlay_enabled and not force) or not self._ass_file:
            return None

        rects = []
        enabled_tags = sorted(
            tag for tag, cfg in base_settings.tags.items() if getattr(cfg, "enabled", False)
        )
        for idx in selected_indices:
            if not (0 <= idx < len(self._source_events)):
                continue
            meta_rect = BoundsController.parse_meta_rect(self._get_bounds_meta(idx))
            if meta_rect:
                rects.append(rect_item(f"libass #{idx + 1}", meta_rect, "#ffd166"))
            source_clip = extract_clip_bounds(self._source_events[idx].text)
            if source_clip:
                rects.append(rect_item(f"source clip #{idx + 1}", source_clip, "#ff9f1c"))
            settings = self._settings_for_event(base_settings, idx, group_range_bounds)
            rects.append(
                rect_item(
                    f"{tr('生成范围')} #{idx + 1}",
                    (settings.text_x1, settings.text_y1, settings.text_x2, settings.text_y2),
                    "#4cc9f0",
                )
            )

        if group_range_bounds:
            rects.append(rect_item(tr("整体范围"), group_range_bounds, "#8cf0c8"))

        clip_shapes = sampled_clip_shapes(result_events)
        total_clip_count = sum(
            len(list(re.finditer(r"\\i?clip\(", getattr(evt, "text", "") or "")))
            for evt in result_events
        )
        summary = [
            f"{tr('调试覆盖')}: {len(selected_indices)} {tr('行')} -> {len(result_events)} {tr('行')}",
            f"clips: {total_clip_count} / {tr('显示')} {len(clip_shapes)}",
            f"tags: {', '.join(enabled_tags) if enabled_tags else '-'}",
            f"mode: {self._mode_combo.currentText()} angle={self._angle_spin.value():.1f}",
        ]
        if group_range_bounds:
            summary.append(f"{tr('整体范围')}: on")

        return {
            "enabled": bool(self._debug_overlay_enabled or force),
            "play_res": (self._ass_file.play_res_x, self._ass_file.play_res_y),
            "rects": rects,
            "clips": clip_shapes,
            "summary": summary,
            "total_clip_count": total_clip_count,
        }

    def _open_range_debug_dialog(self):
        rows = self._build_range_debug_rows()
        if not rows:
            QMessageBox.information(
                self,
                tr("范围调试"),
                tr("当前没有可调试的选中字幕行。"),
            )
            return
        dialog = RangeDebugDialog(rows, self)
        dialog.exec()

    def _build_range_debug_rows(self) -> list[dict[str, object]]:
        base_settings = self._build_settings()
        if not base_settings or not self._ass_file:
            return []

        selected_indices = self._selected_event_indices()
        use_group_bounds = self._line_group_enabled(selected_indices)
        group_range_bounds = self._merged_bounds_rect(selected_indices) if use_group_bounds else None
        rows: list[dict[str, object]] = []

        for idx in selected_indices:
            if not (0 <= idx < len(self._source_events)):
                continue
            evt = self._source_events[idx]
            style = self._ass_file.get_style(evt.style)
            settings = self._settings_for_event(base_settings, idx, group_range_bounds)
            parsed = parse_tags_from_text(evt.text) or {}
            enabled_tags = {
                tag: cfg for tag, cfg in settings.tags.items() if getattr(cfg, "enabled", False)
            }
            base_meta = self._get_bounds_meta(idx)
            source_clip = extract_clip_bounds(evt.text)
            base_rect = (settings.text_x1, settings.text_y1, settings.text_x2, settings.text_y2)
            geom_context = None
            plan = None
            plan_error = ""

            try:
                base_rect, geom_context = self._debug_range_base_rect(
                    evt, style, parsed, settings, enabled_tags, base_meta
                )
                if enabled_tags:
                    plan = calculate_range_plan(
                        event=evt,
                        ass_file=self._ass_file,
                        parsed=parsed,
                        style=style,
                        settings=settings,
                        enabled_tags=enabled_tags,
                        base_rect=base_rect,
                        source_clip_bounds=source_clip,
                        geom_context=geom_context,
                        debug=RangeDebug(enabled=True),
                    )
            except Exception as exc:
                plan_error = str(exc)

            strip_count = 1
            strip_error = ""
            if enabled_tags:
                try:
                    strip_count = len(
                        generate_gradient(evt, style, settings, base_meta, self._ass_file)
                    )
                except Exception as exc:
                    strip_error = str(exc)

            row: dict[str, object] = {
                "line": idx + 1,
                "style": evt.style,
                "enabled_tags": sorted(enabled_tags),
                "mode": self._mode_combo.currentText(),
                "angle": self._angle_spin.value(),
                "step": self._step_spin.value(),
                "libass_bounds": BoundsController.parse_meta_rect(base_meta),
                "source_clip": source_clip,
                "base_bounds": base_rect,
                "group_bounds": group_range_bounds,
                "strip_count": strip_count,
                "strip_error": strip_error,
                "plan_error": plan_error,
            }
            if plan:
                row.update(
                    {
                        "range_source": plan.range_source,
                        "expanded_rect": plan.expanded_rect,
                        "clip_rect": plan.clip_rect,
                        "event_projected_range": plan.event_projected_range,
                        "rendered_range": plan.rendered_range,
                        "geometry_range": plan.geometry_range,
                        "needs_rendered_range": plan.needs_rendered_range,
                        "tag_projected_ranges": {
                            tag: values for tag, values in plan.tag_projected_ranges.items()
                        },
                        "group_projected_ranges": {
                            tag: values for tag, values in plan.group_projected_ranges.items()
                        },
                        "range_debug_steps": plan.debug.steps,
                    }
                )
            else:
                row["range_source"] = "-"
            rows.append(row)
        return rows

    def _debug_range_base_rect(
        self,
        event: ASSEvent,
        style,
        parsed: dict,
        settings: GradientSettings,
        enabled_tags: dict[str, object],
        base_meta: Optional[str],
    ):
        tx1, ty1, tx2, ty2 = (
            settings.text_x1,
            settings.text_y1,
            settings.text_x2,
            settings.text_y2,
        )
        geom_context = None
        if not base_meta:
            return (tx1, ty1, tx2, ty2), geom_context
        try:
            parts = base_meta.split(",")
            bx1, by1, bx2, by2 = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
            meta_pos_x, meta_pos_y = float(parts[5]), float(parts[6])
            actual_pos = parsed.get("pos")
            if actual_pos:
                render_x, render_y = actual_pos
            else:
                render_x, render_y = meta_pos_x, meta_pos_y
            meta_org_x = float(parts[7]) if len(parts) > 7 else meta_pos_x
            meta_org_y = float(parts[8]) if len(parts) > 8 else meta_pos_y
            geom_context = _range_build_geometry_context(
                event,
                style,
                parsed,
                bx1,
                by1,
                bx2,
                by2,
                meta_pos_x,
                meta_pos_y,
                render_x,
                render_y,
                meta_org_x,
                meta_org_y,
            )
            if any(tag in enabled_tags for tag in GEOMETRY_TAGS):
                nx1, ny1, nx2, ny2 = _range_compute_dynamic_geometry_bounds(
                    event,
                    style,
                    settings,
                    enabled_tags,
                    parsed,
                    bx1,
                    by1,
                    bx2,
                    by2,
                    meta_pos_x,
                    meta_pos_y,
                    render_x,
                    render_y,
                    meta_org_x,
                    meta_org_y,
                )
            else:
                nx1, ny1, nx2, ny2 = bx1, by1, bx2, by2

            min_px, max_px, min_py, max_py = self._debug_pos_range(
                parsed, style, settings, enabled_tags, render_x, render_y
            )
            tx1 = nx1 + min_px - render_x
            tx2 = nx2 + max_px - render_x
            ty1 = ny1 + min_py - render_y
            ty2 = ny2 + max_py - render_y
        except Exception:
            return (tx1, ty1, tx2, ty2), geom_context
        return (tx1, ty1, tx2, ty2), geom_context

    def _debug_pos_range(
        self,
        parsed: dict,
        style,
        settings: GradientSettings,
        enabled_tags: dict[str, object],
        render_x: float,
        render_y: float,
    ):
        cfg = enabled_tags.get("pos")
        if cfg and getattr(cfg, "nodes", None):
            xs: list[float] = []
            ys: list[float] = []
            for t in _range_coord_sample_points(cfg):
                value = _range_get_interpolated_value("pos", cfg, t, parsed, style, settings)
                if isinstance(value, tuple) and len(value) >= 2:
                    xs.append(float(value[0]))
                    ys.append(float(value[1]))
            if xs and ys:
                return min(xs), max(xs), min(ys), max(ys)
        return render_x, render_x, render_y, render_y

    # ── Undo / Redo ──────────────────────────────────────────────────────

    def _capture_state(self, description: str = "") -> UndoState:
        """Capture the current effect-editing state."""
        self._save_active_curve()
        self._save_active_animation_curve()
        state = UndoState(
            tag_configs=state_codec.serialize_tag_panel_configs(self._tag_panel),
            tag_curves=state_codec.serialize_curve_store(self._tag_curves),
            tag_modes=state_codec.serialize_curve_modes(self._tag_modes),
            curve_mirrors=copy.deepcopy(self._curve_mirrors),
            mode=self._mode_combo.currentText(),
            angle=self._angle_spin.value(),
            step=self._step_spin.value(),
            color_space=self._tag_panel._cs_combo.currentText(),
            path_sampling_smooth=self._tag_panel.get_path_smooth_map(),
            path_sampling_smooth_strength=self._tag_panel.get_path_smooth_strength_map(),
            merge_selected_lines=self._line_panel.merge_range_enabled(),
            group_range_tags=sorted(self._group_range_tags),
            animation_state=self._animation_panel_state(),
            animation_curves=state_codec.serialize_curve_store(self._animation_curves),
            animation_modes=state_codec.serialize_curve_modes(self._animation_modes),
            animation_curve_mirrors=copy.deepcopy(self._animation_curve_mirrors),
            sampling_paths=serialize_path_state(self._sampling_paths),
            selected_lines=self._selected_event_indices(fallback=False),
            active_event_idx=self._active_event_idx,
            active_curve_key=self._active_curve_key or "",
            description=description,
        )
        return state

    def _push_undo(self, description: str = ""):
        """Capture current state and push to undo stack."""
        if self._undo_timer.isActive():
            self._undo_timer.stop()

        state = self._capture_state(description)
        signature = self._state_signature(state)
        if signature == self._last_undo_signature:
            return
        self._last_undo_signature = signature
        self._undo.push(state)

    def _state_signature(self, state: UndoState) -> str:
        data = dict(state.__dict__)
        data["description"] = ""
        return repr(data)

    def _save_preset(self):
        default_path = "gradient_preset.ggpreset"
        if self._input_path:
            default_path = str(Path(self._input_path).with_suffix(".ggpreset"))

        path = preset_io.select_save_path(self, default_path)
        if not path:
            return

        state = self._capture_state(tr("保存预设"))
        try:
            preset_io.write_preset(path, state_codec.preset_from_state(state))
            preset_io.remember_recent_preset(path)
            self._refresh_recent_presets_menu()
            self._status.showMessage(f"{tr('已保存预设')}: {path}")
        except Exception as e:
            QMessageBox.critical(self, tr("保存预设失败"), str(e))

    def _load_preset(self):
        default_dir = str(Path(self._input_path).parent) if self._input_path else ""

        try:
            result = preset_io.select_and_read_preset(self, default_dir)
            if result is None:
                return
            path, state_data = result
            if not self._confirm_preset_load(path, state_data):
                return
            state = state_codec.state_from_preset_data(state_data)
        except Exception as e:
            QMessageBox.critical(self, tr("加载预设失败"), str(e))
            return

        # A preset describes the effect. Keep the user's current line selection.
        state.selected_lines = self._selected_event_indices(fallback=False)
        state.active_event_idx = self._active_event_idx
        if not state.active_curve_key:
            state.active_curve_key = self._active_curve_key or ""

        self._flush_pending_undo()
        self._push_undo(tr("加载预设前"))
        self._restore_state(state)
        self._last_undo_signature = None
        self._push_undo(f"加载预设: {Path(path).stem}")
        preset_io.remember_recent_preset(path)
        self._refresh_recent_presets_menu()
        self._status.showMessage(f"{tr('已加载预设')}: {path}")

    def _load_recent_preset(self, path: str):
        try:
            state_data = preset_io.read_preset(path)
            if not self._confirm_preset_load(path, state_data):
                return
            state = state_codec.state_from_preset_data(state_data)
        except Exception as e:
            QMessageBox.critical(self, tr("加载预设失败"), str(e))
            self._refresh_recent_presets_menu()
            return

        state.selected_lines = self._selected_event_indices(fallback=False)
        state.active_event_idx = self._active_event_idx
        if not state.active_curve_key:
            state.active_curve_key = self._active_curve_key or ""

        self._flush_pending_undo()
        self._push_undo(tr("加载预设前"))
        self._restore_state(state)
        self._last_undo_signature = None
        self._push_undo(f"加载预设: {Path(path).stem}")
        preset_io.remember_recent_preset(path)
        self._refresh_recent_presets_menu()
        self._status.showMessage(f"{tr('已加载最近预设')}: {path}")

    def _confirm_preset_load(self, path: str, state_data: dict) -> bool:
        summary = preset_io.describe_preset(state_data)
        reply = QMessageBox.question(
            self,
            tr("加载预设"),
            f"{Path(path).name}\n\n{summary}\n\n{tr('是否加载这个预设？')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _refresh_recent_presets_menu(self):
        if not hasattr(self, "_recent_preset_menu"):
            return
        self._recent_preset_menu.clear()
        recent = preset_io.read_recent_presets()
        self._recent_preset_btn.setEnabled(bool(recent))
        if not recent:
            action = self._recent_preset_menu.addAction(tr("没有最近预设"))
            action.setEnabled(False)
            return
        for path in recent:
            action = self._recent_preset_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self._load_recent_preset(p))

    def _debug_curve_nodes(self, nodes) -> list[dict[str, object]]:
        if not nodes:
            return []
        try:
            return state_codec.serialize_nodes(self._clone_nodes(list(nodes)))
        except Exception:
            return []

    def _debug_curve_mode(self, mode) -> Optional[str]:
        if isinstance(mode, InterpolationMode):
            return mode.value
        if mode is None:
            return None
        return str(mode)

    def _build_tag_sync_debug(
        self,
        tag: str,
        base_settings: Optional[GradientSettings] = None,
    ) -> dict[str, object]:
        info = TAG_INFO.get(tag, {})
        tag_type = str(info.get("type", "numeric"))
        row = self._tag_panel.get_row(tag) if hasattr(self, "_tag_panel") else None
        row_config = row.get_config() if row else None
        row_inputs = None
        if row:
            try:
                row_inputs = [inp.value() for inp in row._inputs]
            except Exception:
                row_inputs = None

        curve_keys: dict[str, dict[str, object]] = {}
        for key in curve_keys_for_tag(tag):
            axis = curve_key_axis(key)
            cached_nodes = self._tag_curves.get(key)
            cached_mode = self._tag_modes.get(key)
            editor_active = key == self._active_curve_key and hasattr(self, "_curve_editor")
            editor_nodes = self._curve_editor.get_nodes() if editor_active else None
            editor_mode = self._curve_editor.get_mode() if editor_active else None

            settings_nodes = None
            settings_mode = None
            if base_settings is not None:
                settings_cfg = base_settings.tags.get(tag)
                if settings_cfg is not None:
                    if tag_type == "coord":
                        if axis == "y":
                            settings_nodes = getattr(settings_cfg, "coord_y_nodes", None)
                            settings_mode = getattr(settings_cfg, "coord_y_mode", None)
                        else:
                            settings_nodes = getattr(settings_cfg, "nodes", None)
                            settings_mode = getattr(settings_cfg, "mode", None)
                    else:
                        settings_nodes = getattr(settings_cfg, "nodes", None)
                        settings_mode = getattr(settings_cfg, "mode", None)

            curve_keys[key] = {
                "axis": axis,
                "cached": {
                    "mode": self._debug_curve_mode(cached_mode),
                    "nodes": self._debug_curve_nodes(cached_nodes),
                },
                "editor": {
                    "active": editor_active,
                    "mode": self._debug_curve_mode(editor_mode),
                    "nodes": self._debug_curve_nodes(editor_nodes),
                },
                "settings": {
                    "mode": self._debug_curve_mode(settings_mode),
                    "nodes": self._debug_curve_nodes(settings_nodes),
                },
            }

        path_state = None
        if tag in {"1c", "2c", "3c", "4c"}:
            active_paths = self._sampling_paths.get(self._active_event_idx, {})
            path_set = active_paths.get(tag)
            if path_set:
                path_state = {
                    "is_active": bool(path_set.is_active),
                    "removed_original": bool(path_set.removed_original),
                    "sampling_frame": path_set.sampling_frame,
                    "ass_path": path_set.to_ass_path(),
                    "raw": path_set.to_raw(),
                }

        return {
            "tag": tag,
            "label": str(info.get("label", f"\\{tag}")),
            "type": tag_type,
            "enabled": bool(row_config.enabled) if row_config is not None else False,
            "inputs": row_inputs,
            "row_config": state_codec.serialize_tag_config(row_config) if row_config is not None else None,
            "curve_keys": curve_keys,
            "path_state": path_state,
            "active_tag": self._active_tag,
            "active_curve_key": self._active_curve_key,
        }

    def _export_debug_report(self):
        default_path = "gradient_debug_report.json"
        if self._input_path:
            default_path = str(Path(self._input_path).with_suffix(".gradient-debug.json"))
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("导出调试包"),
            default_path,
            "GradientGUI Debug Report (*.json);;JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        if Path(path).suffix.lower() != ".json":
            path += ".json"

        try:
            report = self._build_debug_report()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            self._status.showMessage(f"{tr('已导出调试包')}: {path}")
        except Exception as e:
            QMessageBox.critical(self, tr("导出调试包失败"), str(e))

    def _build_debug_report(self) -> dict:
        self._save_active_curve()
        state = self._capture_state(tr("调试报告"))
        selected = self._selected_event_indices()
        active_evt = self._get_active_event()
        video_path = self._resolve_video_path()
        base_settings = self._build_settings() if self._ass_file else None
        dependency = None
        if self._ass_file:
            health = self._startup_health
            if health is None:
                health = run_startup_health_check(
                    video_path=video_path,
                    video_time=self._current_video_time(active_evt) if active_evt else None,
                    video_frame=self._ass_file.video_position,
                    frame_sampler=self._path_frame_sampler,
                    check_video_frame=False,
                )
            dependency = [
                {
                    "name": item.name,
                    "ok": item.ok,
                    "message": item.message,
                    "detail": item.detail,
                }
                for item in health.items
            ]

        tag_sync_debug = {
            tag: self._build_tag_sync_debug(tag, base_settings)
            for tag in TAG_INFO.keys()
        }

        source_events = []
        for idx in selected:
            if 0 <= idx < len(self._source_events):
                evt = self._source_events[idx]
                source_events.append(
                    {
                        "index": idx,
                        "start": evt.start,
                        "end": evt.end,
                        "style": evt.style,
                        "text": evt.text,
                        "bounds_meta": self._get_bounds_meta(idx),
                        "source_clip_bounds": extract_clip_bounds(evt.text),
                    }
                )

        debug_overlay = self._last_preview_debug_data
        if not debug_overlay and self._last_preview_result_events and self._ass_file:
            base_settings = self._build_settings()
            if base_settings:
                use_group_bounds = self._line_group_enabled(selected)
                group_range_bounds = self._merged_bounds_rect(selected) if use_group_bounds else None
                debug_overlay = self._build_preview_debug_data(
                    selected,
                    self._last_preview_result_events,
                    group_range_bounds,
                    base_settings,
                    force=True,
                )

        return {
            "format": "GradientGUI Debug Report",
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_path": self._input_path,
            "output_path": self._output_path,
            "video_path": video_path,
            "play_res": (
                (self._ass_file.play_res_x, self._ass_file.play_res_y)
                if self._ass_file else None
            ),
            "dependency_health": dependency,
            "preset_state": state_codec.preset_from_state(state),
            "tag_sync_debug": tag_sync_debug,
            "active_tag_sync_debug": tag_sync_debug.get(self._active_tag) if self._active_tag else None,
            "selected_indices": selected,
            "active_event_idx": self._active_event_idx,
            "source_events": source_events,
            "preview_result_sample": [
                {
                    "index": idx,
                    "text": evt.text,
                }
                for idx, evt in enumerate(self._last_preview_result_events[:8])
            ],
            "preview": self._last_preview_summary,
            "debug_overlay": debug_overlay,
            "frame_sampler_error": self._path_frame_sampler.last_error,
        }

    def _restore_state(self, state: UndoState):
        """Restore UI from an UndoState."""
        if self._undo_timer.isActive():
            self._undo_timer.stop()
        previous_undo_suspended = self._undo_suspended
        self._undo_suspended = True

        state_codec.restore_tag_panel_configs(self._tag_panel, state.tag_configs)

        self._tag_curves = state_codec.deserialize_curve_store(state.tag_curves)
        self._tag_modes = state_codec.deserialize_curve_modes(state.tag_modes)
        self._curve_mirrors = copy.deepcopy(getattr(state, "curve_mirrors", {}))
        self._animation_curves = state_codec.deserialize_curve_store(
            getattr(state, "animation_curves", {})
        )
        self._animation_modes = state_codec.deserialize_curve_modes(
            getattr(state, "animation_modes", {})
        )
        self._animation_curve_mirrors = copy.deepcopy(
            getattr(state, "animation_curve_mirrors", {})
        )
        self._sampling_paths = normalize_path_state(
            getattr(state, "sampling_paths", {})
        )
        self._refresh_path_buttons()

        # Restore mode/angle/step without fanning out extra change signals.
        restore_widgets = [
            self._mode_combo,
            self._angle_spin,
            self._step_spin,
            self._tag_panel._cs_combo,
        ]
        restore_blockers = [widget.blockSignals(True) for widget in restore_widgets]
        try:
            self._mode_combo.setCurrentText(state.mode)
            self._angle_spin.setValue(state.angle)
            self._step_spin.setValue(state.step)
            self._tag_panel._cs_combo.setCurrentText(getattr(state, "color_space", "RGB"))
            self._tag_panel.set_path_smooth_map(getattr(state, "path_sampling_smooth", {}))
            self._tag_panel.set_path_smooth_strength_map(
                getattr(state, "path_sampling_smooth_strength", {})
            )
            self._line_panel.set_merge_range_enabled(getattr(state, "merge_selected_lines", False))
        finally:
            for widget, previous in zip(reversed(restore_widgets), reversed(restore_blockers)):
                widget.blockSignals(previous)
        animation_state = getattr(state, "animation_state", {}) or {}
        saved_enabled_tags = set(animation_state.get("enabled_tags", []) or [])
        if not saved_enabled_tags and bool(animation_state.get("enabled", False)):
            saved_enabled_tags = {
                tag for tag, cfg in getattr(state, "tag_configs", {}).items()
                if bool(cfg.get("enabled", False))
            }
        self._animation_enabled_tags = saved_enabled_tags
        raw_frame_steps = animation_state.get("frame_steps", {}) or {}
        frame_steps: dict[str, int] = {}
        if isinstance(raw_frame_steps, dict):
            for tag, value in raw_frame_steps.items():
                if tag not in TAG_INFO:
                    continue
                try:
                    frame_steps[tag] = max(1, int(value or 1))
                except (TypeError, ValueError):
                    frame_steps[tag] = 1
        if not frame_steps:
            try:
                legacy_frame_step = max(1, int(animation_state.get("frame_step", 1) or 1))
            except (TypeError, ValueError):
                legacy_frame_step = 1
            frame_steps = {
                tag: legacy_frame_step
                for tag in saved_enabled_tags
                if tag in TAG_INFO
            }
        self._animation_frame_steps = frame_steps
        raw_seam_lengths = animation_state.get("seam_blend_lengths", {}) or {}
        seam_lengths: dict[str, int] = {}
        if isinstance(raw_seam_lengths, dict):
            for tag, value in raw_seam_lengths.items():
                if tag not in TAG_INFO:
                    continue
                try:
                    length = int(value or 0)
                except (TypeError, ValueError):
                    length = 0
                if length > 0:
                    seam_lengths[tag] = length
        if not seam_lengths:
            try:
                legacy_length = int(animation_state.get("seam_blend_length", 0) or 0)
            except (TypeError, ValueError):
                legacy_length = 0
            if legacy_length > 0:
                seam_lengths = {
                    tag: legacy_length
                    for tag in saved_enabled_tags
                    if tag in TAG_INFO
                }
        self._animation_seam_blend_lengths = seam_lengths
        self._animation_panel.restore_state(animation_state)
        saved_group_tags = getattr(state, "group_range_tags", None)
        self._group_range_tags = (
            set(saved_group_tags)
            if saved_group_tags is not None
            else set(TAG_INFO.keys())
        )

        active_idx = max(0, min(getattr(state, "active_event_idx", 0), len(self._source_events) - 1))
        self._active_event_idx = active_idx
        self._line_panel.restore_selection(
            active_idx,
            getattr(state, "selected_lines", []),
            len(self._source_events),
        )

        saved_curve_key = getattr(state, "active_curve_key", "") or self._active_curve_key
        self._active_curve_key = saved_curve_key
        self._active_tag = curve_key_tag(saved_curve_key)
        self._sync_tabs()

        # Reload active curve
        if self._active_curve_key and self._active_curve_key in self._tag_curves:
            tag = curve_key_tag(self._active_curve_key) or self._active_curve_key
            tag_type = TAG_INFO.get(tag, {}).get("type")
            is_color = tag_type == "color"
            is_text = tag_type == "text"
            self._curve_editor.set_nodes(
                self._tag_curves[self._active_curve_key], is_color, is_text
            )
            self._curve_editor.set_mode(
                self._tag_modes.get(self._active_curve_key, InterpolationMode.LINEAR)
            )
            self._curve_editor.set_mirror_state(
                *self._curve_mirrors.get(self._active_curve_key, (False, False))
            )
            self._update_curve_color_preview()
            self._sync_animation_curve_editor()

        self._last_undo_signature = self._state_signature(state)
        self._schedule_preview()
        self._undo_suspended = previous_undo_suspended

    def _do_undo(self):
        self._flush_pending_undo()
        state = self._undo.undo()
        if state:
            self._restore_state(state)
            self._status.showMessage(f"{tr('撤销')}: {state.description}")

    def _do_redo(self):
        self._flush_pending_undo()
        state = self._undo.redo()
        if state:
            self._restore_state(state)
            self._status.showMessage(f"{tr('重做')}: {state.description}")

    def _update_undo_buttons(self):
        self._undo_btn.setEnabled(self._undo.can_undo)
        self._redo_btn.setEnabled(self._undo.can_redo)

    # ── Apply / Cancel ───────────────────────────────────────────────────

    def _apply_and_close(self):
        base_settings = self._build_settings()
        if not base_settings or not self._output_path or not self._ass_file:
            return

        all_result_events = []
        selected_list = self._selected_event_indices()
        selected_indices = set(selected_list)
        use_group_bounds = self._line_group_enabled(selected_list)
        group_range_bounds = self._merged_bounds_rect(selected_list) if use_group_bounds else None

        # The Lua launcher replaces every line it passed to the GUI. Keep
        # unselected rows unchanged so narrowing the GUI selection never deletes
        # source lines from Aegisub.
        for i, evt in enumerate(self._source_events):
            if i not in selected_indices:
                all_result_events.append(evt)
                continue
            try:
                result = generate_gradient_events(
                    self._source_events,
                    [i],
                    self._ass_file,
                    base_settings,
                    lambda settings, idx: self._settings_for_event(
                        settings, idx, group_range_bounds
                    ),
                    self._get_bounds_meta,
                )
                all_result_events.extend(result)
            except GradientGenerationError as e:
                self._show_generation_error(
                    e,
                    base_settings,
                    selected_list,
                    group_range_bounds,
                )
                return

        # Write output ASS
        try:
            out = ASSFile(events_format=self._ass_file.events_format)
            out.events = all_result_events
            out.write_events_only(self._output_path)
            self._status.showMessage(tr("已保存输出"))
        except Exception as e:
            QMessageBox.critical(self, tr("错误"), f"{tr('保存失败')}:\n{e}")
            return

        self._video.cleanup()
        self.close()

    def _cancel(self):
        if self._output_path and os.path.exists(self._output_path):
            try:
                os.remove(self._output_path)
            except Exception:
                pass
        self._video.cleanup()
        self.close()

    def closeEvent(self, event):
        if hasattr(self, "_loop_preview_timer"):
            self._loop_preview_timer.stop()
        if getattr(self, "_loop_preview_native", False):
            self._video.stop_loop_playback(pause=True)
            self._loop_preview_native = False
        self._video.cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_video"):
            self._video.refresh_subtitle()
