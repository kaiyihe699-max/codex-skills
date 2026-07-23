#!/usr/bin/env python3
"""Build deterministic Dream Sequence storyboards and continuity reports."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PALETTE = {
    "dominant": ["#0D1B2A", "#68727B"],
    "accent": ["#C94335"],
    "highlight": ["#E9D8A6"],
}

DEFAULT_PHASES = [
    ("建立梦境", "atmosphere", "先建立空间和天气，只露出主体的局部线索。"),
    ("引入主体", "encounter", "让主体和核心符号第一次形成清晰关系。"),
    ("跟随线索", "echo", "让主体沿着符号或光线移动，延续上一幕的方向。"),
    ("穿过边界", "threshold", "进入一个不完全遵循现实的空间，保留前一幕的轮廓或颜色。"),
    ("发生变形", "metamorphosis", "让核心符号发生可解释的形态变化，主体开始被空间吞没或反向观察。"),
    ("余波收束", "afterimage", "留下一个与开场呼应的画面，完成从具体物体到抽象意象的收束。"),
    ("再次回返", "return", "让前面的符号以更轻、更远或更抽象的形式回到画面。"),
    ("消隐", "fade", "保留最后一个视觉回声，让故事停在未解释完的状态。"),
]

CAMERA_LANGUAGES = [
    "远景建立空间，主体靠近画面边缘",
    "中远景跟随主体，保留大量负空间",
    "低机位侧向构图，主体向右移动",
    "对称或近似对称构图，制造边界感",
    "近景压缩细节，核心符号占据视觉重心",
    "固定机位留白，像一张梦醒后的残影",
    "高机位俯视，让主体显得很小",
    "画面逐渐失焦，只保留轮廓和颜色",
]

STOPWORDS = {
    "然后", "但是", "因为", "所以", "一个", "一些", "这个", "那个", "开始",
    "最后", "逐渐", "进入", "出现", "发现", "走进", "看见", "里面", "外面",
    "没有", "变成", "像是", "关于", "场景", "画面", "以及", "同时", "可以",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def split_story(text: str) -> list[str]:
    chunks = re.split(r"[。！？!?；;\n]+", text)
    return [re.sub(r"\s+", " ", chunk).strip() for chunk in chunks if chunk.strip()]


def extract_terms(text: str, limit: int = 8) -> list[str]:
    terms: list[str] = []
    for candidate in re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z][A-Za-z0-9_-]{2,}", text):
        if candidate in STOPWORDS or candidate in terms:
            continue
        terms.append(candidate)
        if len(terms) >= limit:
            break
    return terms


def infer_motif(text: str, supplied: str | None) -> str:
    if supplied:
        return supplied.strip()
    patterns = [
        r"[^，。！？\s]{1,10}(?:气球|雨伞|月亮|灯|花|门|影子|海|鱼|鸟|眼睛|镜子|纸片)",
        r"(?:红色|蓝色|白色|黑色|金色|发光)[^，。！？\s]{1,8}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return "一个圆形发光物"


def parse_ratio(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", value.strip())
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        raise ValueError(f"画幅必须是类似 4:5 的格式，收到 {value!r}")
    return int(match.group(1)), int(match.group(2))


def parse_palette(value: str | None) -> dict[str, list[str]]:
    if not value:
        return DEFAULT_PALETTE
    colors = [item.strip() for item in value.split(",") if item.strip()]
    if not colors:
        return DEFAULT_PALETTE
    return {
        "dominant": colors[:2],
        "accent": colors[2:3] or [colors[-1]],
        "highlight": colors[3:4] or ["#E9D8A6"],
    }


def build_visual_bible(
    title: str,
    story: str,
    scene_count: int,
    ratio: str,
    style: str,
    motif: str,
    character: str,
    palette: dict[str, list[str]],
) -> dict[str, Any]:
    states = [
        f"真实的{motif}",
        f"水面或玻璃中的{motif}倒影",
        f"空间里与{motif}相似的轮廓或光斑",
        f"漂浮的抽象{motif}光源",
        f"与天空、月亮或远处光体重合的{motif}",
        f"只留下{motif}的颜色和圆形回声",
    ]
    return {
        "schema_version": "1.0",
        "title": title,
        "created_utc": now_iso(),
        "story_excerpt": story[:240],
        "scene_count": scene_count,
        "aspect_ratio": ratio,
        "style": style,
        "palette": palette,
        "camera_language": ["低机位", "长焦压缩", "轻微失焦", "大量负空间"],
        "lighting": "阴天散射光，主体边缘有很弱的逆光，转场处逐渐提高符号光源亮度",
        "texture": "湿润胶片颗粒、细微扫描线、局部玻璃反射",
        "character_anchor": character,
        "motif": {
            "name": motif,
            "base_form": f"明确可识别的{motif}",
            "states": states[: max(3, min(scene_count, len(states)))],
        },
        "negative_constraints": [
            "不改变主体的服装主色、轮廓和动作方向",
            "不加入无关文字、logo或额外的高饱和主色",
            "不在相邻场景之间突然切换成完全不同的摄影或插画媒介",
        ],
    }


def build_scenes(
    story: str,
    scene_count: int,
    ratio: str,
    style: str,
    motif: str,
    character: str,
    palette: dict[str, list[str]],
) -> list[dict[str, Any]]:
    sentences = split_story(story)
    terms = extract_terms(story)
    scenes: list[dict[str, Any]] = []
    palette_text = ", ".join(palette["dominant"] + palette["accent"] + palette["highlight"])
    for index in range(scene_count):
        phase, phase_key, phase_note = DEFAULT_PHASES[index % len(DEFAULT_PHASES)]
        source_sentence = sentences[min(index, len(sentences) - 1)] if sentences else story.strip()
        camera = CAMERA_LANGUAGES[index % len(CAMERA_LANGUAGES)]
        motif_state_index = min(index, 5)
        motif_state = [
            f"真实的{motif}",
            f"{motif}在雨水或玻璃中的倒影",
            f"与{motif}相似的圆形轮廓或光斑",
            f"悬浮的抽象{motif}光源",
            f"{motif}与远处天体或城市灯光重合",
            f"只留下{motif}的颜色和圆形回声",
        ][motif_state_index]
        transition = (
            "从上一幕的颜色或轮廓延续进入"
            if index == 0
            else f"将上一幕的{motif}状态转化为本幕的{motif_state}"
        )
        visual_echo = (
            f"保留{motif}的圆形轮廓和{palette['accent'][0]}色"
            if index < 3
            else f"保留{motif}的颜色，把具体物体逐渐减少为光和负空间"
        )
        prompt = (
            f"{character}，{source_sentence or phase_note}。"
            f"这是连续梦境的第 {index + 1} 幕：{phase}。"
            f"{camera}，画幅 {ratio}。"
            f"视觉风格：{style}；色板：{palette_text}。"
            f"核心符号状态：{motif_state}。{visual_echo}。"
            f"与前一幕的转场：{transition}。"
            "保持主体锚点、镜头语言和材质统一，不加入文字或logo。"
        )
        scenes.append({
            "id": f"scene-{index + 1:02d}",
            "index": index + 1,
            "title": phase,
            "narrative_role": phase_key,
            "source_sentence": source_sentence,
            "subject": character,
            "action": phase_note,
            "camera": camera,
            "motif_state": motif_state,
            "visual_echo": visual_echo,
            "transition": transition,
            "keywords": terms[:6],
            "prompt": prompt,
            "negative_prompt": "人物外观漂移、无关文字、logo、额外高饱和主色、完全不同的媒介质感、随机新主体",
            "expected_image": f"scenes/scene-{index + 1:02d}.png",
        })
    return scenes


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_plan(args: argparse.Namespace) -> Path:
    story_path = Path(args.story_file)
    story = story_path.read_text(encoding="utf-8-sig").strip()
    if not story:
        raise ValueError("故事文件为空")
    if not 3 <= args.scene_count <= 8:
        raise ValueError("scene-count 必须在 3 到 8 之间")
    parse_ratio(args.ratio)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "prompts").mkdir(exist_ok=True)
    (output_dir / "scenes").mkdir(exist_ok=True)
    palette = parse_palette(args.palette)
    motif = infer_motif(story, args.motif)
    character = args.character.strip() if args.character else "一个穿深色外套的人物背影，短发，步伐缓慢"
    title = args.title.strip() if args.title else (split_story(story)[0][:24] or "未命名梦境")
    bible = build_visual_bible(
        title, story, args.scene_count, args.ratio, args.style, motif, character, palette
    )
    scenes = build_scenes(
        story, args.scene_count, args.ratio, args.style, motif, character, palette
    )
    manifest = {
        "schema_version": "1.0",
        "project_type": "dream-sequence",
        "title": title,
        "created_utc": now_iso(),
        "source_story_file": str(story_path),
        "story": story,
        "visual_bible": "visual-bible.json",
        "scenes": "scenes.json",
        "scene_count": len(scenes),
        "aspect_ratio": args.ratio,
        "style": args.style,
        "motif": motif,
        "character": character,
        "image_status": {"present": [], "missing": [scene["expected_image"] for scene in scenes]},
        "deliverables": [
            "visual-bible.json",
            "visual-bible.md",
            "scenes.json",
            "storyboard.md",
            "prompts/scene-XX.txt",
            "continuity-report.json",
            "continuity-report.html",
            "animation-plan.json",
        ],
    }
    write_json(output_dir / "visual-bible.json", bible)
    write_json(output_dir / "scenes.json", scenes)
    write_json(output_dir / "manifest.json", manifest)
    (output_dir / "visual-bible.md").write_text(render_bible_markdown(bible), encoding="utf-8")
    (output_dir / "storyboard.md").write_text(render_storyboard_markdown(manifest, scenes), encoding="utf-8")
    for scene in scenes:
        prompt_path = output_dir / "prompts" / f"{scene['id']}.txt"
        prompt_path.write_text(
            scene["prompt"] + "\n\n负面约束：" + scene["negative_prompt"] + "\n",
            encoding="utf-8",
        )
    write_json(output_dir / "animation-plan.json", build_animation_plan(scenes, args.ratio))
    report = build_report(manifest, bible, scenes, output_dir)
    write_json(output_dir / "continuity-report.json", report)
    (output_dir / "continuity-report.html").write_text(render_report_html(report), encoding="utf-8")
    return output_dir


def render_bible_markdown(bible: dict[str, Any]) -> str:
    palette = bible["palette"]
    motif = bible["motif"]
    lines = [
        f"# {bible['title']} Visual Bible",
        "",
        f"- 画幅：{bible['aspect_ratio']}",
        f"- 场景数：{bible['scene_count']}",
        f"- 风格：{bible['style']}",
        f"- 主体锚点：{bible['character_anchor']}",
        "",
        "## 色板",
        "",
        f"- 主色：{', '.join(palette['dominant'])}",
        f"- 符号色：{', '.join(palette['accent'])}",
        f"- 高光：{', '.join(palette['highlight'])}",
        "",
        "## 镜头与材质",
        "",
        f"- 镜头：{'、'.join(bible['camera_language'])}",
        f"- 光线：{bible['lighting']}",
        f"- 材质：{bible['texture']}",
        "",
        f"## 符号变形链：{motif['name']}",
        "",
    ]
    lines.extend(f"{index}. {state}" for index, state in enumerate(motif["states"], 1))
    lines.extend(["", "## 禁止漂移", ""])
    lines.extend(f"- {item}" for item in bible["negative_constraints"])
    return "\n".join(lines) + "\n"


def render_storyboard_markdown(manifest: dict[str, Any], scenes: list[dict[str, Any]]) -> str:
    lines = [
        f"# {manifest['title']} Storyboard",
        "",
        f"> {manifest['story']}",
        "",
        f"画幅：{manifest['aspect_ratio']}  |  场景：{manifest['scene_count']} 幕  |  符号：{manifest['motif']}",
        "",
    ]
    for scene in scenes:
        lines.extend([
            f"## {scene['index']:02d}. {scene['title']}",
            "",
            f"- 叙事作用：{scene['narrative_role']}",
            f"- 画面动作：{scene['action']}",
            f"- 镜头：{scene['camera']}",
            f"- 符号状态：{scene['motif_state']}",
            f"- 视觉呼应：{scene['visual_echo']}",
            f"- 转场：{scene['transition']}",
            f"- 画面文件：`{scene['expected_image']}`",
            "",
            f"> {scene['prompt']}",
            "",
        ])
    return "\n".join(lines)


def build_animation_plan(scenes: list[dict[str, Any]], ratio: str) -> dict[str, Any]:
    duration = max(6, len(scenes) * 2)
    return {
        "schema_version": "1.0",
        "aspect_ratio": ratio,
        "duration_seconds": duration,
        "fps": 12,
        "mode": "keyframe-draft",
        "scenes": [
            {
                "id": scene["id"],
                "start_second": index * 2,
                "end_second": (index + 1) * 2,
                "motion": "轻微推近、颗粒漂移、符号亮度缓慢变化",
                "transition": "叠化 0.6 秒，沿用上一幕的轮廓或颜色",
            }
            for index, scene in enumerate(scenes)
        ],
        "note": "这是关键帧动画草案。正式成片前应在剪辑或动效软件中继续处理节奏、声音和局部变形。",
    }


def find_scene_images(output_dir: Path, scenes: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for scene in scenes:
        relative = Path(scene["expected_image"])
        if (output_dir / relative).exists():
            present.append(str(relative).replace("\\", "/"))
        else:
            missing.append(str(relative).replace("\\", "/"))
    return present, missing


def build_report(
    manifest: dict[str, Any], bible: dict[str, Any], scenes: list[dict[str, Any]], output_dir: Path
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    expected = list(range(1, len(scenes) + 1))
    actual = [scene.get("index") for scene in scenes]
    if actual != expected:
        errors.append(f"场景编号不连续：{actual}")
    if len({scene.get("id") for scene in scenes}) != len(scenes):
        errors.append("场景 id 存在重复")
    for scene in scenes:
        for field in ("subject", "motif_state", "prompt", "transition"):
            if not scene.get(field):
                errors.append(f"{scene.get('id', 'unknown')} 缺少 {field}")
    if not bible.get("palette") or not bible.get("motif"):
        errors.append("视觉圣经缺少色板或核心符号")
    states = [scene.get("motif_state") for scene in scenes]
    if len(set(states)) < min(3, len(scenes)):
        warnings.append("核心符号变化状态较少，场景可能缺少视觉演变")
    if scenes and not scenes[0].get("visual_echo"):
        warnings.append("第一幕没有定义视觉锚点")
    present, missing = find_scene_images(output_dir, scenes)
    if missing:
        notes.append(f"尚未找到 {len(missing)} 张场景图片；当前结果仍是叙事计划阶段")
    if present and len(present) != len(scenes):
        warnings.append("部分场景图片已存在，但数量与计划不一致")
    status = "error" if errors else ("warning" if warnings else "pass")
    return {
        "schema_version": "1.0",
        "status": status,
        "checked_utc": now_iso(),
        "project": manifest.get("title"),
        "scene_count": len(scenes),
        "image_status": {"present": present, "missing": missing},
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
        "checks": {
            "scene_order": not any("场景编号" in item for item in errors),
            "unique_ids": not any("场景 id" in item for item in errors),
            "required_fields": not any("缺少" in item for item in errors),
            "visual_bible": not any("视觉圣经" in item for item in errors),
            "motif_evolution": len(set(states)) >= min(3, len(scenes)),
        },
    }


def render_report_html(report: dict[str, Any]) -> str:
    status = html.escape(report["status"])
    sections = []
    for label, items in (("Errors", report["errors"]), ("Warnings", report["warnings"]), ("Notes", report["notes"])):
        if items:
            body = "".join(f"<li>{html.escape(item)}</li>" for item in items)
            sections.append(f"<section><h2>{label}</h2><ul>{body}</ul></section>")
    checks = "".join(
        f"<li class={'pass' if value else 'fail'}>{html.escape(key)}: {'pass' if value else 'fail'}</li>"
        for key, value in report["checks"].items()
    )
    return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Dream Sequence Continuity Report</title>
<style>body{{font-family:system-ui,-apple-system,'Microsoft YaHei',sans-serif;max-width:900px;margin:40px auto;padding:0 24px;background:#101217;color:#edf1f5;line-height:1.65}}section{{border-top:1px solid #303642;padding:16px 0}}.status{{display:inline-block;padding:4px 10px;border:1px solid #6b7280;border-radius:999px}}.pass{{color:#8ee6ad}}.fail{{color:#ff9b9b}}code{{color:#f3c883}}</style></head>
<body><h1>Dream Sequence 连续性报告</h1><p>状态：<span class=\"status\">{status}</span></p>
<p>项目：{html.escape(str(report.get('project', '')))}，场景：{report['scene_count']} 幕。</p>
<section><h2>Checks</h2><ul>{checks}</ul></section>
{''.join(sections)}
</body></html>"""


