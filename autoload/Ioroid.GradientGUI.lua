-- ============================================================================
-- GradientGUI Launcher for Aegisub
-- ============================================================================
--
-- This script passes the selected subtitle lines to the external GradientGUI
-- application for real-time gradient editing. When the GUI finishes, generated
-- subtitle lines are read back and replace the original selection.
--
-- First-time users should read the project page before running this launcher:
-- https://github.com/loroid/AegisubGradientGUI
-- DependencyControl installs this Lua launcher, but the portable GradientGUI
-- application folder must still be downloaded and placed beside the script.
--
-- Workflow:
--   1. Collect [Script Info], [V4+ Styles], selected events, and video context.
--   2. Write a temporary .ass file.
--   3. Launch GradientGUI.exe and wait for it to exit.
--   4. Read the output file and replace the selected events.
--   5. Remove temporary files.

script_name = "Gradient GUI"
script_description = "Launch external GradientGUI for real-time gradient editing. See the project page for setup."
script_author = "Ioroid"
script_version = "1.0.0"
script_namespace = "Ioroid.GradientGUI"

local PROJECT_URL = "https://github.com/loroid/AegisubGradientGUI"

local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local version = nil
if has_dependency_control then
    version = DependencyControl{
        feed = "https://raw.githubusercontent.com/loroid/AegisubGradientGUI/main/DependencyControl.json",
        {}
    }
end

-- Global path initialization. This runs immediately when Aegisub loads the script.
local _info = debug.getinfo(1, "S")
-- match("^@?(.*[\\/])"): supports sources with or without a leading "@" and
-- keeps everything up to the final slash/backslash.
local SCRIPT_DIR = _info.source:match("^@?(.*[\\/])") or ""
local PATH_SEP = (package.config and package.config:sub(1, 1)) or "\\"
local IS_WINDOWS = PATH_SEP == "\\"

local function path_join(...)
    local parts = {...}
    local path = tostring(parts[1] or "")
    for i = 2, #parts do
        local part = tostring(parts[i] or "")
        if path:match("[\\/]$") then
            path = path .. part
        else
            path = path .. PATH_SEP .. part
        end
    end
    return path
end

local function shell_quote(value)
    value = tostring(value)
    if IS_WINDOWS then
        return '"' .. value:gsub('"', '\\"') .. '"'
    end
    return "'" .. value:gsub("'", "'\\''") .. "'"
end

-- Final fallback if debug.getinfo fails completely, using the portable autoload path.
if SCRIPT_DIR == "" then
    SCRIPT_DIR = path_join(aegisub.decode_path("?data"), "automation", "autoload") .. PATH_SEP
end

-- Bounds measurement is handled by Python/libass. Lua only transports data.
local _module_debug = "python_libass_bounds"

-- Main

-- Convert milliseconds to ASS time string (H:MM:SS.cc).
local function ms_to_time(ms)
    if type(ms) ~= "number" then return "0:00:00.00" end
    ms = math.max(0, ms)
    local s = math.floor(ms / 1000)
    local h = math.floor(s / 3600)
    local m = math.floor((s % 3600) / 60)
    s = s % 60
    local cs = math.floor((ms % 1000) / 10)
    return string.format("%d:%02d:%02d.%02d", h, m, s, cs)
end

-- Convert ASS time string to milliseconds.
local function time_to_ms(time_str)
    if type(time_str) ~= "string" then return 0 end
    local h, m, s, cs = time_str:match("(%d+):(%d+):(%d+)%.(%d+)")
    if not h then return 0 end
    return (tonumber(h) * 3600 + tonumber(m) * 60 + tonumber(s)) * 1000 + tonumber(cs) * 10
end

local function int_string(value)
    local number = tonumber(value)
    if not number then return nil end
    return tostring(math.floor(number + 0.5))
end

