# GradientGUI

GradientGUI is an external visual gradient editor for Aegisub ASS subtitles. It is built for complex typesetting effects where plain Automation scripts become difficult to tune by hand.

The editor provides a real-time video preview, per-tag curve editing, libass-based rendered bounds, path-based color sampling from video frames, multi-line application, undo/redo, and preset saving/loading.

Supported platforms:

- Windows 64-bit
- Linux x64 (tested only in WSL)

## Quick Start

### Windows

The Windows portable package is the recommended way to use GradientGUI on Windows. It does not
require installing Python, PySide6, vcpkg, mpv, libass, or FFmpeg globally.

1. Download the latest portable zip from the GitHub Releases page.
2. Extract the zip.
3. Copy the launcher script and the `GradientGUI/` folder into your Aegisub
   Automation autoload folder.

The final layout should look like this:

```text
autoload/
├── Ioroid.GradientGUI.lua
└── GradientGUI/
    ├── GradientGUI.exe
    ├── ffmpeg.exe
    ├── libmpv-2.dll
    ├── libass/
    └── ...other bundled files...
```

4. Restart Aegisub, or rescan Automation scripts.
5. Open a video and ASS subtitle file.
6. Select one or more subtitle lines.
7. Run `Automation > Gradient GUI`.
8. Enable the tags you want to edit, preview the result, then use `Apply and Close`.

If the launcher says `GradientGUI was not found`, make sure the `GradientGUI/`
folder is beside the Lua launcher script.

### Linux x64

Linux x64 support has been tested only in WSL.

1. Install runtime packages:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg mpv libmpv-dev libass-dev
```

2. Create a virtual environment and install Python packages:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r GradientGUI/requirements.txt
```

3. Build the app:

```bash
cd GradientGUI
pyinstaller --clean GradientGUI.spec
```

4. Copy the launcher script and the generated `dist/GradientGUI/` folder into
   your Aegisub Automation autoload folder. Rename `dist/GradientGUI/` to
   `GradientGUI/` if needed.

The final layout should look like this:

```text
autoload/
├── Ioroid.GradientGUI.lua
└── GradientGUI/
    ├── GradientGUI
    └── ...other bundled files...
```

## Highlights

- Visual curve editor for ASS override tags.
- Bilingual Chinese/English UI. Chinese system locales open in Chinese by default; other locales open in English, and the compact top-right language button can switch at runtime. Translated buttons auto-fit their current label so English text is not clipped.
- Real-time preview with embedded mpv video playback.
- Throttled live preview while choosing manual colors; canceling a color dialog restores the previous committed value.
- libass pixel bounds for rendered subtitle visibility, instead of font metrics boxes.
- Strip gradients: Horizontal, Vertical, and arbitrary Angled modes.
- Layout-positioned subtitle lines without `\pos` are converted to an equivalent static `\pos` during strip generation, so generated overlay strips stay stacked at the original on-screen position.
- Existing vector `\clip` and `\iclip` masks are split per generated strip instead of being reduced to rectangle clips.
- GBC mode: gradient by character.
- Multi-line editing and optional merged range calculation. Any line passed into GradientGUI can be marked as a per-line visual reference: it remains visible unchanged in the preview but is excluded from gradient generation, merged ranges, and processing.
- Path color sampling from the current video frame using ASS drawing paths.
- Per-color-tag path sampling controls for `\1c`, `\2c`, `\3c`, and `\4c`, including mirror and adjustable smooth strength.
- Cyclic gradient animation for selected tags, with `\t` transform output by default where ASS supports it, per-tag frame-step timing, per-tag movement curves, per-tag optional head/tail seam blending, a tag overview, and a timeline overview.
- Editable sampled-color results that can be converted into manual color curves.
- Independent `\pos X`, `\pos Y`, `\org X`, and `\org Y` tag rows, each with its own enable switch, curve, animation, and merged-range setting. Only enabled axes appear as curve tabs. `\fad` keeps its paired fade-in/fade-out curves.
- Curve node box selection, multi-node dragging, batch node insertion, and spreadsheet-style node editing with Excel paste support.
- Per-curve horizontal and vertical mirror controls.
- ASS-rendered preview debug overlay for rendered bounds, source clips, merged ranges, and generated clip strips.
- Range debug table for inspecting libass bounds, source clips, per-tag projected ranges, final clip rectangles, and generated strip counts.
- Preview generation cache for repeated same-frame/same-settings preview refreshes.
- Generation error recovery with line/tag/value/range context and copyable debug JSON.
- Silent background update check against the latest GitHub Release when the GUI opens.
- Undo/redo history, recent presets, and JSON-based `.ggpreset` presets.
- Local JSON debug report export for troubleshooting dependency, range, and preview state.
- Render regression tests for clip/range behavior.

