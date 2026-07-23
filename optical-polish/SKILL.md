---
name: optical-polish
description: "检测并修正 UI 组件、图标、文字、按钮和边框中数值对齐但视觉不齐的问题。用于分析 PNG/JPG/WebP 截图或 SVG 素材的光学中心、可见边界、负空间、图标视觉尺寸、文字与图标组合重心、边框视觉重量，输出测量数据、偏移建议、标注图、修正预览和 HTML 报告；也用于回答光学对齐、视觉居中、按钮重心、中英混排基线和图标大小一致性问题。"
---

# Optical Polish 视觉精修

把光学对齐当成有证据的设计判断。先用脚本测量，再结合形状语义、文字内容和设计意图解释结果。不要把所有非对称设计都判成错误。

## 工作流

1. 确认输入类型和目标组件。
   - 优先使用裁切后的单个按钮、图标或图标加文字组件。
   - 对整页截图，先识别并裁切待检查组件；不要直接对整页运行重心分析。
   - 对 SVG，优先保留原始 viewBox；渲染 SVG 需要 CairoSVG。
2. 检查执行能力。
   - 运行 `python scripts/capability_check.py`。
   - 缺少 Pillow 或 NumPy 时停止执行并说明依赖。
   - 环境没有命令执行或文件输出能力时，降级为视觉分析，只给出定性判断与建议，不声称已经完成像素测量或生成修正版。
3. 运行确定性分析。
   - PNG/JPG/WebP：运行 `python scripts/optical_polish.py INPUT --output-dir OUTPUT`。
   - 指定组件区域：增加 `--container x,y,w,h`。
   - 已知边框宽度：增加 `--border-width N`，将边框从内容重心中排除并单独测量。
   - 背景估计失败：增加 `--background "#RRGGBB"`。
4. 阅读 `analysis.json`，检查 `confidence`、`background_dominance`、`foreground_fraction` 和两种偏移估计是否一致。
5. 使用形状和排版语境复核。
   - 读取 [references/optical-alignment-rules.md](references/optical-alignment-rules.md) 处理三角形、圆形、箭头、描边图标和方向性形状。
   - 读取 [references/cjk-typography.md](references/cjk-typography.md) 处理中英混排、数字、标点和中文按钮标签。
6. 交付证据和建议。
   - 默认交付 `annotated.png`、`comparison.png`、`analysis.json` 和 `report.html`。
   - 把 `corrected.png` 称为修正预览，不称为最终设计文件。
   - 有结构化源文件时，把整数偏移转换成 CSS、SVG transform 或设计参数；先展示补丁，再按用户要求修改源文件。

## 快速命令

```bash
python scripts/capability_check.py
python scripts/optical_polish.py component.png --output-dir optical-report
python scripts/optical_polish.py screen.png --container 120,80,240,64 --border-width 1 --output-dir optical-report
python scripts/optical_polish.py icon.svg --background "#111827" --output-dir optical-report
```

## 解释结果

- `geometric_center`：容器数学中心。
- `visual_centroid`：按透明度与相对背景的视觉反差加权后的重心。
- `visible_bbox_center`：可见内容边界框中心，用于观察四周负空间。
- `centroid_correction`：让视觉重心靠近容器中心的偏移。
- `whitespace_correction`：让可见边界四周留白更均衡的偏移。
- `recommended_shift`：综合两种证据后的整数像素建议。
- `confidence`：背景稳定性、前景占比、对比度和两种估计一致性的综合置信度。

按以下规则行动：

- `high`：可作为明确修正建议，仍需查看修正预览。
- `medium`：作为 A/B 方案交付，让设计师选择。
- `low`：只报告现象，不自动修改；通常表示背景复杂、裁切不准或组件包含多个层级。
- 推荐偏移小于 1 px 时，优先在 2x/3x 渲染下验证，不强行修改 1x 素材。

## 输入和限制

读取 [references/input-and-report.md](references/input-and-report.md) 了解裁切、透明素材、复杂背景、阴影、渐变和报告字段的处理方法。

不要做以下事情：

- 不要用单一像素质心替代设计判断。
- 不要把阴影、辉光或页面背景算进图标内容。
- 不要对品牌标志、手写字或有意偏心的图形自动修正。
- 不要从普通截图声称得到了真实字体基线、图层结构或精确设计参数。
- 不要在低置信度时输出“必须移动 N px”这类确定性结论。