local function write_event_frame_metadata(out, event_pos, line)
    if type(aegisub.frame_from_ms) ~= "function" or type(aegisub.ms_from_frame) ~= "function" then
        return
    end

    local start_ms = tonumber(line.start_time or 0) or 0
    local end_ms = tonumber(line.end_time or start_ms) or start_ms
    local ok_first, first_frame = pcall(aegisub.frame_from_ms, start_ms)
    local ok_end, end_frame_start_mode = pcall(aegisub.frame_from_ms, end_ms)
    if not ok_first or not ok_end or first_frame == nil or end_frame_start_mode == nil then
        return
    end

    first_frame = math.max(0, math.floor((tonumber(first_frame) or 0) + 0.5))
    -- Aegisub's visible frame fields are whole-video frame numbers. The Lua
    -- frame_from_ms helper uses START timing, which is one frame later than the
    -- frame number shown for the timestamp in the subtitle grid around frame
    -- boundaries, so normalize the exported range back by one frame.
    first_frame = math.max(0, first_frame - 1)
    local last_frame = math.floor((tonumber(end_frame_start_mode) or first_frame) + 0.5) - 1
    last_frame = math.max(first_frame, last_frame)

    out:write("Event Start MS " .. event_pos .. ": " .. tostring(math.floor(start_ms + 0.5)) .. "\n")
    out:write("Event End MS " .. event_pos .. ": " .. tostring(math.floor(end_ms + 0.5)) .. "\n")
    out:write("Event Frame Start " .. event_pos .. ": " .. tostring(first_frame) .. "\n")
    out:write("Event Frame End " .. event_pos .. ": " .. tostring(last_frame) .. "\n")

    local times = {}
    for frame = first_frame, last_frame + 1 do
        local ok_ms, frame_ms = pcall(aegisub.ms_from_frame, frame)
        local ms_text = ok_ms and int_string(frame_ms) or nil
        if ms_text then
            table.insert(times, tostring(frame) .. ":" .. ms_text)
        end
    end
    if #times > 0 then
        out:write("Event Frame Times " .. event_pos .. ": " .. table.concat(times, ",") .. "\n")
    end
end