## Supported Tags

| Group | Tags |
| --- | --- |
| Color | `\1c`, `\2c`, `\3c`, `\4c` |
| Alpha | `\alpha`, `\1a`, `\2a`, `\3a`, `\4a` |
| Transform | `\fscx`, `\fscy`, `\fax`, `\fay`, `\frx`, `\fry`, `\frz` |
| Border / shadow | `\bord`, `\xbord`, `\ybord`, `\shad`, `\xshad`, `\yshad` |
| Size / spacing | `\fs`, `\fsp` |
| Effects | `\blur`, `\be`, `\fad` |
| Position | `\pos X`, `\pos Y`, `\org X`, `\org Y` |
| Style | `\fn` |

Dimensionless shear tags `\fax` and `\fay` retain six decimal places in the editor and generated ASS, including animation seam blending, avoiding visible 0.01-value stepping across large subtitle geometry. Other numeric tags keep their existing two-decimal output precision.

## Gradient Modes

| Mode | Description |
| --- | --- |
| Horizontal | Generates vertical clip strips across the rendered subtitle range. |
| Vertical | Generates horizontal clip strips across the rendered subtitle range. |
| Angled | Generates vector clip strips for arbitrary gradient angles. |
| GBC | Applies tags character by character instead of generating clip strips, while preserving inline override blocks and ASS `\N`, `\n`, and `\h` text control sequences. |

Slanted strip clips retain 1/64-pixel coordinate precision, including intersections with source vector clips and inverse masks. This prevents whole-pixel vertex rounding from introducing dark seams at non-unit steps, such as 45 degrees with a 2 px step. Strip spacing and the existing edge overlap are unchanged; animation positioning and debug overlays interpret the scaled coordinates in script space.

GBC cyclic animation wraps across the complete character sequence, including the final color; a single-character line keeps its original gradient value. Drawing commands inside `\p` spans remain intact and do not count as characters. Generated GBC text removes custom `\1xlip` through `\4xlip` sampling metadata, just like strip output.

By default, Stepped interpolation holds the preceding value up to, but not including, the next node. Horizontal mirroring reverses these held intervals as well as the nodes, including numeric, color, font, and animation curves. At a node's exact position, its own value takes effect. The mirrored interval direction is preserved by node-table editing, presets, and undo/redo; canvas and batch insertions inherit their segment's mode and direction.

ASS input/output follows the declared style and event `Format:` columns and preserves subtitle text whitespace and extension fields. Preview and libass bounds measurement share the same serialized styles and embedded fonts, including styles created or modified in memory.

## Path Color Sampling

GradientGUI can sample colors from the video frame along user-drawn ASS paths. The sampled color map can then drive color tags such as `\1c` or `\3c`.

Important details:

