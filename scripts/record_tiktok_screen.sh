#!/usr/bin/env bash
# record_tiktok_screen.sh — capture a slice of your screen as a pristine,
# high-quality clip ready to drop into remy. Works on macOS (avfoundation) and
# Linux/X11; the backend is picked automatically. Two orientations:
#   vertical   → 1080x1920 (9:16) — TikTok full-screen (remy adds a caption).
#                Grabs the tallest centered 9:16 column and upscales it.
#   full       → your WHOLE screen at its native size (e.g. 1920x1200 on a
#                16:10 laptop), no aspect cropping, captured 1:1 — kept native
#                by remy. Best for terminals & screen recordings (wide content
#                fills the frame, no dead bars).
#
# Before capture it draws the chosen region on screen (a bright green frame) so
# you can arrange your window inside it instead of guessing.
#
# Quality: visually-lossless 10-bit 4:4:4 H.264 (CRF 14) by default — looks
# identical to the source, keeps text crisp, stays realtime at 60fps, and is
# re-encoded by remy afterward anyway. No audio (remy mutes by default;
# add a TikTok sound in-app). After capture the dead head/tail are trimmed
# (TRIM_HEAD / TRIM_TAIL seconds, 2 each by default).
#
# Commands ([full] grabs the whole screen; omit for the vertical default):
#   record_tiktok_screen.sh [full]        # interactive: arrange, record, q to stop
#   record_tiktok_screen.sh start [full]  # start in the background, frees the shell
#   record_tiktok_screen.sh stop          # stop a background recording (from anywhere)
#   record_tiktok_screen.sh status        # is a background recording running?
#   record_tiktok_screen.sh guide [full]  # just show the capture frame and exit
#   record_tiktok_screen.sh install       # symlink to ~/.local/bin/remy-rec (run anywhere)
#
# Env knobs:
#   ORIENT=full   whole screen native instead of the 9:16 column
#                                              TRIM_HEAD=2  trim N s off the start
#   DURATION=30   auto-stop after 30s          TRIM_TAIL=2  trim N s off the end
#   ENCODER=nvenc GPU encode (long sessions)   OUTDIR=DIR   where the .mp4 lands
#   GUIDE=0       skip the on-screen frame     SCREEN=1     macOS: grab display 1
#
# macOS: the first capture needs Screen Recording permission for your terminal
#   app (System Settings > Privacy & Security > Screen Recording) — grant it,
#   then restart the terminal. ENCODER=nvenc is Linux/NVIDIA only.
set -euo pipefail

# --- platform ----------------------------------------------------------------
OS="$(uname -s)"
is_macos() { [[ "$OS" == Darwin ]]; }
is_linux() { [[ "$OS" == Linux ]]; }

