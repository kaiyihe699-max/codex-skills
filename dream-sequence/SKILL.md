---
name: dream-sequence
description: "把文字、关键词或参考图转成连续的梦境视觉叙事。用于生成有统一视觉圣经、重复符号、物体变形、镜头呼应关系的分镜、海报组图、图像提示词、连续性报告和短动画方案；适合视觉艺术、摄影、影像、音乐视觉、展览和设计概念创作。"
---

# Dream Sequence 梦境叙事器

把故事拆成一组有视觉因果关系的场景。先建立视觉圣经和符号变形链，再生成每一幕的画面提示词、分镜结构与交付素材。不要把它当成一次性批量出图工具，重点是让场景之间保持关系。

## 工作流

### 1. 读取创作意图

确认以下信息；用户没有提供时使用默认值，不要为了填表反复追问：

- 故事或关键词
- 场景数量：默认 6 幕，范围 3-8 幕
- 画幅：默认 4:5；根据用途可选 16:9、1:1、9:16
- 视觉方向：默认梦境胶片、低饱和冷色、单一高饱和符号色
- 主体锚点：人物、动物、建筑或物件
- 重复符号：默认从故事中提取；无法提取时使用“圆形发光物”
- 交付物：默认分镜、视觉圣经、提示词、连续性报告

### 2. 先建立视觉圣经

在生成场景前，固定以下规则：

- 色板和明度层级
- 镜头语言、景别和构图重心
- 光线方向、天气和空间质感
- 主体外观与不可变特征
- 重复符号的形态、颜色和出现频率
- 材质、颗粒、失焦或印刷质感
- 禁止突然出现的元素

使用 `python scripts/dream_sequence.py plan` 生成 `visual-bible.json` 和 `visual-bible.md`。详细字段见 [visual-bible-schema.md](references/visual-bible-schema.md)。

### 3. 设计符号变形链

为一个核心符号设计 3-6 个状态。优先使用“具体物体 -> 影子或倒影 -> 空间回声 -> 光 -> 抽象形态”的变化路径。

每一幕至少保留一个前后呼应点：颜色、轮廓、方向、位置、光源、动作或构图关系。不要每一幕都重新发明主题。

### 4. 生成分镜与场景提示词

每幕输出：

- 场景标题和叙事作用
- 镜头景别、机位和主体位置
- 画面动作与情绪
- 符号状态及其变化方式
- 必须保留的视觉锚点
- 可直接交给图像生成工具的提示词
- 避免出现的元素

如果当前环境支持图像生成，按“视觉圣经 -> 主体锚点图 -> 场景 1 -> 场景 2...”的顺序生成。每一幕都带上同一份视觉圣经，并把上一幕作为连续性参考。不要一次生成整组后再假设它们天然一致。

如果当前环境不支持图像生成，不要声称已经生成图片；交付完整的场景提示词、分镜结构和素材制作说明，供用户在支持图像生成的环境中继续执行。

### 5. 检查连续性

使用 `python scripts/dream_sequence.py validate --manifest PATH` 检查：

- 场景数量和顺序
- 主体锚点是否在每幕出现
- 色板是否统一
- 重复符号是否有明确变化
- 场景转场是否写清楚
- 是否存在无理由的镜头方向跳变
- 场景图片是否缺失

详细检查规则见 [continuity-checks.md](references/continuity-checks.md)。低置信度的问题只报告，不要擅自改写用户的创作意图。

### 6. 输出素材包

优先输出以下结构：

```text
dream-sequence/
├── manifest.json
├── visual-bible.json
├── visual-bible.md
├── scenes.json
├── storyboard.md
├── prompts/
│   ├── scene-01.txt
│   └── scene-06.txt
├── continuity-report.json
├── continuity-report.html
├── contact-sheet.png
└── animation-plan.json
```

已有场景图片时，使用 `contact-sheet` 子命令生成联系表；已有连续编号的 PNG 图片并且本机有 FFmpeg 时，可使用 `animate` 生成基础关键帧动画。动画输出是后续精剪的草稿，不要把简单叠化称为完成的 AI 视频。

## 快速命令

```bash
python scripts/dream_sequence.py plan --story-file story.txt --output-dir dream-sequence
python scripts/dream_sequence.py plan --story-file story.txt --scene-count 6 --ratio 4:5 --motif "红色气球" --output-dir dream-sequence
python scripts/dream_sequence.py validate --manifest dream-sequence/manifest.json
python scripts/dream_sequence.py contact-sheet --input-dir dream-sequence/scenes --output dream-sequence/contact-sheet.png
python scripts/dream_sequence.py animate --input-dir dream-sequence/scenes --output dream-sequence/dream-sequence.mp4
```

## 交付判断

- 只有故事拆解和提示词时，称为“叙事计划”，不要称为成片。
- 只有场景图片时，检查统一性后再称为“分镜组图”。
- 有连续编号图片和动画文件时，才称为“短动画草稿”。
- 用户要求海报组图时，保持同一色板、同一符号链和同一画幅，不要把 6 张图做成 6 种风格。

