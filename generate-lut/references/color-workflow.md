# Color Workflow Notes

## Creative vs Technical LUTs

Creative LUTs are display-referred looks. They adjust contrast, saturation, hue bias, and tone curves for aesthetic effect.

Technical LUTs convert between defined color spaces, transfer functions, devices, or camera encodings. Do not approximate these from style language.

Treat these as technical requests:

- Log to Rec.709, S-Log, C-Log, V-Log, LogC, REDLogFilm, BMD Film.
- ACES, OCIO, scene-linear, linear-light, IDT, ODT, RRT.
- ICC, monitor calibration, printer proofing, Display P3, Rec.2020, HDR, PQ, HLG.
- "Accurate conversion", "color managed", "match this camera", or "scientific".

For technical requests, ask for source RGB primaries, source transfer function, target RGB primaries, target transfer function, tone-mapping expectations, LUT size, and destination application/device. Use `scripts/generate_technical_lut.py` for built-in transforms or `scripts/ocio_generate_lut.py` when the user supplies an OCIO config.

## Practical Creative Guidelines

Preserve skin by keeping orange/red hue shifts modest and avoiding extreme saturation changes in midtones. Use stronger color bias in shadows and highlights than in midtones.

Protect neutral greys unless the look intentionally tints the whole image. A neutral ramp preview is the quickest way to catch accidental color casts.

Prefer smooth curves and moderate contrast. Hard channel clipping creates banding and breaks footage from other cameras.

Use intensity mixing: generate the full look, then blend it back toward identity with `strength`. This keeps presets reusable across images.

## Built-In Technical Coverage

The built-in color science scripts support common RGB primaries: sRGB/Rec.709, Display P3/P3-D65, Rec.2020/BT.2020, ACES AP0, ACES AP1/ACEScg, and Panasonic V-Gamut.

The built-in transfer functions include linear, sRGB, Rec.709, gamma 2.2, gamma 2.4, ST 2084 PQ, HLG, and Panasonic V-Log.

Use OpenColorIO for exact show pipelines, official ACES configs, camera transforms not listed above, display rendering transforms, or any transform that must match a production facility.

## Preview Guidance

Use a real user image when available. If no image is supplied, preview on:

- `assets/neutral-ramp.png` for tonal continuity and neutral drift.
- `assets/color-checker.png` for hue bias and saturation behavior.

Deliver previews as comparisons when possible so the user can judge strength quickly.