# Resolve $0 to an absolute path, following symlinks — GNU (readlink -f) and BSD
# (macOS, no readlink -f) both. `install` symlinks this script, and the detached
# background worker re-invokes SELF, so this must point at the real file.
resolve_self() {
    local src="$1" dir target
    if command -v realpath >/dev/null 2>&1; then realpath "$src" && return; fi
    if readlink -f "$src" >/dev/null 2>&1; then readlink -f "$src" && return; fi
    while [[ -L "$src" ]]; do
        target="$(readlink "$src")"
        case "$target" in /*) src="$target" ;; *) src="$(dirname "$src")/$target" ;; esac
    done
    dir="$(cd "$(dirname "$src")" && pwd)"
    printf '%s/%s\n' "$dir" "$(basename "$src")"
}

# ----------------------------- CONFIG (env-overridable) ----------------------
FPS="${FPS:-60}"               # capture/output frame rate
CRF="${CRF:-14}"               # x264/x265 quality (lower = better; 14 ≈ visually lossless)
PRESET="${PRESET:-faster}"     # encoder speed/efficiency tradeoff
ENCODER="${ENCODER:-x264}"     # x264 | x265 | nvenc | lossless
OUTDIR="${OUTDIR:-$PWD}"       # where the .mp4 lands
DRAW_MOUSE="${DRAW_MOUSE:-1}"  # 1 = show cursor (tutorials), 0 = hide
DURATION="${DURATION:-}"       # seconds; empty = record until you stop it
DISPLAY_ID="${DISPLAY:-:0.0}"  # X display to grab
REGION="${REGION:-}"           # override capture rect as WxH+X+Y (skips auto 9:16)
GUIDE="${GUIDE:-1}"            # 1 = draw the on-screen capture frame, 0 = skip
TRIM_HEAD="${TRIM_HEAD:-2}"    # seconds trimmed off the start after recording
TRIM_TAIL="${TRIM_TAIL:-2}"    # seconds trimmed off the end after recording
ORIENT="${ORIENT:-vertical}"   # vertical (1080x1920 9:16) | landscape (1920x1080 16:9)

# OUT_W/OUT_H are set from ORIENT in normalize_orient (called by prep_geometry).

# Background-recording state (so `stop`/`status` work from any directory).
STATE="${REMY_REC_STATE:-${XDG_RUNTIME_DIR:-/tmp}/remy-rec}"
SELF="$(resolve_self "$0")"
# -----------------------------------------------------------------------------

die() { echo "error: $*" >&2; exit 1; }

command -v ffmpeg >/dev/null || die "ffmpeg not found"

# Canonicalize ORIENT and set the output canvas + capture aspect from it.
# vertical  → 1080x1920 (9:16, TikTok full-screen, gets a remy caption)
# landscape → 1920x1080 (16:9, kept native by remy — great for terminals)
# full → the entire screen at its native resolution (e.g. a 1920x1200 16:10
#        laptop panel), no aspect cropping; OUT_W/OUT_H are filled from the
#        measured screen size in prep_geometry.
normalize_orient() {
    # `tr` for lowercasing (not ${x,,}) so this runs on stock macOS bash 3.2.
    local o; o="$(printf '%s' "$ORIENT" | tr '[:upper:]' '[:lower:]')"
    case "$o" in
        v|vert|vertical|portrait|9:16)   ORIENT=vertical;  OUT_W=1080; OUT_H=1920 ;;
        # landscape/16:9 aliases now mean "the whole screen" — no separate
        # cropped-16:9 mode (on a 16:10 panel it only lost a top/bottom margin).
        full|fullscreen|native|screen|h|horiz|horizontal|landscape|wide|16:9)
                                         ORIENT=full;      OUT_W=0;    OUT_H=0 ;;
        *) die "unknown ORIENT='$ORIENT' (use vertical | full)" ;;
    esac
}

# macOS: avfoundation grabs a whole *display*, addressed by a video-device index
# (`Capture screen 0`, `Capture screen 1`, …). Find it (SCREEN=N overrides).
AVF_SCREEN=""
detect_avf_screen() {
    [[ -n "$AVF_SCREEN" ]] && return 0
    if [[ -n "${SCREEN:-}" ]]; then AVF_SCREEN="$SCREEN"; return 0; fi
    local list
    list="$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 || true)"
    AVF_SCREEN="$(printf '%s\n' "$list" | \
        sed -n 's/.*\[\([0-9][0-9]*\)\][[:space:]]*Capture screen 0.*/\1/p' | head -1)"
    [[ -n "$AVF_SCREEN" ]] || AVF_SCREEN="$(printf '%s\n' "$list" | \
        sed -n 's/.*\[\([0-9][0-9]*\)\][[:space:]]*Capture screen.*/\1/p' | head -1)"
    [[ -n "$AVF_SCREEN" ]] || die "avfoundation reports no 'Capture screen' \
device. If this is a first run, grant your terminal Screen Recording permission \
(System Settings > Privacy & Security > Screen Recording), restart it, and retry."
}

# macOS: probe the display's real pixel size (authoritative on Retina, where the
# captured frame is larger than the logical point size) → echoes "W H", or
# returns non-zero (the caller must `die`; a `die` here would only exit the
# command-substitution subshell and let the script limp on with no size).
probe_screen_px() {
    local probe size
    probe="$(ffmpeg -hide_banner -f avfoundation -framerate "$FPS" \
        -i "${AVF_SCREEN}:none" -t 0.05 -f null - 2>&1 || true)"
    size="$(printf '%s\n' "$probe" | grep -oE '[0-9]{3,5}x[0-9]{3,5}' | head -1)"
    [[ -n "$size" ]] || return 1
    echo "${size%x*} ${size#*x}"
}