- Sampling uses integer video pixel coordinates.
- Reopening, confirming, or copying an existing path preserves its straight segments, Bezier control handles, and coordinate precision. The canvas and saved sampling path use the same geometry. Moving an existing anchor translates only its adjacent handles; unedited paths are not rebuilt.
- Opening the path editor uses the current main preview frame. After the path is confirmed, GradientGUI stores that frame's integer pixel samples with their coordinates; later preview-frame changes do not resample the path unless the path editor is opened and confirmed again.
- Path saves sample the captured editor frame, without loading the video again. If capture fails, existing paths, sampled colors, and undo history remain unchanged. A path and its copies to other color tags are committed together, including when the source path is cleared after copying.
- Saved sampling snapshots remain usable for generation without an attached video. A video is needed to capture or update samples, not to reuse saved colors.
- Each input line's original paths are loaded when the ASS file opens, so visiting rows in a different order does not change their path assignments. Lines without a per-tag path inherit the active line's complete path state; local paths never inherit an unrelated path's sampling frame.
- The curve editor's lower color strip is rebuilt from that saved raw snapshot, so Horizontal, Vertical, and Angled switches update immediately without reopening the path dialog.
- Repeated coordinates along the active gradient axis are deduplicated.
- Saved path samples are direction-independent. Horizontal, Vertical, and Angled modes reproject the stored coordinates so mode changes update immediately without reading the video frame again.
- When Aegisub frame metadata is available, interactive path sampling loads the current frame by its exported frame time for responsiveness while keeping cache identity tied to the whole-video frame number.
- With smooth sampling disabled, generated clip strips are divided by the number of sampled integer pixels, so each sampled color can be represented directly; the global Step value does not apply in this mode.
- When merged range is enabled for a color tag, path-sampled colors are mapped against the merged projected range instead of each line's local range.
- Smooth sampling blends each generated strip color toward the interpolation between adjacent sampled colors. This can create intermediate colors that were not exact sampled source pixels; disable smooth sampling when the output must use only captured pixel colors.
- The curve editor previews the same sampled, mirrored, and smooth-strength-adjusted colors in its lower color strip.
- Screen picking verifies the loaded frame as well as the preview frame number, so opening a path editor on another frame cannot make it reuse that frame's colors by mistake.
- The sampled-color result editor works on the real saved sample sequence. Colors can be inserted, deleted, simplified, reversed, confirmed back into the path-sampling data, or applied as a manual color curve for the current color tag.
- Sample-result edits validate positions as finite numbers from 0 to 1 and colors as six-digit BGR hex values before applying changes. Invalid drafts remain editable without partially modifying saved samples. Valid BGR text edits update their color swatches immediately, and unchanged positions retain full precision.
- Insertion follows the selected color even after positions are edited. Appending at the end makes room for a distinct final position; curve conversion rejects duplicate positions, preserves nearby distinct color points, and retains the current horizontal mirror direction.
- Confirming edited sampled colors rewrites the stored sample count, so the sampled-color result button and unsmoothed path-color strip output both reflect inserted or deleted colors.
- Animation targets are refreshed when saved samples are edited, inserted, deleted, or reordered, including merged-range transform and split-line output.

## Gradient Animation

The animation editor can split each selected subtitle line across its duration and apply a cyclic phase shift to chosen gradient tags. For example, `\1c` can move one generated strip to the right every frame, wrapping the last strip color back to the first strip. Numeric, alpha, text, and coordinate-style tags use the same strip-shift model.

Controls include:

