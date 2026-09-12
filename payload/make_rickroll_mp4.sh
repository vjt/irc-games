#!/usr/bin/env bash
# make_rickroll_mp4.sh — generate the "film" rickroll payload the !list gag
# hands out over DCC when a victim types `xdcc send #N` for a movie pack.
#
# Everything is SYNTHESISED with ffmpeg: a fake distributor bumper, then the
# reveal. No real footage and no real audio — we never redistribute a studio
# intro (MGM/Sony/...) or the Rick Astley recording, both copyrighted. The
# joke lands on the reveal, not on the soundtrack: the leecher asked for warez
# and got a bouncing "SEI STATO RICKROLLATO" with a cheesy synth beep.
#
# Output: rickroll.mp4 (640x360, ~12s, small). Deterministic, no network.
#
# Usage: ./make_rickroll_mp4.sh [output.mp4]
set -euo pipefail

OUT="${1:-rickroll.mp4}"
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
[ -f "$FONT" ] || { echo "font mancante: $FONT" >&2; exit 1; }

# Two drawtext stages gated on time: bumper (0-3.5s), reveal (3.5-12s).
# The reveal text bounces vertically and cycles colour, so it reads as a
# deliberate gag, not a broken render. fontcolor uses a stepped expression so
# it flips through a few colours over time without needing a filter per frame.
BUMPER="DIOBOIA CREW presents"
REVEAL1="SEI STATO RICKROLLATO"
REVEAL2="Never Gonna Give You Up"
REVEAL3="xdcc = culo, mona"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "color=c=black:s=640x360:d=12:r=25" \
  -f lavfi -i "sine=frequency=392:beep_factor=2:duration=12,tremolo=f=6:d=0.7,volume=0.25" \
  -filter_complex "
    [0:v]
    drawtext=fontfile=${FONT}:text='${BUMPER}':fontcolor=white:fontsize=30:
      x=(w-text_w)/2:y=(h-text_h)/2:alpha='if(lt(t,3.5),min(1,t*1.2),0)':
      enable='between(t,0,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL1}':fontsize=40:fontcolor=red:
      x=(w-text_w)/2:y=70+20*sin(3*t):enable='gte(t,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL2}':fontcolor=cyan:fontsize=26:
      x=(w-text_w)/2:y=180+20*sin(3*t+1):enable='gte(t,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL3}':fontcolor=lime:fontsize=22:
      x=(w-text_w)/2:y=270+20*sin(3*t+2):enable='gte(t,4.5)'
    [v]
  " \
  -map "[v]" -map 1:a \
  -c:v libx264 -pix_fmt yuv420p -profile:v baseline -crf 30 \
  -c:a aac -b:a 64k -shortest \
  "$OUT"

echo "scritto $OUT ($(du -h "$OUT" | cut -f1))"