# --- work out the capture rectangle: largest centered slice of the chosen AR -
prep_geometry() {
    normalize_orient
    if is_linux; then
        [[ "${XDG_SESSION_TYPE:-x11}" == "x11" ]] || die "this recorder uses \
x11grab, but the session is '${XDG_SESSION_TYPE}'. On Wayland use \
wf-recorder/wl-screenrec instead."
    fi
    is_macos && detect_avf_screen   # need the screen device index either way
    if [[ -z "$REGION" ]]; then
        if is_macos; then
            # avfoundation captures the whole display; probe its pixel size so we
            # can crop a centered slice with the same math as the X11 path.
            # `die` in the caller (not the subshell) so a failure really stops us.
            local sz
            sz="$(probe_screen_px)" || die "couldn't read the screen size from \
avfoundation (screen device $AVF_SCREEN). Grant your terminal Screen Recording \
permission (System Settings > Privacy & Security > Screen Recording), restart \
it, and retry."
            read -r sw sh <<<"$sz"
            sx=0; sy=0
        else
            command -v xrandr >/dev/null || die "xrandr not found (needed to size \
the capture region); set REGION=WxH+X+Y to skip auto-detection"
            geo="$(xrandr --query | awk '/ connected primary/{print $4; exit}')"
            [[ -z "$geo" ]] && geo="$(xrandr --query | awk '/ connected/{print $3; exit}')"
            [[ "$geo" =~ ^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$ ]] || \
                die "could not parse screen geometry from xrandr (got '$geo')"
            IFS='x+' read -r sw sh sx sy <<<"$geo"
        fi

        if [[ "$ORIENT" == vertical ]]; then
            # tallest 9:16 slice that fits; clamp to width on very tall screens
            cap_h=$sh
            cap_w=$(awk -v h="$sh" 'BEGIN{w=int(h*9/16); print w - (w%2)}')
            if (( cap_w > sw )); then
                cap_w=$(( sw - sw%2 ))
                cap_h=$(awk -v w="$cap_w" 'BEGIN{h=int(w*16/9); print h - (h%2)}')
                (( cap_h > sh )) && cap_h=$(( sh - sh%2 ))
            fi
        else
            # full: the entire screen, native resolution (even dims for h264)
            cap_w=$(( sw - sw%2 )); cap_h=$(( sh - sh%2 ))
        fi
        off_x=$(( sx + (sw - cap_w) / 2 ))
        off_y=$(( sy + (sh - cap_h) / 2 ))
    else
        [[ "$REGION" =~ ^([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+)$ ]] || \
            die "REGION must look like 675x1200+622+0"
        cap_w="${BASH_REMATCH[1]}"; cap_h="${BASH_REMATCH[2]}"
        off_x="${BASH_REMATCH[3]}"; off_y="${BASH_REMATCH[4]}"
    fi
    # full keeps the source 1:1 — output matches the captured region exactly.
    # (Must be an `if`, not `[[…]] && …`: as the function's last statement a
    # false test would make prep_geometry return 1 and trip `set -e`.)
    if [[ "$ORIENT" == full ]]; then OUT_W=$cap_w; OUT_H=$cap_h; fi
}

# --- pick the video encoder --------------------------------------------------
build_encoder() {
    case "$ENCODER" in
        x264)     venc=(-c:v libx264 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv444p10le) ;;
        lossless) venc=(-c:v libx264 -preset "$PRESET" -qp 0     -pix_fmt yuv444p10le) ;;
        x265)     venc=(-c:v libx265 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv444p10le -tag:v hvc1) ;;
        nvenc)    venc=(-c:v hevc_nvenc -preset p7 -tune hq -rc vbr -cq "$CRF" -pix_fmt p010le -tag:v hvc1) ;;
        *)        die "unknown ENCODER='$ENCODER' (use x264 | x265 | nvenc | lossless)" ;;
    esac
    set_final_tag
}