local function launch_gui(subtitles, selected_lines)
    -- Validate selection
    if #selected_lines == 0 then
        aegisub.log("Please select at least one subtitle line first.\n")
        return
    end

    -- Find script directory and GUI path
    local script_dir = SCRIPT_DIR

    -- Search order: portable app -> Python source -> development dist app.
    local executable_name = IS_WINDOWS and "GradientGUI.exe" or "GradientGUI"
    local search_paths = {
        path_join(script_dir, "GradientGUI", executable_name),
    }
    local gui_py = path_join(script_dir, "GradientGUI", "main.py")
    local dev_dist_exe = path_join(script_dir, "GradientGUI", "dist", "GradientGUI", executable_name)

    local gui_exe = nil
    local use_python = false
    for _, path in ipairs(search_paths) do
        local f = io.open(path, "r")
        if f then
            f:close()
            gui_exe = path
            break
        end
    end

    if not gui_exe then
        -- Prefer the source tree in development mode. A stale dist exe can exist
        -- beside main.py after local packaging, but it should not shadow source.
        local f = io.open(gui_py, "r")
        if f then
            f:close()
            use_python = true
        else
            local dist = io.open(dev_dist_exe, "r")
            if dist then
                dist:close()
                gui_exe = dev_dist_exe
            else
                aegisub.log("GradientGUI was not found:\n")
                for _, p in ipairs(search_paths) do
                    aegisub.log("  " .. p .. "\n")
                end
                aegisub.log("  " .. gui_py .. "\n")
                aegisub.log("  " .. dev_dist_exe .. "\n")
                aegisub.log("\nPlease read the project page before first use:\n")
                aegisub.log("  " .. PROJECT_URL .. "\n")
                aegisub.log("DependencyControl installs only this Lua launcher. The portable GradientGUI folder must be downloaded separately and placed beside the launcher script.\n")
                return
            end
        end
    end

    -- Prepare temp file paths
    local temp_dir = aegisub.decode_path("?temp")
    local input_path = path_join(temp_dir, "gradient_gui_input.ass")
    local output_path = path_join(temp_dir, "gradient_gui_output.ass")
    local launch_log_path = path_join(temp_dir, "gradient_gui_launcher.log")

    -- Remove old output file
    os.remove(output_path)
    os.remove(launch_log_path)

    -- Collect subtitle data.

    local out = io.open(input_path, "w")
    if not out then
        aegisub.log("Could not create temporary file: " .. input_path .. "\n")
        return
    end

    -- Write BOM for UTF-8
    out:write("\xEF\xBB\xBF")

    -- Collect all header/info/style lines and the events
    -- Iterate through the subtitle object
    local header_lines = {}
    local style_lines = {}

    for i = 1, #subtitles do
        local line = subtitles[i]
        if line.class == "info" then
            table.insert(header_lines, line)
        elseif line.class == "style" then
            table.insert(style_lines, line)
        end
    end

    -- Write [Script Info]
    out:write("[Script Info]\n")
    for _, line in ipairs(header_lines) do
        if line.key and line.value then
            out:write(line.key .. ": " .. line.value .. "\n")
        elseif line.raw then
            out:write(line.raw .. "\n")
        end
    end
    out:write("\n")

    -- Write [Aegisub Project Garbage] with video info
    local props = aegisub.project_properties()
    out:write("[Aegisub Project Garbage]\n")
    if props then
        if props.video_file and props.video_file ~= "" then
            out:write("Video File: " .. props.video_file .. "\n")
        end
        if props.video_position then
            out:write("Video Position: " .. tostring(props.video_position) .. "\n")
        end
    end

    -- Pass video resolution as context for the Python preview.
    local xres, yres = aegisub.video_size()
    if xres and yres then
        out:write("Video Width: " .. tostring(xres) .. "\n")
        out:write("Video Height: " .. tostring(yres) .. "\n")
    end
    out:write("\n")

    -- Write [GradientGUI Metadata]
    out:write("[GradientGUI Metadata]\n")
    out:write("Module Status: " .. _module_debug .. "\n")
    local exported_event_pos = 0
    for _, sel_idx in ipairs(selected_lines) do
        local line = subtitles[sel_idx]
        if line.class == "dialogue" and not line.comment then
            exported_event_pos = exported_event_pos + 1
            write_event_frame_metadata(out, exported_event_pos, line)
        end
    end
    out:write("\n")

    -- Write [V4+ Styles]
    out:write("[V4+ Styles]\n")
    out:write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    for _, sline in ipairs(style_lines) do
        if sline.raw then
            out:write(sline.raw .. "\n")
        else
            -- Reconstruct from fields
            out:write(string.format(
                "Style: %s,%s,%s,%s,%s,%s,%s,%d,%d,%d,%d,%s,%s,%s,%s,%d,%s,%s,%d,%04d,%04d,%04d,%d\n",
                sline.name or "Default",
                sline.fontname or "Arial",
                tostring(sline.fontsize or 48),
                sline.color1 or "&H00FFFFFF",
                sline.color2 or "&H000000FF",
                sline.color3 or "&H00000000",
                sline.color4 or "&H00000000",
                sline.bold and -1 or 0,
                sline.italic and -1 or 0,
                sline.underline and -1 or 0,
                sline.strikeout and -1 or 0,
                tostring(sline.scale_x or 100),
                tostring(sline.scale_y or 100),
                tostring(sline.spacing or 0),
                tostring(sline.angle or 0),
                sline.borderstyle or 1,
                tostring(sline.outline or 0),
                tostring(sline.shadow or 0),
                sline.align or 2,
                sline.margin_l or 0,
                sline.margin_r or 0,
                sline.margin_t or 0,
                sline.encoding or 1
            ))
        end
    end
    out:write("\n")

    -- Write [Events] with selected lines only
    out:write("[Events]\n")
    out:write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    for _, sel_idx in ipairs(selected_lines) do
        local line = subtitles[sel_idx]
        if line.class == "dialogue" then
            local prefix = line.comment and "Comment" or "Dialogue"
            
            out:write(string.format(
                "%s: %d,%s,%s,%s,%s,%04d,%04d,%04d,%s,%s\n",
                prefix,
                line.layer or 0,
                line.start_time and ms_to_time(line.start_time) or "0:00:00.00",
                line.end_time and ms_to_time(line.end_time) or "0:00:00.00",
                line.style or "Default",
                line.actor or "",
                line.margin_l or 0,
                line.margin_r or 0,
                line.margin_t or 0,
                line.effect or "",
                line.text or ""
            ))
        end
    end

    out:close()

    -- Launch GUI.

    aegisub.progress.title("GradientGUI")
    aegisub.progress.task("Waiting for GradientGUI...")

    local cmd
    if use_python then
        -- The debug launcher already provides a visible console. Run Python in
        -- that command context so startup stderr appears there without opening
        -- a second CMD window.
        if IS_WINDOWS then
            cmd = string.format(
                '""python" "%s" --input "%s" --output "%s""',
                gui_py, input_path, output_path
            )
        else
            cmd = string.format(
                "python3 %s --input %s --output %s",
                shell_quote(gui_py), shell_quote(input_path), shell_quote(output_path)
            )
        end
    else
        if IS_WINDOWS then
            -- Quote the whole command so Windows CMD handles paths with spaces.
            cmd = string.format(
                '""%s" --input "%s" --output "%s""',
                gui_exe, input_path, output_path
            )
        else
            cmd = string.format(
                "%s --input %s --output %s",
                shell_quote(gui_exe), shell_quote(input_path), shell_quote(output_path)
            )
        end
    end

    if not IS_WINDOWS then
        -- Aegisub may itself run from an AppImage. Do not leak its library and
        -- Qt plugin environment into the PySide6/PyInstaller child process.
        cmd = string.format(
            "env -u APPDIR -u APPIMAGE -u LD_LIBRARY_PATH -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH -u QML2_IMPORT_PATH -u PYTHONHOME -u PYTHONPATH %s > %s 2>&1",
            cmd,
            shell_quote(launch_log_path)
        )
    end

    -- Print the exact command to the Automation log.
    aegisub.log("Executing command:\n" .. cmd .. "\n")
    
    local execute_result = os.execute(cmd)

    -- Read output.

    local result_file = io.open(output_path, "r")
    if not result_file then
        aegisub.log("Cancelled, or the output file does not exist.\n")
        if execute_result ~= nil then
            aegisub.log("Launcher return value: " .. tostring(execute_result) .. "\n")
        end
        local launch_log = io.open(launch_log_path, "r")
        if launch_log then
            aegisub.log("GradientGUI launcher log:\n")
            local line_count = 0
            for line in launch_log:lines() do
                line_count = line_count + 1
                if line_count <= 120 then
                    aegisub.log(line .. "\n")
                elseif line_count == 121 then
                    aegisub.log("... log truncated ...\n")
                end
            end
            launch_log:close()
        end
        os.remove(input_path)
        return
    end

    -- Parse output events
    local new_lines = {}
    local in_events_section = false

    local first_line = true
    for raw_line in result_file:lines() do
        -- Strip UTF-8 BOM from first line if present
        if first_line then
            raw_line = raw_line:gsub("^\239\187\191", "")
            first_line = false
        end
        local trimmed = raw_line:match("^%s*(.-)%s*$")
        if trimmed == "[Events]" then
            in_events_section = true
        elseif trimmed:match("^Format:") then
            -- skip format line
        elseif in_events_section and (trimmed:match("^Dialogue:") or trimmed:match("^Comment:")) then
            -- Parse dialogue line
            local is_comment = trimmed:match("^Comment:")
            local prefix_len = is_comment and 9 or 10
            local content = trimmed:sub(prefix_len)

            -- Split into fields (max 10, last is text with commas)
            local fields = {}
            local field_count = 0
            local pos = 1
            while field_count < 9 do
                local comma_pos = content:find(",", pos)
                if not comma_pos then break end
                table.insert(fields, content:sub(pos, comma_pos - 1))
                pos = comma_pos + 1
                field_count = field_count + 1
            end
            table.insert(fields, content:sub(pos))  -- text field (rest)

            if #fields >= 10 then
                local new_line = {
                    class = "dialogue",
                    comment = is_comment ~= nil,
                    layer = tonumber(fields[1]) or 0,
                    start_time = time_to_ms(fields[2]:match("^%s*(.-)%s*$")),
                    end_time = time_to_ms(fields[3]:match("^%s*(.-)%s*$")),
                    style = fields[4]:match("^%s*(.-)%s*$"),
                    actor = fields[5]:match("^%s*(.-)%s*$"),
                    margin_l = tonumber(fields[6]) or 0,
                    margin_r = tonumber(fields[7]) or 0,
                    margin_t = tonumber(fields[8]) or 0,
                    effect = fields[9]:match("^%s*(.-)%s*$"),
                    text = fields[10],
                }
                table.insert(new_lines, new_line)
            end
        end
    end

    result_file:close()

    if #new_lines == 0 then
        aegisub.log("The output file did not contain any valid events.\n")
        os.remove(input_path)
        os.remove(output_path)
        return
    end

    -- Apply changes.

    -- Delete selected lines (in reverse order to preserve indices)
    local sorted_sel = {}
    for _, idx in ipairs(selected_lines) do
        table.insert(sorted_sel, idx)
    end
    table.sort(sorted_sel, function(a, b) return a > b end)

    for _, idx in ipairs(sorted_sel) do
        subtitles.delete(idx)
    end

    -- Insert new lines at the position of the first deleted line
    local insert_pos = selected_lines[1]
    for i, new_line in ipairs(new_lines) do
        subtitles.insert(insert_pos + i - 1, new_line)
    end

    -- Build new selection
    local new_sel = {}
    for i = 1, #new_lines do
        table.insert(new_sel, insert_pos + i - 1)
    end

    -- Cleanup
    os.remove(input_path)
    os.remove(output_path)

    aegisub.set_undo_point("Gradient GUI Apply")
    return new_sel
end

-- Register.

if version then
    version:registerMacro(launch_gui)
else
    aegisub.register_macro(script_name, script_description, launch_gui)
end
