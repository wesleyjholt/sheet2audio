scans fixtures (robustness to scanned / photographed input)

Sources (original or public-domain, engraved with LilyPond 2.26):
  march_c.ly    16 bars 4/4, C major, piano (original)          -> .pdf, .midi (ground truth)
  ode_d.ly      16 bars 4/4, D major, Beethoven 1824 theme (PD) -> .pdf, .midi
  twopage_f.ly  32 bars 3/4, F major, 2 pages, 24pt staff (original) -> .pdf, .midi
  min_octave_shift_unterminated.musicxml  3-note repro: unterminated <octave-shift>
                                          segfaults Verovio renderToMIDI (exit 139)

degraded/  derived with make_degraded.sh (pdftoppm + ImageMagick 7). Naming:
  <fixture>__r<dpi>.png                plain gray raster
  __r300_rot<deg>.png                  rotation 0.5/1.0/1.5 deg
  __r300_noise<att>.png                gaussian noise (+noise Gaussian, -attenuate att)
  __r300_jpeg<q>.jpg                   JPEG quality q
  __r300_light<gray>.png               diagonal uneven lighting (multiply to grayNN)
  __r300_blur1.5.png                   gaussian blur sigma 1.5 px
  __r<dpi>_scan.jpg                    rot 0.5 + noise 0.6 + JPEG 75
  __r<dpi>_photomild.jpg               rot 1.0 + light gray60 + noise 0.6 + JPEG 60
  __r<dpi>_photo.jpg                   rot 1.0 + light gray60 + noise 2 + blur 0.7 + JPEG 60
  __r<dpi>_imgpdf.pdf / _photo_imgpdf  image-only PDF of the above
  __fmt_*                              pixel formats (RGBA, 16-bit, bilevel G4 TIFF, CMYK JPEG,
                                       GIF, .JPG, WebP)
  twopage_f__r<dpi>_imgpdf.pdf / _multipage.tif, __mixed_p2lowres_imgpdf.pdf, __p2blank_imgpdf.pdf
Large files removed to keep the repo small (regenerate with make_degraded.sh; noise is random):
  *__r300_noise2.png, *__r300_noise4.png, march_c__fmt_bmp.bmp