# hevc needs the hvc1 brand carried onto the remuxed/trimmed copies; h264 doesn't.
# NOTE: for x264 this is an *empty* array, so every expansion uses the
# "${final_tag[@]+"${final_tag[@]}"}" guard — plain "${final_tag[@]}" on an empty
# array trips `set -u` on macOS's stock bash 3.2 ("unbound variable").
set_final_tag() {
    case "$ENCODER" in
        x265|nvenc) final_tag=(-tag:v hvc1) ;;
        *)          final_tag=() ;;
    esac
}

prep_paths() {
    mkdir -p "$OUTDIR"
    out="$(cd "$OUTDIR" && pwd)/screen_$(date +%Y%m%d_%H%M%S).mp4"
    # Record into a *fragmented* MP4 intermediate, not straight to $out. A plain
    # MP4 only becomes playable after a finalize pass at the very end (it writes
    # the moov index, and +faststart then rewrites the whole file). Interrupt
    # that pass and the moov never lands, so no editor can open the result. A
    # fragmented MP4 is written as self-contained ~1s chunks with no trailing
    # moov, so whatever reached disk stays playable no matter how it stopped. On
    # stop we losslessly remux the chunks into a clean faststart MP4.
    rec="${out%.mp4}.part.mp4"
}

build_cmd() {
    local setp="setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    local vf crop=""
    if is_macos; then
        # avfoundation hands us the whole display — crop the chosen rectangle in
        # the filtergraph (x11grab does this in-hardware via the input offset).
        # `full` with no REGION needs no crop: the capture already IS the screen.
        [[ "$ORIENT" != full || -n "$REGION" ]] && \
            crop="crop=${cap_w}:${cap_h}:${off_x}:${off_y},"
        vf="${crop}scale=${OUT_W}:${OUT_H}:flags=lanczos:in_range=full:out_range=tv,${setp}"
        cmd=(ffmpeg -hide_banner -loglevel warning -stats
             -f avfoundation -capture_cursor "$DRAW_MOUSE" -framerate "$FPS"
             -i "${AVF_SCREEN}:none")
    else
        vf="scale=${OUT_W}:${OUT_H}:flags=lanczos:in_range=full:out_range=tv,${setp}"
        cmd=(ffmpeg -hide_banner -loglevel warning -stats
             -f x11grab -framerate "$FPS" -draw_mouse "$DRAW_MOUSE"
             -video_size "${cap_w}x${cap_h}" -i "${DISPLAY_ID}+${off_x},${off_y}")
    fi
    [[ -n "$DURATION" ]] && cmd+=(-t "$DURATION")
    cmd+=(-vf "$vf"
          "${venc[@]}"
          -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv
          -g "$FPS" -movflags +frag_keyframe+empty_moov+default_base_moof -an
          "$rec")
}

# --- finalize + trim ---------------------------------------------------------
finalize_rec() {
    [[ -n "${rec:-}" && -s "$rec" ]] || { echo "nothing was captured." >&2; return 1; }
    # -c copy: no re-encode, just rebuild a normal front-loaded moov. Works even
    # on a partial capture — a trailing half-written fragment is dropped.
    if ffmpeg -hide_banner -loglevel error -y -i "$rec" \
            -map 0 -c copy "${final_tag[@]+"${final_tag[@]}"}" -movflags +faststart "$out" \
            2>/dev/null && [[ -s "$out" ]]; then
        rm -f "$rec"
    else
        # remux failed (capture too short for even one full fragment) — keep the
        # raw fragmented file; it is itself playable
        mv -f "$rec" "$out"
    fi
}