def command_validate(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bible_path = manifest_path.parent / manifest.get("visual_bible", "visual-bible.json")
    scenes_path = manifest_path.parent / manifest.get("scenes", "scenes.json")
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
    report = build_report(manifest, bible, scenes, manifest_path.parent)
    output_path = Path(args.output) if args.output else manifest_path.parent / "continuity-report.json"
    write_json(output_path, report)
    (output_path.parent / "continuity-report.html").write_text(render_report_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "error" else 0


def command_contact_sheet(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    files = sorted(
        [path for path in input_dir.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}],
        key=lambda path: path.name,
    )
    if not files:
        raise ValueError(f"没有在 {input_dir} 找到 PNG/JPG/WebP 场景图片")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("contact-sheet 需要 Pillow，请先安装 requirements.txt") from exc
    columns = min(3, len(files))
    cell_width = 360
    cell_height = 300
    label_height = 38
    rows = (len(files) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#11151c")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, path in enumerate(files):
        image = Image.open(path).convert("RGB")
        image.thumbnail((cell_width - 24, cell_height - label_height - 24))
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        left = x + (cell_width - image.width) // 2
        top = y + label_height + (cell_height - label_height - image.height) // 2
        sheet.paste(image, (left, top))
        draw.text((x + 12, y + 12), path.stem, fill="#edf1f5", font=font)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    print(f"contact sheet written: {output}")
    return 0


def command_animate(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    output = Path(args.output)
    images = sorted(input_dir.glob("scene-*.png"))
    if not images:
        raise ValueError("animate 需要 scenes/scene-01.png 这类连续编号的 PNG 图片")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        plan = {
            "status": "planned",
            "reason": "本机未找到 ffmpeg",
            "input_images": [str(path) for path in images],
            "suggested_command": "ffmpeg -framerate 12 -i scene-%02d.png -vf format=yuv420p dream-sequence.mp4",
        }
        plan_path = output.with_suffix(".json")
        write_json(plan_path, plan)
        print(f"ffmpeg unavailable; animation plan written: {plan_path}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-framerate",
        str(args.fps),
        "-i",
        str(input_dir / "scene-%02d.png"),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    print(f"animation written: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dream Sequence storyboard and continuity toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="build a visual bible and storyboard plan")
    plan.add_argument("--story-file", required=True)
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--scene-count", type=int, default=6)
    plan.add_argument("--ratio", default="4:5")
    plan.add_argument("--style", default="梦境胶片、低饱和冷色、单一高饱和符号色")
    plan.add_argument("--motif")
    plan.add_argument("--character")
    plan.add_argument("--palette", help="comma-separated hex colors")
    plan.add_argument("--title")
    plan.set_defaults(func=write_plan)

    validate = subparsers.add_parser("validate", help="validate a generated manifest")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--output")
    validate.set_defaults(func=command_validate)

    contact = subparsers.add_parser("contact-sheet", help="make a contact sheet from scene images")
    contact.add_argument("--input-dir", required=True)
    contact.add_argument("--output", required=True)
    contact.set_defaults(func=command_contact_sheet)

    animate = subparsers.add_parser("animate", help="make a simple keyframe draft when ffmpeg is available")
    animate.add_argument("--input-dir", required=True)
    animate.add_argument("--output", required=True)
    animate.add_argument("--fps", type=int, default=12)
    animate.set_defaults(func=command_animate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
        if isinstance(result, Path):
            print(f"dream sequence plan written: {result}")
            return 0
        return int(result)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
