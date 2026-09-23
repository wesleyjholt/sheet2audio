#!/bin/bash
# Derive "scanned / photographed" variants of the scans fixtures.
#   bash tests/fixtures/scans/make_degraded.sh      (from the project root)
# Output: tests/fixtures/scans/degraded/<fixture>__<variant>.{png,jpg,pdf,tif}
# Requires pdftoppm (poppler) and ImageMagick 7. Deterministic apart from +noise.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
D="$HERE/degraded"; mkdir -p "$D"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

raster() { # pdf dpi outstem  -> gray PNG(s) outstem-N.png (always numbered)
  pdftoppm -r "$2" -gray -png "$1" "$3"
}
light() { # in out darkest-gray : diagonal uneven lighting (multiply by a gradient)
  magick "$1" -colorspace Gray \( +clone -sparse-color Barycentric "0,0 white %w,%h $3" \) \
    -compose Multiply -composite "$2"
}
topdf() { # dpi out in...  : image-only PDF, page size from dpi
  local dpi=$1 out=$2; shift 2
  magick "$@" -units PixelsPerInch -density "$dpi" "$out"
}

for fx in march_c ode_d; do
  pdf="$HERE/$fx.pdf"
  for r in 600 300 200 150 120 100 75; do
    raster "$pdf" $r "$T/$fx-$r"
    cp "$T/$fx-$r-1.png" "$D/${fx}__r$r.png"
  done
  b="$T/$fx-300-1.png"
  for a in 0.5 1.0 1.5; do
    magick "$b" -background white -rotate $a "$D/${fx}__r300_rot$a.png"
  done
  for n in 0.6 2 4; do
    magick "$b" -colorspace Gray -attenuate $n +noise Gaussian "$D/${fx}__r300_noise$n.png"
  done
  for q in 50 20 10; do
    magick "$b" -colorspace Gray -quality $q "$D/${fx}__r300_jpeg$q.jpg"
  done
  light "$b" "$D/${fx}__r300_light70.png" gray70
  light "$b" "$D/${fx}__r300_light40.png" gray40
  magick "$b" -blur 0x1.5 "$D/${fx}__r300_blur1.5.png"
  # "phone photo": rotation + uneven light + noise + slight blur + JPEG
  for r in 300 200 150; do
    s="$T/$fx-$r-1.png"
    light "$s" "$T/l.png" gray60
    magick "$T/l.png" -background white -rotate 1.0 -attenuate 2 +noise Gaussian \
      -blur 0x0.7 -colorspace Gray -quality 60 "$D/${fx}__r${r}_photo.jpg"
    # image-only PDFs of the plain raster and of the photo
    topdf $r "$D/${fx}__r${r}_imgpdf.pdf" "$s"
    topdf $r "$D/${fx}__r${r}_photo_imgpdf.pdf" "$D/${fx}__r${r}_photo.jpg"
  done
  topdf 100 "$D/${fx}__r100_imgpdf.pdf" "$T/$fx-100-1.png"
done

# Multi-page: two-page fixture as image-only PDF and multi-page TIFF
fx=twopage_f; pdf="$HERE/$fx.pdf"
for r in 300 150; do
  raster "$pdf" $r "$T/$fx-$r"
  topdf $r "$D/${fx}__r${r}_imgpdf.pdf" "$T/$fx-$r-1.png" "$T/$fx-$r-2.png"
  magick "$T/$fx-$r-1.png" "$T/$fx-$r-2.png" -compress lzw "$D/${fx}__r${r}_multipage.tif"
done
# page 1 clean at 300 dpi, page 2 a degraded 75-dpi raster upscaled to the same page size
raster "$pdf" 75 "$T/$fx-75"
magick "$T/$fx-75-2.png" -resize 400% "$T/p2bad.png"
topdf 300 "$D/${fx}__mixed_p2lowres_imgpdf.pdf" "$T/$fx-300-1.png" "$T/p2bad.png"
# page 1 music, page 2 blank
magick -size 2550x3300 xc:white "$T/blank.png"
topdf 300 "$D/${fx}__p2blank_imgpdf.pdf" "$T/$fx-300-1.png" "$T/blank.png"

# ---- extra variants (added after the first round; skipped if already present) ----
need() { [ ! -e "$1" ]; }
for fx in march_c ode_d; do
  pdf="$HERE/$fx.pdf"
  for r in 250 175; do
    need "$D/${fx}__r$r.png" && { raster "$pdf" $r "$T/$fx-$r"; cp "$T/$fx-$r-1.png" "$D/${fx}__r$r.png"; }
  done
  for r in 300 200 150; do
    [ -e "$T/$fx-$r-1.png" ] || raster "$pdf" $r "$T/$fx-$r"
    s="$T/$fx-$r-1.png"
    # flatbed "scan": slight skew + mild noise + JPEG 75
    need "$D/${fx}__r${r}_scan.jpg" && magick "$s" -background white -rotate 0.5 -attenuate 0.6 \
      +noise Gaussian -colorspace Gray -quality 75 "$D/${fx}__r${r}_scan.jpg"
    # milder "phone photo": skew + uneven light + mild noise + JPEG 60 (no blur)
    if need "$D/${fx}__r${r}_photomild.jpg"; then
      light "$s" "$T/l.png" gray60
      magick "$T/l.png" -background white -rotate 1.0 -attenuate 0.6 +noise Gaussian \
        -colorspace Gray -quality 60 "$D/${fx}__r${r}_photomild.jpg"
    fi
  done
done
# pixel-format / container variants of march_c at 300 dpi
fx=march_c; b="$D/${fx}__r300.png"
need "$D/${fx}__fmt_rgba.png"   && magick "$b" -colorspace sRGB -alpha set -channel A -evaluate set 100% +channel -define png:color-type=6 "$D/${fx}__fmt_rgba.png"
need "$D/${fx}__fmt_gray16.png" && magick "$b" -colorspace Gray -depth 16 -define png:bit-depth=16 "$D/${fx}__fmt_gray16.png"
need "$D/${fx}__fmt_bilevel.tif" && magick "$b" -colorspace Gray -threshold 50% -type Bilevel -compress Group4 "$D/${fx}__fmt_bilevel.tif"
need "$D/${fx}__fmt_cmyk.jpg"   && magick "$b" -colorspace CMYK -quality 90 "$D/${fx}__fmt_cmyk.jpg"
need "$D/${fx}__fmt_gif.gif"    && magick "$b" -colorspace Gray "$D/${fx}__fmt_gif.gif"
need "$D/${fx}__fmt_bmp.bmp"    && magick "$b" -colorspace Gray "$D/${fx}__fmt_bmp.bmp"
need "$D/${fx}__fmt_upper.JPG"  && magick "$b" -colorspace Gray -quality 90 "$D/${fx}__fmt_upper.JPG"
need "$D/${fx}__fmt_webp.webp"  && magick "$b" -colorspace Gray -quality 90 "$D/${fx}__fmt_webp.webp"
need "$D/${fx}__r600_imgpdf.pdf" && topdf 600 "$D/${fx}__r600_imgpdf.pdf" "$D/${fx}__r600.png"
ls -la "$D" | awk '{s+=$5} END {print "total bytes", s}'