# Trim TRIM_HEAD off the front and TRIM_TAIL off the end. The recorder forces a
# keyframe every second (-g $FPS), so an integer-second cut lands exactly on a
# keyframe and a stream copy is frame-accurate — no re-encode, no frozen lead-in.
trim_clip() {
    local head="${TRIM_HEAD:-0}" tail="${TRIM_TAIL:-0}"
    (( head > 0 || tail > 0 )) 2>/dev/null || return 0
    command -v ffprobe >/dev/null || { echo "⚠ ffprobe missing — skipping trim" >&2; return 0; }
    [[ -s "$out" ]] || return 0
    local dur keep
    dur=$(ffprobe -v error -show_entries format=duration \
                  -of default=nw=1:nk=1 "$out" 2>/dev/null || echo 0)
    keep=$(awk -v d="$dur" -v h="$head" -v t="$tail" 'BEGIN{printf "%.3f", d-h-t}')
    if awk -v k="$keep" 'BEGIN{exit !(k < 1.0)}'; then
        echo "⚠ clip is only ${dur}s — too short to trim ${head}s+${tail}s; keeping full length" >&2
        return 0
    fi
    local tmp="${out%.mp4}.trim.mp4"
    if ffmpeg -hide_banner -loglevel error -y -ss "$head" -i "$out" -t "$keep" \
            -map 0 -c copy "${final_tag[@]+"${final_tag[@]}"}" -movflags +faststart "$tmp" \
            2>/dev/null && [[ -s "$tmp" ]]; then
        mv -f "$tmp" "$out"
        echo "✂  trimmed ${head}s head + ${tail}s tail"
    else
        rm -f "$tmp"
        echo "⚠ trim failed — keeping the untrimmed clip" >&2
    fi
}

report() {
    echo
    echo "✅ saved $out  ($(du -h "$out" 2>/dev/null | cut -f1))"
    echo "   edit it:  venv/bin/python3 -m remy \"$out\" -c \"Your caption ⚡\" -o edited.mp4"
}

