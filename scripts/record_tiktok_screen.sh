#!/usr/bin/env bash
# record_tiktok_screen.sh — capture a vertical slice of your X11 screen as a
# pristine 1080x1920 (9:16) clip, ready to drop into tokcut.
#
# Your display is landscape and can't supply a native 1080x1920 region, so
# this grabs the tallest true-portrait 9:16 column from the center of the
# screen and upscales it to exactly 1080x1920 with lanczos. Arrange the app
# you're recording inside that center column (a tip is printed on launch).
#
# Quality: visually-lossless 10-bit 4:4:4 H.264 (CRF 14) by default — looks
# identical to the source, keeps text crisp, stays realtime at 60fps, and is
# re-encoded by tokcut afterward anyway. No audio (tokcut mutes by default;
# add a TikTok sound in-app).
#
# Usage:
#   scripts/record_tiktok_screen.sh                 # record until you press q
#   DURATION=30 scripts/record_tiktok_screen.sh     # auto-stop after 30s
#   ENCODER=nvenc scripts/record_tiktok_screen.sh   # GPU encode (long sessions)
#   ENCODER=lossless CRF unused                      # exact lossless (huge)
#
# Stop a running capture with  q <Enter>  (or Ctrl-C). The file is finalized
# either way. Tune via env vars (see CONFIG below).
set -euo pipefail

# ----------------------------- CONFIG (env-overridable) ----------------------
FPS="${FPS:-60}"               # capture/output frame rate
CRF="${CRF:-14}"               # x264/x265 quality (lower = better; 14 ≈ visually lossless)
PRESET="${PRESET:-faster}"     # encoder speed/efficiency tradeoff
ENCODER="${ENCODER:-x264}"     # x264 | x265 | nvenc | lossless
OUTDIR="${OUTDIR:-$PWD}"       # where the .mp4 lands
DRAW_MOUSE="${DRAW_MOUSE:-1}"  # 1 = show cursor (tutorials), 0 = hide
DURATION="${DURATION:-}"       # seconds; empty = record until you press q
DISPLAY_ID="${DISPLAY:-:0.0}"  # X display to grab
REGION="${REGION:-}"           # override capture rect as WxH+X+Y (skips auto 9:16)

OUT_W=1080                     # TikTok vertical canvas
OUT_H=1920
# -----------------------------------------------------------------------------

die() { echo "error: $*" >&2; exit 1; }

[[ "${XDG_SESSION_TYPE:-x11}" == "x11" ]] || \
    die "this recorder uses x11grab, but the session is '${XDG_SESSION_TYPE}'. \
On Wayland use wf-recorder/wl-screenrec instead."
command -v ffmpeg >/dev/null || die "ffmpeg not found"

# --- work out the capture rectangle: tallest centered 9:16 column ------------
if [[ -z "$REGION" ]]; then
    command -v xrandr >/dev/null || die "xrandr not found (needed to size the \
9:16 column); set REGION=WxH+X+Y to skip auto-detection"
    geo="$(xrandr --query | awk '/ connected primary/{print $4; exit}')"
    [[ -z "$geo" ]] && geo="$(xrandr --query | awk '/ connected/{print $3; exit}')"
    [[ "$geo" =~ ^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$ ]] || \
        die "could not parse screen geometry from xrandr (got '$geo')"
    IFS='x+' read -r sw sh sx sy <<<"$geo"

    # tallest 9:16 slice that fits; clamp to width on very tall screens
    cap_h=$sh
    cap_w=$(awk -v h="$sh" 'BEGIN{w=int(h*9/16); print w - (w%2)}')
    if (( cap_w > sw )); then
        cap_w=$(( sw - sw%2 ))
        cap_h=$(awk -v w="$cap_w" 'BEGIN{h=int(w*16/9); print h - (h%2)}')
        (( cap_h > sh )) && cap_h=$(( sh - sh%2 ))
    fi
    off_x=$(( sx + (sw - cap_w) / 2 ))
    off_y=$(( sy + (sh - cap_h) / 2 ))
else
    [[ "$REGION" =~ ^([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+)$ ]] || \
        die "REGION must look like 675x1200+622+0"
    cap_w="${BASH_REMATCH[1]}"; cap_h="${BASH_REMATCH[2]}"
    off_x="${BASH_REMATCH[3]}"; off_y="${BASH_REMATCH[4]}"
fi

# --- pick the video encoder --------------------------------------------------
case "$ENCODER" in
    x264)     venc=(-c:v libx264 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv444p10le) ;;
    lossless) venc=(-c:v libx264 -preset "$PRESET" -qp 0     -pix_fmt yuv444p10le) ;;
    x265)     venc=(-c:v libx265 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv444p10le -tag:v hvc1) ;;
    nvenc)    venc=(-c:v hevc_nvenc -preset p7 -tune hq -rc vbr -cq "$CRF" -pix_fmt p010le -tag:v hvc1) ;;
    *)        die "unknown ENCODER='$ENCODER' (use x264 | x265 | nvenc | lossless)" ;;
esac

mkdir -p "$OUTDIR"
out="$OUTDIR/screen_$(date +%Y%m%d_%H%M%S).mp4"

cat <<INFO
🎬 tokcut screen recorder
   display     : $DISPLAY_ID
   capture     : ${cap_w}x${cap_h} at +${off_x},+${off_y}  (native 9:16 slice)
   output      : ${OUT_W}x${OUT_H} @ ${FPS}fps  (lanczos-scaled)
   encoder     : $ENCODER  ${ENCODER:+(crf ${CRF})}
   cursor      : $([[ "$DRAW_MOUSE" == 1 ]] && echo shown || echo hidden)
   file        : $out
   ${DURATION:+stops after ${DURATION}s}

   ➜ Put what you're filming inside the center column of the screen.
   ➜ Press  q  then Enter to stop (or Ctrl-C). Recording…
INFO

cmd=(ffmpeg -hide_banner -loglevel warning -stats
     -f x11grab -framerate "$FPS" -draw_mouse "$DRAW_MOUSE"
     -video_size "${cap_w}x${cap_h}" -i "${DISPLAY_ID}+${off_x},${off_y}")
[[ -n "$DURATION" ]] && cmd+=(-t "$DURATION")
cmd+=(-vf "scale=${OUT_W}:${OUT_H}:flags=lanczos:in_range=full:out_range=tv,\
setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
      "${venc[@]}"
      -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv
      -g "$(( FPS * 2 ))" -movflags +faststart -an
      "$out")

"${cmd[@]}"

echo
echo "✅ saved $out  ($(du -h "$out" | cut -f1))"
echo "   edit it:  venv/bin/python3 -m tokcut \"$out\" -c \"Your caption ⚡\" -o edited.mp4"
