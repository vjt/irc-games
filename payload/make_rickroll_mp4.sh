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
# Output: rickroll.mp4 (1920x1080, ~45s). Deterministic, no network.
#
# HD since 2026-09-14 (vjt, #sbiffo: "240kb che minchia di video e'? dovrebbe
# essere un bel rickroll in hd"). The size is the point as much as the picture:
# a 240 KB file answering a request for a 6 GB pack gives the gag away before
# it plays. Black frames compress to nothing whatever the resolution, so the
# reveal carries film grain — it is what makes an honestly-encoded HD file
# land in the tens of MB instead of hundreds of KB. The rate cap is there for
# the same reason in the other direction: grain at an uncapped CRF encodes to
# hundreds of MB, and nobody is pushing that over DCC for a joke.
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
SECONDS_TOTAL=45
BUMPER="DIOBOIA CREW presents"
REVEAL1="SEI STATO RICKROLLATO"
REVEAL2="Never Gonna Give You Up"
REVEAL3="xdcc = culo, mona"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "color=c=black:s=1920x1080:d=${SECONDS_TOTAL}:r=25" \
  -f lavfi -i "sine=frequency=392:beep_factor=2:duration=${SECONDS_TOTAL},tremolo=f=6:d=0.7,volume=0.25" \
  -filter_complex "
    [0:v]
    drawtext=fontfile=${FONT}:text='${BUMPER}':fontcolor=white:fontsize=90:
      x=(w-text_w)/2:y=(h-text_h)/2:alpha='if(lt(t,3.5),min(1,t*1.2),0)':
      enable='between(t,0,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL1}':fontsize=120:fontcolor=red:
      x=(w-text_w)/2:y=210+60*sin(3*t):enable='gte(t,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL2}':fontcolor=cyan:fontsize=78:
      x=(w-text_w)/2:y=540+60*sin(3*t+1):enable='gte(t,3.5)',
    drawtext=fontfile=${FONT}:text='${REVEAL3}':fontcolor=lime:fontsize=66:
      x=(w-text_w)/2:y=810+60*sin(3*t+2):enable='gte(t,4.5)',
    noise=alls=10:allf=t+u:all_seed=1911
    [v]
  " \
  -map "[v]" -map 1:a \
  -map_metadata -1 \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p -profile:v high -crf 23 \
  -maxrate 6M -bufsize 12M \
  -c:a aac -b:a 128k -shortest \
  "$OUT"

echo "scritto $OUT ($(du -h "$OUT" | cut -f1))"