# --- on-screen capture frame -------------------------------------------------
# Draws the exact 9:16 capture column as a bright border (4 thin always-on-top
# borderless bars + a label) so you can see where the recording lands and drag
# your window into it. Destroyed before capture starts, so it's never filmed.
# Blocks until you press Enter in this terminal. Returns non-zero if it can't
# draw (no python3/tkinter) so the caller can fall back to a printed geometry.
show_guide() {
    command -v python3 >/dev/null || return 1
    python3 <(cat <<'PY'
import sys, time, select
try:
    import tkinter as tk
except Exception:
    sys.exit(1)
w, h, x, y, mode = (int(sys.argv[1]), int(sys.argv[2]),
                    int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
# arg6 = screen width in *pixels* (macOS/Retina); 0 = coords already in points.
screen_px = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
T = 6  # border thickness (points)
try:
    root = tk.Tk()
except Exception:
    sys.exit(1)
root.withdraw()
# The capture rect is in pixels, but tkinter positions windows in logical
# points. On a Retina display those differ by the backing scale factor; convert
# so the green frame lands exactly on the region ffmpeg will crop.
scale = screen_px / root.winfo_screenwidth() if screen_px > 0 else 1.0
if scale != 1.0:
    w, h, x, y = int(w / scale), int(h / scale), int(x / scale), int(y / scale)

def bar(gw, gh, gx, gy):
    t = tk.Toplevel(root)
    t.overrideredirect(True)
    try:
        t.attributes('-topmost', True)
    except tk.TclError:
        pass
    t.configure(bg='#39FF14')
    t.geometry(f'{gw}x{gh}+{gx}+{gy}')
    return t

bars = [bar(w, T, x, y), bar(w, T, x, y + h - T),
        bar(T, h, x, y), bar(T, h, x + w - T, y)]
lbl = tk.Toplevel(root)
lbl.overrideredirect(True)
try:
    lbl.attributes('-topmost', True)
except tk.TclError:
    pass
tk.Label(lbl, text=' TikTok capture area — put your window here · Enter to start ',
         bg='#39FF14', fg='black', font=('DejaVu Sans', 13, 'bold')).pack()
lbl.update_idletasks()
lbl.geometry(f'+{x + max(0, (w - lbl.winfo_width()) // 2)}+{y + 12}')
root.update()
if mode == 'wait':
    while True:
        root.update()
        if select.select([sys.stdin], [], [], 0.08)[0]:
            sys.stdin.readline()
            break
else:
    end = time.time() + float(mode)
    while time.time() < end:
        root.update()
        time.sleep(0.05)
root.destroy()
PY
) "$cap_w" "$cap_h" "$off_x" "$off_y" "wait" "${sw:-0}"
}

arrange_pause() {
    if [[ "$GUIDE" != 0 ]]; then
        echo "   ➜ a green frame marks the capture area — drag your window into it,"
        echo "     then press Enter here to start recording."
        show_guide || {
            echo "   (couldn't draw the frame; capture column is \
${cap_w}x${cap_h} at +${off_x},+${off_y} — arrange there)"
            read -r -p "   press Enter to start recording… " _
        }
    else
        local n="${COUNTDOWN:-3}"
        if [[ "$n" -gt 0 ]] 2>/dev/null; then
            for ((i = n; i > 0; i--)); do
                printf '\r   ➜ recording in %ss… ' "$i"; sleep 1
            done
            echo
        fi
    fi
}

# Clear the terminal right before capture so this recorder's own banner (and
# whatever else was on screen) isn't baked into the opening frames.
clear_screen() {
    clear 2>/dev/null || printf '\033c'
    sleep 0.3   # let the compositor paint the cleared screen before frame 1
}

print_banner() {  # $1 = "fg" (q to stop) | "bg" (stop command)
    local scalenote="lanczos-scaled"
    [[ "$ORIENT" == full ]] && scalenote="native 1:1"
    local src="$DISPLAY_ID"
    is_macos && src="avfoundation screen ${AVF_SCREEN}"
    cat <<INFO
🎬 remy screen recorder
   source      : $src
   capture     : ${cap_w}x${cap_h} at +${off_x},+${off_y}  (native $ORIENT slice)
   output      : ${OUT_W}x${OUT_H} @ ${FPS}fps  ($ORIENT, $scalenote)
   encoder     : $ENCODER  ${ENCODER:+(crf ${CRF})}
   cursor      : $([[ "$DRAW_MOUSE" == 1 ]] && echo shown || echo hidden)
   trim        : ${TRIM_HEAD}s head + ${TRIM_TAIL}s tail (after recording)
   file        : $out
   ${DURATION:+stops after ${DURATION}s}
INFO
    if [[ "$1" == bg ]]; then
        echo "   ➜ stop it any time with:  ${REMY_REC_NAME:-$SELF} stop"
    else
        echo "   ➜ stop with  q <Enter>  (or Ctrl-C)."
    fi
}

# --- background state helpers ------------------------------------------------
recording_active() {
    [[ -f "$STATE/ffpid" ]] && kill -0 "$(cat "$STATE/ffpid" 2>/dev/null)" 2>/dev/null
}

write_state() {
    mkdir -p "$STATE"
    rm -f "$STATE/done" "$STATE/ffpid" "$STATE/summary"
    {
        echo "out='$out'"
        echo "rec='$rec'"
        echo "ENCODER='$ENCODER'"
        echo "TRIM_HEAD='$TRIM_HEAD'"
        echo "TRIM_TAIL='$TRIM_TAIL'"
        echo "started='$(date +%s)'"
    } > "$STATE/meta"
    printf '%s\0' "${cmd[@]}" > "$STATE/cmd"
}

# ============================ commands =======================================
run_foreground() {
    prep_geometry; build_encoder; prep_paths; build_cmd
    trap 'finish' EXIT
    print_banner fg
    arrange_pause
    clear_screen
    # Let ffmpeg — not the shell — own Ctrl-C: it flushes the current fragment
    # and exits, and the EXIT trap then remuxes + trims. The (non-empty) INT
    # trap keeps the shell from aborting first; `|| true` swallows ffmpeg's
    # interrupt status so `set -e` doesn't skip finish either.
    trap ':' INT
    "${cmd[@]}" || true
    trap - INT
}

finish() {
    trap - EXIT INT TERM
    [[ -n "${rec:-}" && -s "$rec" ]] || return 0
    finalize_rec || return 0
    trim_clip
    report
}

run_start() {
    recording_active && die "a recording is already running (pid \
$(cat "$STATE/ffpid")). stop it first:  $SELF stop"
    prep_geometry; build_encoder; prep_paths; build_cmd
    print_banner bg
    arrange_pause
    clear_screen
    write_state
    # Detach into its own session so it survives this shell closing and isn't
    # killed by the terminal. The worker re-enters this script, runs ffmpeg,
    # then finalizes + trims when ffmpeg exits (by `stop` signal or DURATION).
    # `setsid` is Linux-only; on macOS `nohup … & disown` gives the same detach.
    if command -v setsid >/dev/null 2>&1; then
        setsid bash "$SELF" __bg_worker "$STATE" </dev/null >"$STATE/log" 2>&1 &
    else
        nohup bash "$SELF" __bg_worker "$STATE" </dev/null >"$STATE/log" 2>&1 &
        disown 2>/dev/null || true
    fi
    echo "● recording in the background → $out"
    echo "   stop it (from any directory) with:  ${REMY_REC_NAME:-$SELF} stop"
    echo "   check it with:                       ${REMY_REC_NAME:-$SELF} status"
}

run_bg_worker() {
    local state="$1"
    # shellcheck disable=SC1091
    source "$state/meta"
    set_final_tag
    # Read the NUL-delimited argv back into an array. A `read -d ''` loop instead
    # of `mapfile -d ''` so this runs on stock macOS bash 3.2 (no mapfile).
    cmd=()
    while IFS= read -r -d '' _arg; do cmd+=("$_arg"); done < "$state/cmd"
    trap '' INT TERM       # ignore here; only ffmpeg should act on the signal
    "${cmd[@]}" &
    local ffpid=$!
    echo "$ffpid" > "$state/ffpid"
    wait "$ffpid" || true
    rm -f "$state/ffpid"
    { finalize_rec && trim_clip && report; } > "$state/summary" 2>&1 || true
    touch "$state/done"
}

run_stop() {
    if [[ ! -f "$STATE/ffpid" ]]; then
        if [[ -f "$STATE/done" ]]; then
            echo "already stopped."; cat "$STATE/summary" 2>/dev/null; return 0
        fi
        die "no recording is running. start one with:  $SELF start"
    fi
    local ffpid; ffpid="$(cat "$STATE/ffpid")"
    echo "⏹  stopping recording (finalizing + trimming)…"
    kill -INT "$ffpid" 2>/dev/null || true
    local i
    for i in $(seq 1 300); do [[ -f "$STATE/done" ]] && break; sleep 0.1; done
    if [[ -f "$STATE/done" ]]; then
        cat "$STATE/summary" 2>/dev/null
    else
        echo "⚠ still finalizing in the background; output will appear shortly \
(state: $STATE)" >&2
    fi
}

run_status() {
    if recording_active; then
        # shellcheck disable=SC1091
        source "$STATE/meta"
        # shellcheck disable=SC2154  # `started`/`out` come from meta
        echo "● recording ($(( $(date +%s) - started ))s) → $out"
    else
        echo "○ idle — no active recording"
    fi
}

run_install() {
    local dir="${1:-$HOME/.local/bin}" name="remy-rec"
    mkdir -p "$dir"
    ln -sf "$SELF" "$dir/$name"
    echo "linked $dir/$name → $SELF"
    case ":$PATH:" in
        *":$dir:"*) ;;
        *) echo "add $dir to your PATH:  export PATH=\"$dir:\$PATH\"" ;;
    esac
    echo "now, from any directory:  $name start   ·   $name stop   ·   $name status"
}

# ============================ dispatch =======================================
# An orientation token (vertical/full & aliases) can be passed as the 2nd arg
# to once/start/guide — e.g. `start full` — or used as the command itself as a
# shorthand for an interactive recording in that orientation.
case "${1:-once}" in
    ""|once)      ORIENT="${2:-$ORIENT}"; run_foreground ;;
    start)        ORIENT="${2:-$ORIENT}"; run_start ;;
    stop)         run_stop ;;
    status)       run_status ;;
    guide)        ORIENT="${2:-$ORIENT}"; prep_geometry; show_guide || die \
"couldn't draw the guide (need python3 + tkinter); capture region is \
${cap_w}x${cap_h} at +${off_x},+${off_y}" ;;
    install)      run_install "${2:-}" ;;
    __bg_worker)  run_bg_worker "${2:?state dir required}" ;;
    v|vert|vertical|portrait|9:16|h|horiz|horizontal|landscape|wide|16:9\
|full|fullscreen|native|screen)
                  ORIENT="$1"; run_foreground ;;
    -h|--help|help)
        sed -n '2,38p' "$SELF" | sed 's/^# \{0,1\}//' ;;
    *)            die "unknown command '$1' (use once|start|stop|status|guide|install \
[vertical|full])" ;;
esac