- The tag editor area has two second-level tabs for the selected tag: the normal curve editor and the animation editor.
- The tag overview and animation overview are top-level tabs at the right side of the tag tab row, so global inspection stays separate from per-tag editing.
- The tag overview lists every enabled tag curve, its group, value range, manual/path source, and a mini curve or color-strip preview.
- Each tag has its own animation switch in the animation editor. A tag can keep its static gradient while another tag animates.
- A per-tag movement curve. X uses integer frame offsets from each line's start, beginning at 0; Y is the number of generated strips to move at each animation step. Nodes keep their relative frame positions across lines of different durations: frame 10 stays frame 10, without proportional rescaling.
- Switching lines only changes the displayed timeline range. The editor keeps all authored nodes visible and shades the region after the current line's last frame; longer lines continue using the last node's movement value beyond the curve end. Switching tags, inspecting overviews, or temporarily disabling a tag does not change its saved animation settings.
- The animation curve Y axis defaults to `-10` through `+10` strip cells. Existing nodes outside that range still expand the visible curve range.
- The animation curve editor reuses curve mirroring, batch node insertion, and spreadsheet-style node table editing. Horizontal mirroring uses the authored first and last node frames, so toggling it while viewing different-duration lines does not shift the curve in time.
- The animation overview lists every enabled tag, its relative frame range, whether that tag is animated through `\t`, split-line output, or disabled animation, and a mini movement-curve preview with the current preview frame marked.
- Enable/disable gradient animation per tag.
- Output mode: `\t` transform output is the default and keeps one generated strip set; legacy time-sliced subtitle output remains available for comparison.
- Time-sliced output rebases existing `\t`, `\move`, `\fad`, `\fade`, and karaoke tag timing so these effects do not restart in each segment. Generated `\fad` gradients also retain the original subtitle duration, and timing offsets follow the centisecond boundaries actually written to ASS.
- In simple `\t` mode, target values are derived by rotating the generated strip sequence. When merged ranges are active, transform targets use a cached global strip sequence so multi-line/path-sampled animations keep the same phase as split-line output without regenerating every frame. Color, alpha, and numeric tags use `\t`; non-transformable tags such as `\pos`, `\org`, `\fad`, and `\fn` automatically fall back to split-line animation.
- Frame step is stored per tag: each animated tag can use its own "move every N frames" timing while sharing the same subtitle duration timeline.
- Head/tail seam blending is stored per tag and can insert a configurable number of transition strip values between the last generated strip and the first strip during cyclic wraparound.
- Shadow seam blending also handles negative `\shad` values serialized as paired `\xshad` / `\yshad` offsets.
- When merged range is enabled for a tag, animation phase shifts use the merged projected range, so multi-line and path-sampled animations keep one shared phase instead of restarting on each line.
- Reverse movement can be expressed with negative movement values or curve mirroring.
- Preview frame controls with `Alt+Left` and `Alt+Right` shortcuts for stepping through the subtitle duration.
- When launched from Aegisub, the Lua launcher exports whole-video frame numbers aligned to Aegisub's displayed line frame range. Animation curves, `\t` timing, split-line timing, preview stepping, and path-sampling preview all use those video frame numbers instead of estimating from floating-point fps. The relative end frame follows Aegisub/FBF-style inclusive frame counts.
- Preview stepping uses integer video frame numbers; the last preview frame is the final frame before the ASS event end time. Development-mode files without Aegisub frame metadata fall back to fps-based timing.
- Loop playback for repeatedly previewing the current subtitle line duration.

## Render Bounds

GradientGUI uses libass to render the source line and scan the actual visible alpha pixels. This avoids the common problem where font metrics boxes are larger than the visible glyph area.

The range system also accounts for dynamic tags such as border, shadow, blur, scale, rotation, and position. Existing source `\clip` tags are used as the generation envelope automatically.

Numeric range measurement includes interior Bezier and smooth-curve peaks and valleys, even between closely spaced nodes. These positions are evaluated with the same interpolation as the generated tags and included in libass sampling, effect expansion, and merged-range calculation. Invisible endpoints do not skip visible interior samples. Color fill ranges remain independent of border and shadow extents.

When the source line contains a vector `\clip`, strip generation intersects that vector shape with each generated strip and outputs per-strip vector clips. Vector and rectangular `\iclip` are also preserved by subtracting the inverse mask from each generated strip and writing the remaining strip area as a vector `\clip`.

## Preview Debug Overlay

The debug overlay button appends translucent diagnostic ASS drawing events to the preview subtitle:

- libass-measured visible bounds.
- Existing source `\clip` bounds.
- Per-line generation ranges.
- Merged multi-line range, when enabled.
- A sampled subset of generated `\clip` strips, so very large outputs stay responsive.

The same debug data is included in exported debug reports.

The range debug window shows the same calculation as structured data: measured bounds, source `\clip`, base and merged ranges, projected per-tag ranges, final clip rectangles, range source, and output line count.

Preview generation keeps a small LRU cache of generated ASS events keyed by the selected source lines, current frame, bounds metadata, and effect settings. Repeated refreshes of the same state reuse the cached result while still rebuilding the optional debug overlay on demand.

If generation fails, the previous successful preview remains visible. The error dialog shows the source line, likely failing tag, current tag value, generated/source clip range, and a detailed JSON report that can be copied for debugging.

## Curve Node Editing

The curve editor supports direct node dragging, box-select multi-node dragging, batch node insertion, and a separate node table. The node table accepts tab-separated data copied from Excel, so positions, numeric values, colors, text values, handles, and segment interpolation modes can be edited precisely.

Group dragging preserves node spacing and moves Bezier handles with their anchors. Ctrl locks movement horizontally; Shift locks it vertically, for the entire selection or an individual handle. Animation node movement snaps to integer frames throughout the drag. Preview and undo receive complete group changes, never partially moved selections. Range refreshes are deferred until dragging ends and retain the selection.

