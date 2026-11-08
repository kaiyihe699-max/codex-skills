# Codex Skills

Reusable Codex skills for visual creation, imaging, color workflows, and creative tools.

面向视觉创作、影像处理和创意工作流的 Codex Skills 集合。每个目录包含独立的 `SKILL.md` 和所需资源。

## Start here

- [product-color-match](product-color-match/) — align product colors to an approved reference, including RAW input and 16-bit TIFF output.
- [generate-lut](generate-lut/) — create and validate creative or technical LUTs, with Panasonic S9 camera support. See the [camera compatibility guide](generate-lut/references/camera-compatibility.md) and [S9 profile](generate-lut/references/camera-profiles/panasonic-dc-s9.json).
- [film-scan-lab](film-scan-lab/) — analyze, invert, restore, and archive film scans.
- [raw-darkroom](raw-darkroom/) — inspect and develop camera RAW files.
- [dream-sequence](dream-sequence/) — build connected visual stories from text or reference images.
- [optical-polish](optical-polish/) — measure and correct optical alignment in graphics and UI.

## Included skills

| Skill | Purpose |
| --- | --- |
| [dream-sequence](dream-sequence/) | Turn stories, keywords, or references into connected dreamlike visual sequences. |
| [film-scan-lab](film-scan-lab/) | Analyze, invert, restore, and archive film scans or camera reproductions. |
| [generate-lut](generate-lut/) | Generate, adjust, preview, batch-apply, and package creative or technical LUTs. |
| [hatch-pet](hatch-pet/) | Create and validate Codex-compatible v2 animated pets. |
| [humanizer-zh](humanizer-zh/) | Remove common AI-writing patterns from Chinese text. |
| [meeting-notes](meeting-notes/) | Convert raw meeting material into structured notes and action items. |
| [optical-polish](optical-polish/) | Measure and correct optical alignment in UI components and graphics. |
| [product-color-match](product-color-match/) | Match product photo colors to an approved reference with localized masks and RAW support. |
| [photo-roast-critique](photo-roast-critique/) | Produce sharp Chinese photography critiques with practical fixes. |
| [raw-darkroom](raw-darkroom/) | Decode, analyze, and develop camera RAW files. |
| [simulation-sandbox](simulation-sandbox/) | Build runnable toy simulations from rule systems or domain ideas. |
| [synesthesia-mapper](synesthesia-mapper/) | Map images deterministically into music and visual scores. |

## Related project

[Classical Page Lab](https://github.com/kaiyihe699-max/classical-page-lab) turns modern Chinese text into a configurable classical-book-style page and exports PNG, SVG, and interactive HTML.

## Installation

Copy the desired skill folder into your Codex skills directory. Each skill contains its own `SKILL.md` and any scripts, references, tests, or assets it needs.

The built-in `.system` skills and generated Python cache files are intentionally excluded from this repository.

## License

Licensing currently varies by skill. Review the license files in individual folders before redistributing or changing this repository to public visibility.


