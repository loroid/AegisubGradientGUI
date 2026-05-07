"""Compact controls for time-sliced gradient animation."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from engine.models import AnimationSettings
from gui.i18n import set_button_text, tr


class AnimationPanel(QFrame):
    """UI for cyclic gradient animation."""

    settings_changed = Signal()
    preview_frame_changed = Signal(int)
    loop_play_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("animationPanel")
        self.setStyleSheet(
            """
            QFrame#animationPanel {
                background: #14142a;
                border: 1px solid #2b2b48;
                border-radius: 4px;
            }
            """
        )
        self._total_frames = 1
        self._active_tag_label: str | None = None
        self._loop_playing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._title_label = QLabel(tr("动画:"))
        self._title_label.setStyleSheet("font-weight: bold; color: #cfd4ff;")
        layout.addWidget(self._title_label)

        self._enabled = QCheckBox(tr("当前 tag 动画"))
        self._enabled.setToolTip(tr("仅让当前选中的 tag 参与渐变动画"))
        self._enabled.toggled.connect(self._on_settings_changed)
        layout.addWidget(self._enabled)

        self._use_transform = QCheckBox(tr(r"使用\t"))
        self._use_transform.setChecked(True)
        self._use_transform.setToolTip(tr(r"使用 \t 在同一套 clip strip 内变换颜色；关闭后按时间切成多套字幕行"))
        self._use_transform.toggled.connect(self._on_settings_changed)
        layout.addWidget(self._use_transform)

        self._every_label = QLabel(tr("每"))
        layout.addWidget(self._every_label)
        self._frame_step = QSpinBox()
        self._frame_step.setRange(1, 999)
        self._frame_step.setValue(1)
        self._frame_step.setSuffix(tr(" 帧"))
        self._frame_step.setFixedWidth(72)
        self._frame_step.valueChanged.connect(self._on_settings_changed)
        layout.addWidget(self._frame_step)

        self._seam_label = QLabel(tr("头尾渐变"))
        layout.addWidget(self._seam_label)
        self._seam_blend_length = QSpinBox()
        self._seam_blend_length.setRange(0, 999)
        self._seam_blend_length.setValue(0)
        self._seam_blend_length.setSuffix(tr(" 格"))
        self._seam_blend_length.setFixedWidth(76)
        self._seam_blend_length.setToolTip(tr("循环动画中，在尾部颜色回到头部颜色之间插入过渡渐变；0 表示直接首尾相接"))
        self._seam_blend_length.valueChanged.connect(self._on_settings_changed)
        layout.addWidget(self._seam_blend_length)

        self._start_frame = QSpinBox()
        self._start_frame.setRange(0, 999999)
        self._start_frame.setValue(0)
        self._start_frame.setToolTip(tr("动画开始帧，按当前字幕持续时间内的相对帧偏移计算"))
        self._start_frame.valueChanged.connect(self._on_frame_limit_changed)
        self._start_frame.hide()

        self._end_frame = QSpinBox()
        self._end_frame.setRange(-1, 999999)
        self._end_frame.setSpecialValueText(tr("末帧"))
        self._end_frame.setValue(-1)
        self._end_frame.setToolTip(tr("动画结束帧，包含该帧；末帧表示字幕可见的最后一帧"))
        self._end_frame.valueChanged.connect(self._on_frame_limit_changed)
        self._end_frame.hide()

        self._shift_start = QDoubleSpinBox(self)
        self._shift_start.setRange(-9999.0, 9999.0)
        self._shift_start.setDecimals(2)
        self._shift_start.setSingleStep(0.5)
        self._shift_start.setValue(1.0)
        self._shift_start.valueChanged.connect(self._on_settings_changed)
        self._shift_start.hide()

        self._shift_end = QDoubleSpinBox(self)
        self._shift_end.setRange(-9999.0, 9999.0)
        self._shift_end.setDecimals(2)
        self._shift_end.setSingleStep(0.5)
        self._shift_end.setValue(1.0)
        self._shift_end.valueChanged.connect(self._on_settings_changed)
        self._shift_end.hide()

        self._direction = QComboBox(self)
        self._direction.addItem(tr("正向(右/下)"), 1)
        self._direction.addItem(tr("反向(左/上)"), -1)
        self._direction.setFixedWidth(110)
        self._direction.currentIndexChanged.connect(self._on_settings_changed)
        self._direction.hide()

        layout.addStretch()

        self._preview_controls = QWidget(self)
        preview_layout = QHBoxLayout(self._preview_controls)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)

        self._to_start_btn = QPushButton(tr("起始帧"))
        set_button_text(self._to_start_btn, "起始帧", minimum=84, padding=26)
        self._to_start_btn.clicked.connect(lambda: self.set_frame_offset(0, emit=True))
        preview_layout.addWidget(self._to_start_btn)

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(34)
        self._prev_btn.setToolTip(tr("上一帧 (Alt+Left)"))
        self._prev_btn.clicked.connect(lambda: self.step_preview_frame(-1))
        preview_layout.addWidget(self._prev_btn)

        self._preview_frame_label = QLabel(tr("预览帧"))
        preview_layout.addWidget(self._preview_frame_label)
        self._frame_spin = QSpinBox()
        self._frame_spin.setRange(0, 0)
        self._frame_spin.setFixedWidth(70)
        self._frame_spin.valueChanged.connect(self.preview_frame_changed.emit)
        preview_layout.addWidget(self._frame_spin)

        self._frame_total_label = QLabel("/ 0")
        self._frame_total_label.setFixedWidth(48)
        preview_layout.addWidget(self._frame_total_label)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(34)
        self._next_btn.setToolTip(tr("下一帧 (Alt+Right)"))
        self._next_btn.clicked.connect(lambda: self.step_preview_frame(1))
        preview_layout.addWidget(self._next_btn)

        self._loop_btn = QPushButton(tr("循环播放"))
        self._loop_btn.setCheckable(True)
        set_button_text(self._loop_btn, "循环播放", minimum=82, padding=26)
        self._loop_btn.setToolTip(tr("循环播放当前字幕持续时间内的预览"))
        self._loop_btn.toggled.connect(self.loop_play_toggled.emit)
        preview_layout.addWidget(self._loop_btn)

        self._update_settings_enabled()

    def preview_controls_widget(self) -> QWidget:
        return self._preview_controls

    def to_settings(self, fps: float) -> AnimationSettings:
        return AnimationSettings(
            enabled=False,
            use_transform=self._use_transform.isChecked(),
            frame_step=self._frame_step.value(),
            frame_steps={},
            seam_blend_length=self._seam_blend_length.value(),
            start_frame=0,
            end_frame=-1,
            shift_start=self._shift_start.value(),
            shift_end=self._shift_end.value(),
            direction=1,
            fps=fps,
        )

    def state(self) -> dict[str, object]:
        return {
            "enabled": self._enabled.isChecked(),
            "use_transform": self._use_transform.isChecked(),
            "frame_step": self._frame_step.value(),
            "seam_blend_length": self._seam_blend_length.value(),
            "preview_frame": self.frame_offset(),
        }

    def restore_state(self, data: dict[str, object]) -> None:
        self._enabled.blockSignals(True)
        self._use_transform.blockSignals(True)
        self._frame_step.blockSignals(True)
        self._seam_blend_length.blockSignals(True)
        try:
            self._enabled.setChecked(False)
            self._use_transform.setChecked(bool(data.get("use_transform", True)))
            self._frame_step.setValue(max(1, int(data.get("frame_step", 1) or 1)))
            self._seam_blend_length.setValue(
                max(0, int(data.get("seam_blend_length", 0) or 0))
            )
        finally:
            self._enabled.blockSignals(False)
            self._use_transform.blockSignals(False)
            self._frame_step.blockSignals(False)
            self._seam_blend_length.blockSignals(False)
        self._start_frame.setValue(0)
        self._end_frame.setValue(-1)
        self._shift_start.setValue(1.0)
        self._shift_end.setValue(1.0)
        self._direction.setCurrentIndex(0)
        self.set_frame_offset(int(data.get("preview_frame", 0) or 0), emit=False)
        self._update_settings_enabled()

    def set_active_tag(
        self,
        label: str | None,
        checked: bool,
        seam_blend_length: int = 0,
        frame_step: int = 1,
    ) -> None:
        self._enabled.blockSignals(True)
        self._frame_step.blockSignals(True)
        self._seam_blend_length.blockSignals(True)
        try:
            self._active_tag_label = label
            self._enabled.setText(f"{label} {tr('动画')}" if label else tr("当前 tag 动画"))
            self._enabled.setChecked(bool(checked) if label else False)
            self._enabled.setEnabled(bool(label))
            self._frame_step.setValue(max(1, int(frame_step or 1)))
            self._seam_blend_length.setValue(max(0, int(seam_blend_length or 0)))
        finally:
            self._enabled.blockSignals(False)
            self._frame_step.blockSignals(False)
            self._seam_blend_length.blockSignals(False)
        self._update_settings_enabled()

    def active_tag_animation_enabled(self) -> bool:
        return self._enabled.isEnabled() and self._enabled.isChecked()

    def seam_blend_length(self) -> int:
        return int(self._seam_blend_length.value())

    def frame_step(self) -> int:
        return max(1, int(self._frame_step.value()))

    def frame_offset(self) -> int:
        return int(self._frame_spin.value())

    def set_frame_range(self, total_frames: int) -> None:
        self._total_frames = max(1, int(total_frames))
        current = min(self.frame_offset(), self._total_frames - 1)
        self._frame_spin.blockSignals(True)
        self._frame_spin.setRange(0, self._total_frames - 1)
        self._frame_spin.setValue(current)
        self._frame_spin.blockSignals(False)
        self._frame_total_label.setText(f"/ {self._total_frames - 1}")

    def set_frame_offset(self, frame: int, emit: bool = False) -> None:
        frame = max(0, min(int(frame), self._total_frames - 1))
        if emit:
            self._frame_spin.setValue(frame)
        else:
            self._frame_spin.blockSignals(True)
            self._frame_spin.setValue(frame)
            self._frame_spin.blockSignals(False)

    def step_preview_frame(self, delta: int) -> None:
        self.set_frame_offset(self.frame_offset() + int(delta), emit=True)

    def set_loop_playing(self, playing: bool) -> None:
        self._loop_playing = bool(playing)
        self._loop_btn.blockSignals(True)
        self._loop_btn.setChecked(bool(playing))
        set_button_text(
            self._loop_btn,
            "停止循环" if playing else "循环播放",
            minimum=82,
            padding=26,
        )
        self._loop_btn.blockSignals(False)

    def retranslate_ui(self) -> None:
        self._title_label.setText(tr("动画:"))
        self._enabled.setText(
            f"{self._active_tag_label} {tr('动画')}"
            if self._active_tag_label else tr("当前 tag 动画")
        )
        self._enabled.setToolTip(tr("仅让当前选中的 tag 参与渐变动画"))
        self._use_transform.setText(tr(r"使用\t"))
        self._use_transform.setToolTip(tr(r"使用 \t 在同一套 clip strip 内变换颜色；关闭后按时间切成多套字幕行"))
        self._every_label.setText(tr("每"))
        self._frame_step.setSuffix(tr(" 帧"))
        self._seam_label.setText(tr("头尾渐变"))
        self._seam_blend_length.setSuffix(tr(" 格"))
        self._seam_blend_length.setToolTip(tr("循环动画中，在尾部颜色回到头部颜色之间插入过渡渐变；0 表示直接首尾相接"))
        self._start_frame.setToolTip(tr("动画开始帧，按当前字幕持续时间内的相对帧偏移计算"))
        self._end_frame.setSpecialValueText(tr("末帧"))
        self._end_frame.setToolTip(tr("动画结束帧，包含该帧；末帧表示字幕可见的最后一帧"))
        self._direction.setItemText(0, tr("正向(右/下)"))
        self._direction.setItemText(1, tr("反向(左/上)"))
        set_button_text(self._to_start_btn, "起始帧", minimum=84, padding=26)
        self._prev_btn.setToolTip(tr("上一帧 (Alt+Left)"))
        self._preview_frame_label.setText(tr("预览帧"))
        self._next_btn.setToolTip(tr("下一帧 (Alt+Right)"))
        set_button_text(
            self._loop_btn,
            "停止循环" if self._loop_playing else "循环播放",
            minimum=82,
            padding=26,
        )
        self._loop_btn.setToolTip(tr("循环播放当前字幕持续时间内的预览"))

    def _on_settings_changed(self, *_args) -> None:
        self._update_settings_enabled()
        self.settings_changed.emit()

    def _on_frame_limit_changed(self, *_args) -> None:
        if self._end_frame.value() >= 0 and self._end_frame.value() < self._start_frame.value():
            self._end_frame.blockSignals(True)
            self._end_frame.setValue(self._start_frame.value())
            self._end_frame.blockSignals(False)
        self._on_settings_changed()

    def _update_settings_enabled(self) -> None:
        for widget in (
            self._use_transform,
            self._frame_step,
            self._seam_blend_length,
        ):
            widget.setEnabled(True)