Opening, sorting, or applying the node table without edits preserves numeric precision and hidden node fields. Invalid hex colors and non-finite numbers are rejected before applying changes. Bezier handles at `(0, 0)` remain valid through copying and mirroring. Redrawing or resizing either curve editor only updates graphics, without rounding node values or clipping handle coordinates; automatic Y-axis bounds include the handles. Pure visual refreshes and unchanged node-table confirmations do not enqueue subtitle generation or new undo states. Node insertion, deletion, batch insertion, and interpolation-mode changes each publish a single complete edit.

Both curve editors keep their canvas aligned to the viewport when switching tabs or resizing the window. Hidden or out-of-range Bezier handles retain their original values without expanding the scrollable canvas, shifting the grid, or pushing endpoints off-screen.

In numeric and animation curves, hold Ctrl and scroll over the canvas to zoom the Y axis around the mouse position. Scroll down to show a wider value range and give Bezier handles more room; scroll up for finer editing. Ctrl+double-click restores automatic Y-axis bounds. Zooming changes only the view, preserves node/handle values and selection, and does not regenerate subtitles or add undo steps. Manual zoom survives range refreshes and window resizing; loading another curve restores automatic bounds. Color and text curves have no numeric Y axis and are unaffected.

Manual color bands and inserted color nodes use the same interpolation and color space as generated subtitles, including stepped segments. Switching RGB, HSL, or OKLab refreshes the manual band immediately without changing saved path samples. Smooth HSL interpolation follows adjacent hues across the hue wrap instead of falling back to RGB. Numeric and animation Bezier curves use bracketed root solving to handle flat derivatives without replacing the user's handle coordinates.

Tag enable switches and path smoothing controls preserve existing curve nodes and interpolation modes. Editing an endpoint in the tag panel changes only that endpoint; coordinate inputs affect only the edited axis, preserving the other axis and unedited high-precision values. Generation settings contain independent curve and animation snapshots, so reading widget settings or making later edits cannot rewrite an earlier generation request.

Switching the current subtitle line refreshes defaults only for unconfigured tags. Enabled tags and previously edited curves keep their shared endpoint values, even when temporarily disabled. Position/origin axes are enabled independently: a disabled axis retains each source line's coordinate in generated ASS, not the currently viewed line's value. Output still uses standard `\pos(x,y)` and `\org(x,y)` tags. Both `\fad` curves remain unchanged even if their tabs have not been opened. Older paired position/origin presets are expanded into independent axes when loaded.

## Presets

Presets are saved as `.ggpreset` JSON files with a formal schema:

```json
{
  "format": "GradientGUI Preset",
  "version": 4,
  "tags": {},
  "curves": {},
  "path_sampling": {},
  "range_settings": {},
  "animation": {
    "settings": {
      "enabled_tags": [],
      "seam_blend_length": 0,
      "seam_blend_lengths": {}
    },
    "curves": {}
  },
  "ui_state": {}
}
```

There is intentionally no legacy preset compatibility yet, because the preset format was finalized before public preset files were created.

The GUI also keeps a local recent-preset list and shows a summary before loading a preset. The recent list is stored on the local machine and is not part of portable preset files.

Preset files, debug reports, and completed ASS output are written to a temporary file in the destination directory, then replaced only after a successful write. Failed saves leave existing files intact instead of publishing incomplete output. This does not change the live video-preview write path.

Undo/redo and preset loading restore curves, interpolation modes, per-tag animation, saved path samples, and range settings consistently from the editors or either overview. Restoring another active line also refreshes its path indicators and preview-frame range. Tags omitted from a preset are disabled. If applying a preset fails, the previous editing state is restored and undo recording remains available.

Undo/redo commits its history position only after the effect has been restored successfully. A failed restore keeps the current effect and both history stacks intact, reports the error, and can be retried without skipping an edit. The history retains up to 500 snapshots; making a new edit after undo starts a new branch and clears redo normally.

Preset loading validates curve coordinates, colors, interpolation modes, animation parameters, saved samples, and control settings before showing the load confirmation or changing the current effect and undo history. Invalid data reports its exact field path instead of silently changing values or discarding samples. Optional sections remain optional; valid numeric precision and Bezier handles are preserved.

