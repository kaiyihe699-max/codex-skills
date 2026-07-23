---
name: generate-lut
description: Generate, preview, validate, and package creative and technical color grading LUTs, especially .cube 3D LUT files. Use when Codex needs to create creative looks from text prompts, convert between RGB color spaces and transfer functions, build camera Log or V-Log oriented LUTs, work with ACES/Display P3/Rec.709/Rec.2020/PQ/HLG concepts, bake LUTs through OpenColorIO when a config is supplied, apply LUT previews to images, validate LUT quality, or explain practical LUT usage.
---

# Generate LUT

## Overview

Create practical `.cube` 3D LUTs with deterministic scripts, preview them on images or generated test charts, and validate the generated files before delivery. Support both creative display-referred looks and technical color transforms.

## Workflow

1. Classify the request as creative look, technical transform, OCIO pipeline bake, preview/application, or validation.
2. For creative looks, use `scripts/generate_cube_lut.py` and default to `LUT_3D_SIZE 33`, `strength 0.65`, and `.cube` output unless the user asks otherwise.
3. For built-in technical transforms, use `scripts/generate_technical_lut.py` after confirming source/target primaries and transfer functions.
4. For show pipelines, camera transforms beyond the built-ins, ACES config workflows, or facility-managed color, use `scripts/ocio_generate_lut.py` with a user-supplied OCIO config.
5. For previews, use the user's sample image when provided; otherwise run `scripts/create_test_assets.py` if needed, then use `assets/neutral-ramp.png` and `assets/color-checker.png`.
6. Always run `scripts/validate_lut.py` on generated or edited `.cube` files before handing them over.
7. Deliver the `.cube` file, at least one preview image when possible, and a short note about intended use, source/target assumptions, and limits.

## Script Guide

Generate a creative LUT:

```bash
python scripts/generate_cube_lut.py --look cinematic-teal-orange --strength 0.65 --size 33 --output output.cube
```

Generate a technical LUT with built-in color science:

```bash
python scripts/generate_technical_lut.py --source-space panasonic-v-gamut --source-transfer panasonic-vlog --target-space rec709 --target-transfer rec709 --tone-map luminance-reinhard --size 33 --output vlog-to-rec709.cube
```

Bake a LUT through OpenColorIO when a config is supplied:

```bash
python scripts/ocio_generate_lut.py --config config.ocio --source "Input - Panasonic V-Log" --target "Rec.709 Display" --size 33 --output ocio-baked.cube
```

Inspect available built-in and optional capabilities:

```bash
python scripts/color_science_status.py
```

Apply a LUT preview:

```bash
python scripts/apply_lut_preview.py --input sample.png --lut output.cube --output preview.png --mode comparison
```

Create fallback preview charts:

```bash
python scripts/create_test_assets.py --output-dir assets
```

Validate a LUT:

```bash
python scripts/validate_lut.py output.cube
```

Use `--look neutral` for test identity LUTs. Use `--look custom` with numeric controls such as `--contrast`, `--saturation`, `--warmth`, `--exposure`, and `--lift` when the user describes a look that does not fit a preset.

## References

Read `references/lut-format.md` when changing parser/writer behavior or explaining `.cube` compatibility.

Read `references/looks.md` when selecting or tuning a creative preset from natural-language style requests.

Read `references/color-workflow.md` when distinguishing creative looks from technical transforms or planning previews.

Read `references/technical-color-science.md` when generating technical conversion LUTs, choosing primaries/transfer functions, or deciding whether an OCIO config is required.

## Guardrails

Do not present creative LUTs as mathematically accurate camera, scene-linear, ACES, ICC, or display transforms. For technical transforms, name the source RGB primaries, source transfer, target RGB primaries, target transfer, tone map, LUT size, and whether output was clipped.

Do not claim a built-in camera conversion is an official vendor LUT. For exact camera/vendor/show looks, ask for the official LUT or an OCIO config and bake through `scripts/ocio_generate_lut.py`.

Keep neutral grey reasonably stable unless the user explicitly asks for a tinted look. Warn when a strong style intentionally shifts greys, clips saturated colors, or may behave differently across editors.

Prefer `.cube` for broad compatibility. Avoid exporting many sizes unless the user needs them; `33x33x33` is a good default balance, `17` is light and coarse, and `65` is larger and smoother.
