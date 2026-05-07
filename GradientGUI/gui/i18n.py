"""Small runtime language helper for GradientGUI."""

from __future__ import annotations

import locale
from typing import Any

try:
    from PySide6.QtCore import QLocale
except Exception:  # pragma: no cover - PySide is always present in the GUI.
    QLocale = None


LANG_ZH = "zh"
LANG_EN = "en"


def default_language() -> str:
    name = ""
    try:
        if QLocale is not None:
            name = QLocale.system().name()
    except Exception:
        name = ""
    if not name:
        try:
            name = locale.getlocale()[0] or ""
        except Exception:
            name = ""
    return LANG_ZH if name.lower().startswith("zh") else LANG_EN


_language = default_language()


def language() -> str:
    return _language


def set_language(lang: str) -> str:
    global _language
    _language = LANG_ZH if str(lang).lower().startswith("zh") else LANG_EN
    return _language


def toggle_language() -> str:
    return set_language(LANG_EN if _language == LANG_ZH else LANG_ZH)


def is_english() -> bool:
    return _language == LANG_EN


def tr(text: Any) -> str:
    value = str(text)
    if _language == LANG_ZH:
        return value
    return EN.get(value, value)


def tag_label(tag: str, fallback: str | None = None) -> str:
    if _language == LANG_ZH:
        return fallback or f"\\{tag}"
    return TAG_LABELS_EN.get(tag, fallback or f"\\{tag}")


def group_label(name: str) -> str:
    if _language == LANG_ZH:
        return name
    return GROUP_LABELS_EN.get(name, name)


def button_text_width(
    button: Any,
    *,
    text: str | None = None,
    padding: int = 28,
    minimum: int = 0,
) -> int:
    """Return a compact button width that still fits the current translated text."""

    value = button.text() if text is None else str(text)
    try:
        metrics = button.fontMetrics()
        width = metrics.horizontalAdvance(value)
    except Exception:
        width = max(0, len(value)) * 8
    try:
        icon = button.icon()
        if icon and not icon.isNull():
            width += int(button.iconSize().width()) + 6
    except Exception:
        pass
    return max(int(minimum), int(width) + int(padding))


def fit_button_width(
    button: Any,
    *,
    padding: int = 28,
    minimum: int = 0,
    fixed: bool = True,
) -> None:
    width = button_text_width(button, padding=padding, minimum=minimum)
    if fixed:
        button.setFixedWidth(width)
    else:
        button.setMinimumWidth(width)


def set_button_text(
    button: Any,
    text: Any,
    *,
    padding: int = 28,
    minimum: int = 0,
    fixed: bool = True,
) -> None:
    button.setText(tr(text))
    fit_button_width(button, padding=padding, minimum=minimum, fixed=fixed)


GROUP_LABELS_EN = {
    "Color": "Color",
    "Alpha": "Alpha",
    "Transform": "Transform",
    "Border/Shadow": "Border / Shadow",
    "Size": "Size",
    "Effect": "Effect",
    "Position": "Position",
    "Style": "Style",
    "Other": "Other",
}


TAG_LABELS_EN = {
    "1c": r"\1c  Fill",
    "2c": r"\2c  Secondary",
    "3c": r"\3c  Border",
    "4c": r"\4c  Shadow",
    "alpha": r"\alpha  All Alpha",
    "1a": r"\1a  Fill Alpha",
    "2a": r"\2a  Secondary Alpha",
    "3a": r"\3a  Border Alpha",
    "4a": r"\4a  Shadow Alpha",
    "fscx": r"\fscx  X Scale",
    "fscy": r"\fscy  Y Scale",
    "fax": r"\fax  X Shear",
    "fay": r"\fay  Y Shear",
    "frx": r"\frx  X Rotation",
    "fry": r"\fry  Y Rotation",
    "frz": r"\frz  Z Rotation",
    "bord": r"\bord  Border",
    "xbord": r"\xbord X Border",
    "ybord": r"\ybord Y Border",
    "shad": r"\shad  Shadow",
    "xshad": r"\xshad X Shadow",
    "yshad": r"\yshad Y Shadow",
    "fs": r"\fs   Font Size",
    "fsp": r"\fsp  Spacing",
    "blur": r"\blur Gaussian Blur",
    "be": r"\be   Edge Blur",
    "fad": r"\fad  Fade In/Out",
    "fn": r"\fn   Font",
    "pos": r"\pos  Position",
    "org": r"\org  Origin",
}