## Debug Reports

The debug report export writes a local JSON file containing:

- Current dependency health result.
- Current preset/effect state.
- Selected source events and cached libass bounds.
- Source clip bounds.
- Last preview summary and debug overlay data.
- Last frame-sampling error, if any.

Use this when a range, clip, path color, or dependency issue needs to be reproduced outside the live GUI.

When the launcher is run from a debug CMD, the development Python source tree
prints startup stage timings to that console, including UI construction, ASS
parsing, dependency health checks, and mpv video loading. ffmpeg subprocess
output is decoded with UTF-8 and replacement for invalid bytes so Windows debug
consoles do not crash on GBK decode errors.

## Repository Layout

```text
.
├── autoload/
│   └── Ioroid.GradientGUI.lua      Aegisub Automation launcher
├── GradientGUI/
│   ├── main.py                     Python entry point
│   ├── requirements.txt
│   ├── GradientGUI.spec             PyInstaller spec
│   ├── libmpv-2.dll
│   ├── libass/                     libass and runtime DLLs
│   ├── engine/                     ASS parsing, gradient generation, bounds, sampling
│   ├── gui/                        PySide6 GUI widgets and controllers
│   └── tests/                      Architecture, model, cache, preset, render regression tests; sample ASS/video fixtures
```

## Startup Health Check

When the GUI opens an ASS file, it runs a lightweight dependency check before preview work starts:

- `libass` can be loaded and initialized.
- `mpv` / `libmpv` is available for video preview.
- `ffmpeg` can be launched.

Font availability is intentionally not checked here. If a check fails, the GUI shows a warning with the failing item and continues opening so project data can still be inspected or edited. Portable builds should bundle `ffmpeg.exe`, `libmpv-2.dll`, and the `libass` DLL set beside the application.

## Requirements

Runtime:

- Windows 64-bit
- Linux x64 (tested only in WSL)
- Aegisub with Lua Automation support
- Python 3.12 or newer for development mode
- FFmpeg for frame extraction
- libmpv for video preview
- libass for rendered subtitle bounds

Windows portable runtime files:

- `ffmpeg.exe`
- `libmpv-2.dll`
- `GradientGUI/libass/*.dll`

Linux x64/WSL development packages:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg mpv libmpv-dev libass-dev patchelf
```

Python packages:

```text
PySide6>=6.5.0
python-mpv>=1.0.0
Pillow>=10.0.0
pyinstaller>=6.0.0
```

Install dependencies:

```bash
cd GradientGUI
python -m pip install -r requirements.txt
```

On Linux x64, prefer a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r GradientGUI/requirements.txt
```

## Running In Development Mode

From this project directory:

```bash
python GradientGUI/main.py --input path/to/input.ass --output path/to/output.ass
```

Or from inside `GradientGUI/`:

```bash
python main.py --input path/to/input.ass --output path/to/output.ass
```

When launched by Aegisub, the Lua script passes temporary input/output ASS paths to the Python GUI.

## Building A Portable Package

A portable release should run on another Windows machine without installing
Python or native dependencies globally. Aegisub itself is still required because
the Lua launcher runs inside Aegisub.

Build from this project directory:

```bash
cd GradientGUI
python -m pip install -r requirements.txt
pyinstaller --clean GradientGUI.spec
```

Before publishing a new release, update `GradientGUI/gui/app_version.py` so `APP_VERSION` matches the GitHub Release tag.

The Windows spec excludes third-party ICU DLLs found on the build machine's `PATH`, so Qt uses the compatible ICU library supplied by Windows instead of an unrelated tool's DLL.

Then copy `ffmpeg.exe` into the generated app folder:

```powershell
Copy-Item <path-to-ffmpeg.exe> dist\GradientGUI\
```

The generated `dist/GradientGUI/` folder already contains:

- `GradientGUI.exe`
- `libmpv-2.dll`
- `libass/*.dll`
- PyInstaller runtime files
- PySide6 runtime files

Avoid PyInstaller `onefile` for this project unless you specifically need a single executable. It usually starts slower because the bundle must be extracted before the GUI can open.

