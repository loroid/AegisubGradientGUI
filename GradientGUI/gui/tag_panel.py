"""
Tag settings panel for gradient configuration.

v3: Reverted intermediate value nodes. Simple Start/End values.
Entire row is clickable to select as active curve.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QCheckBox, QLabel, QLineEdit, QPushButton, QFrame,
    QDoubleSpinBox, QSpinBox, QColorDialog, QComboBox, QDialog,
)
from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import QColor, QPixmap, QIcon, QGuiApplication, QPainter, QPen, QBrush

from engine.tag_parser import TAG_INFO
from engine.models import TagGradientConfig, ColorSpace
from gui.i18n import group_label, set_button_text, tag_label, tr


def _bgr_to_qcolor(bgr: str) -> QColor:
    bgr = bgr.ljust(6, "0")
    b = int(bgr[0:2], 16)
    g = int(bgr[2:4], 16)
    r = int(bgr[4:6], 16)
    return QColor(r, g, b)

def _qcolor_to_bgr(c: QColor) -> str:
    return f"{c.blue():02X}{c.green():02X}{c.red():02X}"

def _make_color_icon(bgr: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(_bgr_to_qcolor(bgr))
    return QIcon(pm)


class ScreenColorPicker(QWidget):
    """Fullscreen local screen color sampler."""
    color_picked = Signal(QColor)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(None)
        self._parent_window = parent
        self._screenshots = []
        self._hover_pos = None
        self._hover_color = QColor("#ffffff")

        self._capture_screens()
        primary = QGuiApplication.primaryScreen()
        virtual_rect = primary.virtualGeometry() if primary else QRectF(0, 0, 1, 1).toRect()
        self.setGeometry(virtual_rect)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _capture_screens(self):
        self._screenshots.clear()
        for screen in QGuiApplication.screens():
            pixmap = screen.grabWindow(0)
            self._screenshots.append((screen.geometry(), pixmap.toImage()))

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabMouse()
        self.grabKeyboard()

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _sample_at(self, global_pos) -> QColor:
        for geometry, image in self._screenshots:
            if not geometry.contains(global_pos) or image.isNull():
                continue
            scale_x = image.width() / max(1, geometry.width())
            scale_y = image.height() / max(1, geometry.height())
            x = int((global_pos.x() - geometry.x()) * scale_x)
            y = int((global_pos.y() - geometry.y()) * scale_y)
            if 0 <= x < image.width() and 0 <= y < image.height():
                return QColor(image.pixel(x, y))
        return QColor()

    def _finish(self):
        self.releaseMouse()
        self.releaseKeyboard()
        self.close()

    def mouseMoveEvent(self, event):
        self._hover_pos = self._event_global_pos(event)
        color = self._sample_at(self._hover_pos)
        if color.isValid():
            self._hover_color = color
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            color = self._sample_at(self._event_global_pos(event))
            if color.isValid():
                self.color_picked.emit(color)
            self._finish()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self._finish()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self._finish()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        if self._hover_pos is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        local = self.mapFromGlobal(self._hover_pos)
        preview = QRectF(local.x() + 16, local.y() + 16, 94, 42)
        if preview.right() > self.width():
            preview.moveRight(local.x() - 16)
        if preview.bottom() > self.height():
            preview.moveBottom(local.y() - 16)

        painter.setPen(QPen(QColor(255, 255, 255, 170), 1))
        painter.setBrush(QBrush(QColor(15, 15, 35, 220)))
        painter.drawRoundedRect(preview, 4, 4)

        swatch = QRectF(preview.left() + 8, preview.top() + 8, 24, 24)
        painter.setBrush(QBrush(self._hover_color))
        painter.drawRect(swatch)

        painter.setPen(QColor(230, 230, 240))
        painter.drawText(
            QRectF(preview.left() + 38, preview.top() + 8, 50, 24),
            Qt.AlignmentFlag.AlignVCenter,
            f"#{self._hover_color.red():02X}{self._hover_color.green():02X}{self._hover_color.blue():02X}",
        )


class ValueInput(QWidget):
    """A widget for inputting a single value based on tag type."""
    changed = Signal()

    def __init__(self, tag_type: str, tag: str = "", parent=None):
        super().__init__(parent)
        self.tag_type = tag_type
        self.tag = tag
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4 if tag_type == "color" else 2 if tag_type == "coord" else 0)

        if tag_type == "color":
            self._btn = QPushButton()
            self._btn.setFixedSize(46, 24)
            self._btn.setIconSize(QSize(36, 18))
            self._btn.clicked.connect(self._pick)
            self._btn.setToolTip(tr("打开颜色选择器"))
            self._picker_btn = QPushButton(tr("取"))
            self._picker_btn.setFixedHeight(24)
            set_button_text(self._picker_btn, "取", minimum=52, padding=18)
            self._picker_btn.setToolTip(tr("从屏幕拾取颜色，左键确认，右键或 Esc 取消"))
            self._picker_btn.clicked.connect(self._pick_screen)
            self._val = "FFFFFF"
            self._screen_picker = None
            self._update_btn()
            layout.addWidget(self._btn)
            layout.addWidget(self._picker_btn)
        elif tag_type == "alpha":
            self._spin = QSpinBox()
            self._spin.setRange(0, 255)
            self._spin.setFixedWidth(50)
            self._spin.valueChanged.connect(lambda: self.changed.emit())
            layout.addWidget(self._spin)
        elif tag_type == "coord":
            self._cx = QDoubleSpinBox()
            self._cx.setRange(-9999, 9999)
            self._cx.setFixedWidth(65)
            self._cy = QDoubleSpinBox()
            self._cy.setRange(-9999, 9999)
            self._cy.setFixedWidth(65)
            self._cx.valueChanged.connect(lambda: self.changed.emit())
            self._cy.valueChanged.connect(lambda: self.changed.emit())
            layout.addWidget(self._cx)
            layout.addWidget(self._cy)
        elif tag_type == "text":
            self._text = QLineEdit()
            self._text.setFixedWidth(130)
            self._text.textChanged.connect(lambda: self.changed.emit())
            layout.addWidget(self._text)
        else:
            self._spin = QDoubleSpinBox()
            self._spin.setRange(-9999, 9999)
            self._spin.setDecimals(2)
            wide_numeric_tags = {"fscx", "fscy", "fs"}
            self._spin.setFixedWidth(88 if self.tag in wide_numeric_tags else 72)
            self._spin.valueChanged.connect(lambda: self.changed.emit())
            layout.addWidget(self._spin)

    def value(self):
        if self.tag_type == "color":
            return self._val
        elif self.tag_type == "alpha":
            return self._spin.value()
        elif self.tag_type == "coord":
            return (self._cx.value(), self._cy.value())
        elif self.tag_type == "text":
            return self._text.text()
        else:
            return self._spin.value()

    def set_value(self, val):
        if self.tag_type == "color":
            self._val = str(val) if isinstance(val, str) else "FFFFFF"
            self._update_btn()
        elif self.tag_type == "alpha":
            prev = self._spin.blockSignals(True)
            try:
                if isinstance(val, str):
                    try:
                        v = int(val, 16)
                    except ValueError:
                        v = 0
                    self._spin.setValue(v)
                else:
                    self._spin.setValue(int(val) if val is not None else 0)
            finally:
                self._spin.blockSignals(prev)
        elif self.tag_type == "coord":
            prev_x = self._cx.blockSignals(True)
            prev_y = self._cy.blockSignals(True)
            try:
                if isinstance(val, tuple):
                    self._cx.setValue(val[0])
                    self._cy.setValue(val[1])
                else:
                    self._cx.setValue(0.0)
                    self._cy.setValue(0.0)
            finally:
                self._cx.blockSignals(prev_x)
                self._cy.blockSignals(prev_y)
        elif self.tag_type == "text":
            prev = self._text.blockSignals(True)
            try:
                self._text.setText("" if val is None else str(val))
            finally:
                self._text.blockSignals(prev)
        else:
            prev = self._spin.blockSignals(True)
            try:
                self._spin.setValue(float(val) if val is not None else 0)
            finally:
                self._spin.blockSignals(prev)

    def _pick(self):
        initial = _bgr_to_qcolor(self._val)
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle(tr("选择颜色"))
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.currentColorChanged.connect(self._preview_color)

        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            color = dialog.selectedColor()
            if color.isValid():
                self._set_color(color)
        else:
            self._update_btn()

    def _pick_screen(self):
        picker = ScreenColorPicker(self.window())
        self._screen_picker = picker

        def apply_color(color: QColor):
            self._set_color(color)
            self._screen_picker = None

        def clear_picker(*_args):
            self._screen_picker = None

        picker.color_picked.connect(apply_color)
        picker.cancelled.connect(clear_picker)
        picker.destroyed.connect(clear_picker)
        picker.start()

    def _set_color(self, color: QColor):
        self._val = _qcolor_to_bgr(color)
        self._update_btn()
        self.changed.emit()

    def _preview_color(self, color: QColor):
        if color.isValid():
            self._apply_btn_color(color)

    def _apply_btn_color(self, color: QColor):
        self._btn.setIcon(_make_color_icon(_qcolor_to_bgr(color)))
        self._btn.setStyleSheet(
            "QPushButton {"
            f" background-color: {color.name()};"
            " border: 1px solid #666680;"
            " border-radius: 2px;"
            " }"
        )
        self._btn.update()

    def _update_btn(self):
        self._apply_btn_color(_bgr_to_qcolor(self._val))

    def retranslate_ui(self):
        if self.tag_type == "color":
            self._btn.setToolTip(tr("打开颜色选择器"))
            set_button_text(self._picker_btn, "取", minimum=52, padding=18)
            self._picker_btn.setToolTip(tr("从屏幕拾取颜色，左键确认，右键或 Esc 取消"))


class TagRowWidget(QFrame):
    changed = Signal(str)
    selected = Signal(str)
    path_requested = Signal(str)

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.info = TAG_INFO.get(tag, {})
        self.tag_type = self.info.get("type", "numeric")
        self._config = TagGradientConfig(tag=tag)
        self._is_active = False
        self._path_active = False

        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.update_style()

        is_path_color_tag = self.tag_type == "color" and tag in {"1c", "2c", "3c", "4c"}
        if is_path_color_tag:
            outer_layout = QVBoxLayout(self)
            outer_layout.setContentsMargins(6, 4, 6, 4)
            outer_layout.setSpacing(4)
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            outer_layout.addLayout(layout)
        else:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(6)

        self._check = QCheckBox()
        self._check.setFixedWidth(20)
        self._check.toggled.connect(self._on_toggle)
        layout.addWidget(self._check)

        self._label = QLabel(tag_label(tag, self.info.get("label", f"\\{tag}")))
        self._label.setFixedWidth(140)
        layout.addWidget(self._label)

        self._path_btn: Optional[QPushButton] = None
        self._path_smooth: Optional[QCheckBox] = None
        self._path_smooth_strength: Optional[QDoubleSpinBox] = None
        if is_path_color_tag:
            self._path_btn = QPushButton(tr("路径"))
            set_button_text(self._path_btn, "路径", minimum=58, padding=26)
            self._path_btn.setToolTip(tr("使用视频帧上的贝塞尔路径采集颜色"))
            self._path_btn.clicked.connect(lambda: self.path_requested.emit(self.tag))
            layout.addWidget(self._path_btn)

            self._path_smooth = QCheckBox(tr("平滑"))
            self._fit_path_smooth_width()
            self._path_smooth.setStyleSheet("QCheckBox { background: transparent; }")
            self._path_smooth.setToolTip(tr("路径采色平滑过渡；关闭时使用原色彩"))
            self._path_smooth.toggled.connect(self._on_path_smooth_toggled)
            layout.addWidget(self._path_smooth)

            self._path_smooth_strength = QDoubleSpinBox()
            self._path_smooth_strength.setRange(0.0, 1.0)
            self._path_smooth_strength.setDecimals(2)
            self._path_smooth_strength.setSingleStep(0.1)
            self._path_smooth_strength.setAccelerated(False)
            self._path_smooth_strength.setValue(1.0)
            self._path_smooth_strength.setFixedWidth(58)
            self._path_smooth_strength.setToolTip(tr("路径采色平滑力度：0=原色阶梯，1=完全平滑"))
            self._path_smooth_strength.valueChanged.connect(lambda _value=0.0: self.changed.emit(self.tag))
            self._path_smooth_strength.setEnabled(False)
            layout.addWidget(self._path_smooth_strength)

        self._values_layout = QHBoxLayout()
        self._values_layout.setContentsMargins(0, 0, 0, 0)
        self._values_layout.setSpacing(4)
        if is_path_color_tag:
            self._values_layout.addSpacing(20 + 6 + 140 + 6)
            outer_layout.addLayout(self._values_layout)
            layout.addStretch()
        else:
            layout.addLayout(self._values_layout)
            layout.addStretch()
        
        self._inputs: list[ValueInput] = []
        self._syncing = False
        
        # Initialize with Start and End
        self._add_input_widget()
        self._add_input_widget()
        if is_path_color_tag:
            self._values_layout.addStretch()

    def update_style(self):
        bg = "#3a3a6a" if self._is_active else "#1e1e36"
        border = "#6366f1" if self._is_active else "#333"
        self.setStyleSheet(f"""
            TagRowWidget {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 4px;
            }}
            TagRowWidget:hover {{ border-color: #5555aa; }}
        """)

    def set_active(self, active: bool):
        self._is_active = active
        self.update_style()

    def mousePressEvent(self, event):
        self.selected.emit(self.tag)
        super().mousePressEvent(event)

    def _on_toggle(self, checked: bool):
        self._config.enabled = checked
        if checked:
            self.selected.emit(self.tag)
        self.changed.emit(self.tag)

    def set_enabled_checked(self, checked: bool):
        self._config.enabled = bool(checked)
        prev = self._check.blockSignals(True)
        try:
            self._check.setChecked(bool(checked))
        finally:
            self._check.blockSignals(prev)

    def set_path_active(self, active: bool):
        self._path_active = bool(active)
        if self._path_btn:
            set_button_text(
                self._path_btn,
                "路径✓" if active else "路径",
                minimum=58,
                padding=26,
            )
            self._path_btn.setStyleSheet(
                "QPushButton { color: #8cf0c8; font-weight: bold; }"
                if active else ""
            )

    def path_smooth_enabled(self) -> bool:
        return bool(self._path_smooth and self._path_smooth.isChecked())

    def set_path_smooth_enabled(self, enabled: bool):
        if self._path_smooth:
            prev = self._path_smooth.blockSignals(True)
            try:
                self._path_smooth.setChecked(bool(enabled))
            finally:
                self._path_smooth.blockSignals(prev)
        if self._path_smooth_strength:
            self._path_smooth_strength.setEnabled(bool(enabled))

    def path_smooth_strength(self) -> float:
        if not self._path_smooth_strength:
            return 1.0
        return float(self._path_smooth_strength.value())

    def set_path_smooth_strength(self, strength: float):
        if self._path_smooth_strength:
            try:
                value = float(strength)
            except (TypeError, ValueError):
                value = 1.0
            prev = self._path_smooth_strength.blockSignals(True)
            try:
                self._path_smooth_strength.setValue(max(0.0, min(1.0, value)))
            finally:
                self._path_smooth_strength.blockSignals(prev)

    def _on_path_smooth_toggled(self, checked: bool):
        if self._path_smooth_strength:
            self._path_smooth_strength.setEnabled(bool(checked))
        self.changed.emit(self.tag)

    def _on_val_changed(self):
        if not self._syncing:
            self.changed.emit(self.tag)
            
    def _add_input_widget(self):
        inp = ValueInput(self.tag_type, self.tag)
        inp.changed.connect(self._on_val_changed)
        if self._inputs:
            lbl = QLabel("→")
            self._values_layout.addWidget(lbl)
        self._values_layout.addWidget(inp)
        self._inputs.append(inp)
        


    def set_start_value(self, val):
        if self._inputs:
            self._inputs[0].set_value(val)

    def set_end_value(self, val):
        if self._inputs:
            self._inputs[-1].set_value(val)

    def get_config(self) -> TagGradientConfig:
        v_start = self._inputs[0].value()
        v_end = self._inputs[1].value()

        if self.tag_type == "coord":
            sx, sy = v_start if isinstance(v_start, tuple) else (0.0, 0.0)
            ex, ey = v_end if isinstance(v_end, tuple) else (sx, sy)
            from engine.interpolation import make_default_nodes
            if not self._config.nodes or len(self._config.nodes) < 2:
                self._config.nodes = make_default_nodes(start_y=float(sx), end_y=float(ex))
            else:
                self._config.nodes[0].y = float(sx)
                self._config.nodes[-1].y = float(ex)
            if not self._config.coord_y_nodes or len(self._config.coord_y_nodes) < 2:
                self._config.coord_y_nodes = make_default_nodes(start_y=float(sy), end_y=float(ey))
            else:
                self._config.coord_y_nodes[0].y = float(sy)
                self._config.coord_y_nodes[-1].y = float(ey)
            return self._config

        if self.tag_type == "text":
            if not self._config.nodes or len(self._config.nodes) < 2:
                from engine.interpolation import make_default_nodes
                self._config.nodes = make_default_nodes(start_y=0.0, end_y=0.0)
            self._config.nodes[0].value_str = str(v_start)
            self._config.nodes[-1].value_str = str(v_end)
            return self._config

        if not self._config.nodes:
            from engine.interpolation import make_default_nodes
            if self.tag_type == "color":
                self._config.nodes = make_default_nodes(start_color=str(v_start), end_color=str(v_end))
            else:
                self._config.nodes = make_default_nodes(start_y=float(v_start), end_y=float(v_end))
        else:
            if self.tag_type == "color":
                self._config.nodes[0].value_str = str(v_start)
                self._config.nodes[-1].value_str = str(v_end)
            else:
                self._config.nodes[0].y = float(v_start)
                self._config.nodes[-1].y = float(v_end)
        return self._config

    def set_config(self, cfg: TagGradientConfig):
        self._syncing = True
        self._config = cfg
        prev_check = self._check.blockSignals(True)
        try:
            self._check.setChecked(cfg.enabled)
            if cfg.nodes and len(cfg.nodes) >= 2:
                if self.tag_type == "color":
                    self._inputs[0].set_value(cfg.nodes[0].value_str)
                    self._inputs[1].set_value(cfg.nodes[-1].value_str)
                elif self.tag_type == "text":
                    self._inputs[0].set_value(cfg.nodes[0].value_str)
                    self._inputs[1].set_value(cfg.nodes[-1].value_str)
                elif self.tag_type == "coord":
                    y_nodes = cfg.coord_y_nodes if len(cfg.coord_y_nodes) >= 2 else None
                    start_y = y_nodes[0].y if y_nodes else 0.0
                    end_y = y_nodes[-1].y if y_nodes else start_y
                    self._inputs[0].set_value((cfg.nodes[0].y, start_y))
                    self._inputs[1].set_value((cfg.nodes[-1].y, end_y))
                else:
                    self._inputs[0].set_value(cfg.nodes[0].y)
                    self._inputs[1].set_value(cfg.nodes[-1].y)
        finally:
            self._check.blockSignals(prev_check)
            self._syncing = False

    def retranslate_ui(self):
        self._label.setText(tag_label(self.tag, self.info.get("label", f"\\{self.tag}")))
        if self._path_btn:
            set_button_text(
                self._path_btn,
                "路径✓" if self._path_active else "路径",
                minimum=58,
                padding=26,
            )
            self._path_btn.setToolTip(tr("使用视频帧上的贝塞尔路径采集颜色"))
        if self._path_smooth:
            self._path_smooth.setText(tr("平滑"))
            self._fit_path_smooth_width()
            self._path_smooth.setToolTip(tr("路径采色平滑过渡；关闭时使用原色彩"))
        if self._path_smooth_strength:
            self._path_smooth_strength.setToolTip(tr("路径采色平滑力度：0=原色阶梯，1=完全平滑"))
        for input_widget in self._inputs:
            input_widget.retranslate_ui()

    def _fit_path_smooth_width(self) -> None:
        if not self._path_smooth:
            return
        self._path_smooth.setFixedWidth(max(62, self._path_smooth.sizeHint().width() + 8))


class TagPanel(QWidget):
    tag_changed = Signal(str)
    tag_selected = Signal(str)
    path_sample_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._color_space_widget = QWidget()
        cs_layout = QHBoxLayout(self._color_space_widget)
        self._color_space_layout = cs_layout
        self._color_space_trailing_widget: Optional[QWidget] = None
        cs_layout.setContentsMargins(4, 0, 4, 2)
        cs_layout.setSpacing(4)
        self._color_space_label = QLabel(tr("颜色空间:"))
        cs_layout.addWidget(self._color_space_label)
        self._cs_combo = QComboBox()
        self._cs_combo.addItems(["RGB", "HSL", "OKLab"])
        self._cs_combo.setFixedWidth(80)
        self._cs_combo.currentTextChanged.connect(lambda: self.tag_changed.emit(""))
        cs_layout.addWidget(self._cs_combo)
        cs_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        self._tag_layout = QVBoxLayout(container)
        self._tag_layout.setContentsMargins(0, 0, 0, 0)
        self._tag_layout.setSpacing(2)

        self._rows: dict[str, TagRowWidget] = {}
        groups = {}
        for tag, info in TAG_INFO.items():
            g = info.get("group", "Other")
            groups.setdefault(g, []).append(tag)

        for group_name, tags in groups.items():
            grp_label = QLabel(f"── {group_label(group_name)} ──")
            grp_label.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
            self._tag_layout.addWidget(grp_label)
            if not hasattr(self, "_group_labels"):
                self._group_labels = []
            self._group_labels.append((grp_label, group_name))
            if group_name == "Color":
                self._tag_layout.addWidget(self._color_space_widget)
            for tag in tags:
                row = TagRowWidget(tag)
                row.changed.connect(self.tag_changed.emit)
                row.selected.connect(self._on_row_selected)
                row.path_requested.connect(self.path_sample_requested.emit)
                self._tag_layout.addWidget(row)
                self._rows[tag] = row

        self._tag_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def set_color_space_trailing_widget(self, widget: QWidget | None) -> None:
        if self._color_space_trailing_widget is widget:
            return
        if self._color_space_trailing_widget is not None:
            self._color_space_layout.removeWidget(self._color_space_trailing_widget)
            self._color_space_trailing_widget.setParent(None)
        self._color_space_trailing_widget = widget
        if widget is not None:
            self._color_space_layout.addWidget(widget)

    def retranslate_ui(self):
        self._color_space_label.setText(tr("颜色空间:"))
        for label, group_name in getattr(self, "_group_labels", []):
            label.setText(f"── {group_label(group_name)} ──")
        for row in self._rows.values():
            row.retranslate_ui()

    def _on_row_selected(self, tag: str):
        for t, row in self._rows.items():
            row.set_active(t == tag)
        self.tag_selected.emit(tag)

    def get_color_space(self) -> ColorSpace:
        cs_map = {"RGB": ColorSpace.RGB, "HSL": ColorSpace.HSL, "OKLab": ColorSpace.OKLAB}
        return cs_map.get(self._cs_combo.currentText(), ColorSpace.RGB)

    def get_row(self, tag: str) -> Optional[TagRowWidget]:
        return self._rows.get(tag)

    def get_all_configs(self) -> dict[str, TagGradientConfig]:
        configs = {}
        cs = self.get_color_space()
        for tag, row in self._rows.items():
            cfg = row.get_config()
            cfg.color_space = cs
            configs[tag] = cfg
        return configs

    def get_path_smooth_map(self) -> dict[str, bool]:
        return {
            tag: row.path_smooth_enabled()
            for tag, row in self._rows.items()
            if tag in {"1c", "2c", "3c", "4c"}
        }

    def get_path_smooth_strength_map(self) -> dict[str, float]:
        return {
            tag: row.path_smooth_strength()
            for tag, row in self._rows.items()
            if tag in {"1c", "2c", "3c", "4c"}
        }

    def set_path_smooth_map(self, smooth_map):
        if isinstance(smooth_map, bool):
            smooth_map = {tag: smooth_map for tag in ("1c", "2c", "3c", "4c")}
        elif not isinstance(smooth_map, dict):
            smooth_map = {}
        for tag in ("1c", "2c", "3c", "4c"):
            row = self._rows.get(tag)
            if row:
                row.set_path_smooth_enabled(bool(smooth_map.get(tag, False)))

    def set_path_smooth_strength_map(self, strength_map):
        if not isinstance(strength_map, dict):
            strength_map = {}
        for tag in ("1c", "2c", "3c", "4c"):
            row = self._rows.get(tag)
            if row:
                row.set_path_smooth_strength(strength_map.get(tag, 1.0))

    def get_enabled_tags(self) -> list[str]:
        return [tag for tag, row in self._rows.items() if row.get_config().enabled]

    def set_defaults_from_parsed(self, parsed_tags: dict, style=None):
        from engine.tag_parser import get_tag_value
        for tag, row in self._rows.items():
            val = get_tag_value(tag, parsed_tags, style)
            row.set_start_value(val)
            row.set_end_value(val)