EN = {
    # Common
    "确定": "OK",
    "取消": "Cancel",
    "应用": "Apply",
    "关闭": "Off",
    "开启": "On",
    "开": "On",
    "关": "Off",
    "未知": "Unknown",
    "编辑": "Edit",
    "动画": "Animation",
    "手动": "Manual",
    "路径": "Path",
    "路径✓": "Path✓",
    "平滑": "Smooth",
    "取": "Pick",
    "(空)": "(empty)",
    "末帧": "Last",
    "全选": "All",
    "反选": "Invert",
    "设置": "Settings",
    "就绪": "Ready",
    "中文 / English": "中文 / English",
    "EN / 中": "EN / 中",
    "切换界面语言": "Switch UI language",
    # Main window
    "GradientGUI — 实时渐变编辑器": "GradientGUI — Real-time Gradient Editor",
    "曲线编辑器": "Curve Editor",
    "动画移动曲线": "Animation Motion Curve",
    "动画编辑器": "Animation Editor",
    "标签总览": "Tag Overview",
    "动画总览": "Animation Overview",
    "模式:": "Mode:",
    "角度 (°):": "Angle (°):",
    "步长 (px):": "Step (px):",
    "保存预设": "Save Preset",
    "加载预设": "Load Preset",
    "最近预设": "Recent",
    "没有最近预设": "No recent presets",
    "↩ 撤销": "↩ Undo",
    "↪ 重做": "↪ Redo",
    "撤销": "Undo",
    "重做": "Redo",
    "调试覆盖": "Debug Overlay",
    "范围调试": "Range Debug",
    "预览刷新": "Refresh Preview",
    "导出调试包": "Export Debug",
    "应用并关闭": "Apply && Close",
    "未找到输入文件": "Input file not found",
    "未找到 Dialogue 行": "No Dialogue lines found",
    "依赖健康检查": "Dependency Health Check",
    "依赖检查通过": "Dependency check passed",
    "依赖检查发现问题": "Dependency check found issues",
    "启动依赖健康检查发现问题：": "Startup dependency health check found issues:",
    "字体不会在此检查中验证。修复失败项后重新打开 GUI 即可重新检查。": "Fonts are not checked here. Fix failed items and reopen the GUI to recheck.",
    "可用": "Available",
    "不可用": "Unavailable",
    "检查失败": "Check failed",
    "python-mpv 模块不可用": "python-mpv module unavailable",
    "libmpv 不可用": "libmpv unavailable",
    "未找到 ffmpeg.exe": "ffmpeg.exe not found",
    "请将 ffmpeg.exe 放入程序目录或加入 PATH。": "Put ffmpeg.exe in the program directory or add it to PATH.",
    "无法运行": "Could not run",
    "视频帧读取": "Video Frame Read",
    "ASS 未关联视频文件": "ASS has no linked video file",
    "需要视频预览或路径采色时请加载带视频路径的字幕。": "Load subtitles with a video path when preview or path sampling is needed.",
    "视频文件不存在": "Video file does not exist",
    "无法读取当前帧": "Could not read current frame",
    "视频文件未找到": "Video file not found",
    "已加载行": "Loaded line",
    "初始状态": "Initial State",
    "整体范围设置": "Merged Range Settings",
    "当前没有启用的 tag。": "No enabled tags.",
    "路径采色": "Path Sampling",
    "已移除": "Removed",
    "已清空": "Cleared",
    "已设置": "Set",
    "视频帧": "Video Frame",
    "\\fad 淡入": "\\fad Fade In",
    "\\fad 淡出": "\\fad Fade Out",
    "未加载 ASS 文件。": "No ASS file loaded.",
    "采色结果": "Sampled Colors",
    "当前颜色 tag 没有可编辑的路径采色结果。": "The current color tag has no editable sampled colors.",
    "渐变生成失败": "Gradient Generation Failed",
    "范围调试": "Range Debug",
    "当前没有可调试的选中字幕行。": "There are no selected subtitle lines to debug.",
    "保存预设失败": "Failed to Save Preset",
    "加载预设失败": "Failed to Load Preset",
    "加载预设": "Load Preset",
    "导出调试包": "Export Debug Package",
    "导出调试包失败": "Failed to Export Debug Package",
    "错误": "Error",
    "解析 ASS 失败": "Failed to parse ASS",
    "已保存输出": "Output saved",
    "已导出调试包": "Debug package exported",
    "保存失败": "Save failed",
    "已保存预设": "Preset saved",
    "已加载预设": "Preset loaded",
    "已加载最近预设": "Recent preset loaded",
    "预览缓存": "Preview Cache",
    "保存预设": "Save Preset",
    "加载预设前": "Before Loading Preset",
    "调试报告": "Debug Report",
    "逐行范围": "Per-line Range",
    "整体范围": "Merged Range",
    "等待视频加载...": "Waiting for video...",
    "mpv 初始化失败": "mpv initialization failed",
    "发现新版本": "Update Available",
    "发现 GradientGUI 新版本。": "A new GradientGUI version is available.",
    "最新版本": "Latest version",
    "发布页面": "Release page",
    "打开发布页面": "Open Releases",
    "稍后": "Later",
    "是否加载这个预设？": "Load this preset?",
    "预设格式不正确。": "Invalid preset format.",
    "版本": "Version",
    "模式": "Mode",
    "角度": "Angle",
    "步长": "Step",
    "启用 tag": "Enabled tags",
    "项": "items",
    "色带动画": "Color-strip animation",
    "预览已更新": "Preview updated",
    "行源字幕": "source lines",
    "行输出": "output lines",
    "当前没有启用的 tag": "No enabled tags",
    "个启用 tag": "enabled tags",
    "条曲线": "curves",
    "颜色空间": "Color space",
    "未输出动画": "No animation output",
    r"\t 优先": r"\t first",
    "路径已移除": "Path Removed",
    "原路径": "Source Clip",
    # Line selection / group range
    "行:": "Lines:",
    "仅当前": "Current",
    "将多选行视为一个整体，共用合并后的渐变范围": "Treat selected lines as one group and use a merged gradient range",
    "设置哪些 tag 使用整体范围": "Choose which tags use merged range",
    # Tag panel
    "颜色空间:": "Color Space:",
    "打开颜色选择器": "Open color picker",
    "从屏幕拾取颜色，左键确认，右键或 Esc 取消": "Pick a screen color; left click confirms, right click or Esc cancels",
    "选择颜色": "Choose Color",
    "使用视频帧上的贝塞尔路径采集颜色": "Sample colors along a Bezier path on the video frame",
    "路径采色平滑过渡；关闭时使用原色彩": "Smooth path-sampled colors; off keeps original colors",
    "路径采色平滑力度：0=原色阶梯，1=完全平滑": "Path sampling smooth strength: 0=original steps, 1=fully smoothed",
    # Path sampler
    "路径列表:": "Path List:",
    "当前路径应用到:": "Apply Current Path To:",
    "全部路径应用到:": "Apply All Paths To:",
    "撤销点": "Undo Point",
    "新增路径": "Add Path",
    "移除当前路径": "Remove Current",
    "清空全部": "Clear All",
    "移除全部路径": "Remove All Paths",
    "路径不足": "Not Enough Path Points",
    "当前路径至少需要两个点。": "The current path needs at least two points.",
    "至少需要一条包含两个点的路径。": "At least one path with two points is required.",
    "(空路径，点击画面添加点)": "(empty path, click the image to add points)",
    # Sample editor
    "编辑路径采样得到的颜色点；确定会保存采样结果，应用为曲线会转换为手动曲线。": "Edit sampled path colors. OK saves the samples; Apply as Curve converts them to a manual curve.",
    "位置": "Position",
    "颜色": "Color",
    "删除选中": "Delete Selected",
    "插入": "Insert",
    "合并阈值:": "Merge Threshold:",
    "合并相近": "Merge Similar",
    "反转": "Reverse",
    "应用为曲线": "Apply as Curve",
    "采色结果不足": "Not Enough Sampled Colors",
    "至少需要两个颜色点。": "At least two color points are required.",
    "至少保留两个颜色点。": "Keep at least two color points.",
    "选择采样颜色": "Choose Sample Color",
    # Curve editor
    "设置颜色...": "Set Color...",
    "拾取屏幕颜色...": "Pick Screen Color...",
    "设置文本...": "Set Text...",
    "设置数值...": "Set Value...",
    "此段插值类型": "Segment Interpolation",
    "删除节点": "Delete Node",
    "设置文本": "Set Text",
    "输入此节点文本:": "Text for this node:",
    "设置数值": "Set Value",
    "输入此节点的整数移动格数:": "Integer strip movement for this node:",
    "输入此节点的数值:": "Value for this node:",
    "批量增加控制点": "Batch Add Nodes",
    "控制点数量:": "Node Count:",
    "起始位置:": "Start Position:",
    "间隔:": "Interval:",
    "固定": "Fixed",
    "递增": "Increase",
    "递减": "Decrease",
    "间隔规则:": "Interval Rule:",
    "数值:": "Value:",
    "数值变化量:": "Value Delta:",
    "数值规则:": "Value Rule:",
    "颜色曲线会在新增位置按当前曲线采样颜色。": "Color curves sample the current curve at the inserted positions.",
    "文本曲线会在新增位置继承前一个文本值。": "Text curves inherit the previous text value at inserted positions.",
    "横向镜像": "Mirror X",
    "镜像当前曲线的渐变方向": "Mirror the current curve direction",
    "纵向镜像": "Mirror Y",
    "镜像当前曲线的数值方向": "Mirror the current curve value axis",
    "采色结果": "Samples",
    "编辑路径采样颜色，并应用为当前颜色曲线": "Edit path-sampled colors and apply them as the current color curve",
    "批量加点": "Batch Add",
    "按数量、间隔和数值规则批量增加控制点": "Add control points by count, interval, and value rules",
    "节点表格": "Node Table",
    "打开表格编辑控制点；支持粘贴 Excel 单元格": "Edit control points in a table; supports Excel paste",
    "默认插值:": "Default Interp:",
    # Node table
    "曲线节点表格": "Curve Node Table",
    "整数帧": "Integer Frame",
    "位置": "Position",
    "添加行": "Add Row",
    "粘贴": "Paste",
    "排序": "Sort",
    "节点数据错误": "Node Data Error",
    "节点不足": "Not Enough Nodes",
    "至少需要两个控制点。": "At least two control points are required.",
    "至少保留两个控制点。": "Keep at least two control points.",
    "帧": "Frame",
    "段插值": "Segment Interp",
    "文本": "Text",
    "整数移动": "Integer Move",
    "入柄X": "In Handle X",
    "入柄Y": "In Handle Y",
    "出柄X": "Out Handle X",
    "出柄Y": "Out Handle Y",
    "数值": "Value",
    # Animation
    "动画:": "Animation:",
    "当前 tag 动画": "Current Tag Animation",
    "仅让当前选中的 tag 参与渐变动画": "Animate only the currently selected tag",
    r"使用\t": r"Use \t",
    r"使用 \t 在同一套 clip strip 内变换颜色；关闭后按时间切成多套字幕行": r"Use \t to transform colors within one strip set; off splits subtitle lines by time",
    "每": "Every",
    " 帧": " frames",
    "头尾渐变": "Seam Blend",
    " 格": " cells",
    "循环动画中，在尾部颜色回到头部颜色之间插入过渡渐变；0 表示直接首尾相接": "Insert transition colors when the tail wraps to the head; 0 means direct wrap",
    "动画开始帧，按当前字幕持续时间内的相对帧偏移计算": "Animation start frame, relative to the current subtitle duration",
    "动画结束帧，包含该帧；末帧表示字幕可见的最后一帧": "Animation end frame, inclusive; Last means the final visible subtitle frame",
    "正向(右/下)": "Forward (Right/Down)",
    "反向(左/上)": "Reverse (Left/Up)",
    "起始帧": "Start Frame",
    "上一帧 (Alt+Left)": "Previous Frame (Alt+Left)",
    "预览帧": "Preview Frame",
    "下一帧 (Alt+Right)": "Next Frame (Alt+Right)",
    "循环播放": "Loop",
    "循环播放当前字幕持续时间内的预览": "Loop preview over the current subtitle duration",
    "停止循环": "Stop Loop",
    # Overviews
    "启用 tag 后，这里会显示每个 tag 的动画曲线、帧范围和输出方式。": "Enable tags to show animation curves, frame ranges, and output mode here.",
    "输出": "Output",
    "帧范围": "Frame Range",
    "移动曲线": "Movement Curve",
    "启用 tag 后，这里会显示每个 tag 的取值、路径状态和曲线概览。": "Enable tags to show values, path state, and curve previews here.",
    "来源": "Source",
    "取值": "Value",
    "曲线 / 色带": "Curve / Color Strip",
    # Range debug
    "行": "Line",
    "范围来源": "Range Source",
    "原 clip": "Source Clip",
    "生成范围": "Generated Range",
    "显示": "shown",
    "投影范围": "Projected Range",
    "clip 矩形": "Clip Rect",
    "输出行": "Output Lines",
    "当前选中字幕行的范围、投影、clip 和输出行数。选中一行可查看完整细节。": "Range, projection, clip, and output line count for selected lines. Select a row for details.",
}