Linux x64 builds use the same `GradientGUI.spec`, but normally rely on system
`ffmpeg`, `libmpv.so`, and `libass.so` from the distribution:

```bash
cd GradientGUI
python -m pip install -r requirements.txt
pyinstaller --clean GradientGUI.spec
```

The generated Linux x64 executable is `dist/GradientGUI/GradientGUI`. If you want a
more portable Linux x64 bundle, copy compatible `libmpv.so*` and `libass.so*` files
beside the app before building, then verify the result on a clean Linux machine.

## Tests

Run all tests from this project directory:

```bash
python -m unittest discover -s GradientGUI/tests -p "test_*.py" -v
```

Current test coverage includes:

- Engine/GUI dependency boundary checks.
- Structured path sampling model.
- Path color sampling cache.
- Formal preset schema.
- Render regression tests for bounds, clip envelopes, border/shadow/blur expansion, source `\clip`, group range, angled mode, and `\pos`.
- Pixel-level angled seam tests cover non-unit steps, positive and negative angles, increasing/decreasing `\fscy`, source vector/inverse masks, and scaled clip coordinates used by animation and debug overlays.
- Gradient animation tests for per-tag frame slicing, cyclic tag shifting, transform output, split fallback, movement curves, and merged-range phase targets.
- Pixel-by-pixel libass comparisons of sliced source transforms, movement, fades, and karaoke, including GBC drawings, changing gradient phases, prefix/suffix intervals, and rounded frame boundaries.
- Warm/cold animation-cache comparisons after saved-sample edits, sub-cent shear precision, and signed shadow offsets during seam blending.
- GBC text control sequences, drawing spans, and full-cycle shifts, using synthetic subtitle text.
- ASS format-column mapping, text whitespace, BOM/CRLF input, extension fields, attachment round trips, and shared preview/bounds styles, including real libass checks for modified styles.
- Exact timestamp frame-cache tests, including requests less than 10 milliseconds apart.
- Real Qt widget tests for initial multi-selection, selection restoration, the current-line button, reference checkboxes, and keyboard input.
- State-restoration tests with real Qt editors and both overview pages, including undo/redo, preset loading, omitted tags, failed-load rollback, and active-line frame/path state.
- Curve editing tests for zero-coordinate handles, mirror round trips, redraw invariance, endpoint constraints, full-precision table round trips, invalid input, and Excel-pasted animation nodes.
- Viewport mouse-event tests for multi-node dragging, integer animation frames, axis locks, Bezier handles, range refreshes, interrupted drags, and complete main-window animation snapshots.
- Independent Bezier root checks, smooth HSL hue-wrap checks, generated GBC color checks, and actual Qt color-band pixel comparisons against engine interpolation in all color spaces and modes.
- Numeric curve extrema tests cover narrow peaks, negative valleys, mixed interpolation, mirroring, border/shadow/blur extents, scale, and coordinate axes. Real libass regressions check peak-strip clipping in Horizontal/Vertical modes, unsmoothed path sampling with merged ranges, and projected envelopes in angled modes.
- Tag-control interaction tests across all supported tags, including endpoint/axis isolation, smoothing and enable switches, overview editing, configuration snapshot isolation, and GBC output restoration after cancelling live color preview.
- Save-failure tests with synthetic data, covering serialization, encoding, disk flush, and file replacement failures without damaging existing files or publishing partial output.
- Preset validation tests cover non-finite values, invalid colors and modes, malformed nested fields and sample data, and both load entry points without changing the effect or undo/redo history.
- Main-window path initialization and selection-order invariance, per-line path-state isolation, and saved-sample generation without a video in all four modes.
- Background update-check version parsing.

## Debugging

Range calculation can emit debug information:

```bash
set GRADIENTGUI_DEBUG_RANGE=1
python GradientGUI/main.py --input input.ass --output output.ass
```

## Notes And Limitations

- Windows remains the primary release target. Linux x64 support has been tested only in WSL.
- Startup speed is affected by Qt/PySide6, mpv, video loading, libass bounds, and preview generation.
- Some generated gradients can create many ASS events, especially small-step angled gradients over multiple lines.
- The current renderer and tests are centered on libass behavior.
