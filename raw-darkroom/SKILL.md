---
name: raw-darkroom
description: "解码、分析并显影相机 RAW 文件，读取传感器与拍摄元数据，检查各通道高光裁切、曝光余量和暗部可恢复性，生成中性基准、高光保护、暗部展开、柔和底片四套候选，并输出 16-bit TIFF、JPEG、分析地图、参数 JSON、接触表和 HTML 校验报告。用于用户上传 DNG、CR2/CR3、NEF、ARW、RW2、RAF、ORF、PEF 等相机 RAW，提出 RAW 显影、曝光诊断、高光抢救、暗部恢复、数字底片、接触表或显影方案比较时；具体格式支持取决于本地 LibRaw 版本。"
---

# 数字底片实验室 RAW Darkroom

从 CFA/Bayer 传感器数据开始分析，不把普通 JPEG 当作 RAW。区分直接测量与估算：黑白电平、通道分位数和裁切比例来自 RAW；推荐 EV、暗部信号风险和高光重建效果属于有依据的估算。

## 工作流

1. 保留源 RAW，不覆盖或修改原文件。
2. 运行能力检测：

```bash
python scripts/capability_check.py
```

缺少 `rawpy`、NumPy、Pillow 或 tifffile 时停止。没有命令执行或文件写入能力时，只能解释显影思路，不要声称已经完成传感器测量和文件输出。

3. 使用默认相机白平衡运行：

```bash
python scripts/raw_darkroom.py INPUT_RAW --output-dir OUTPUT
```

相机白平衡无效时脚本自动回退到 auto。需要比较时使用：

```bash
python scripts/raw_darkroom.py INPUT_RAW --output-dir OUTPUT --white-balance daylight
python scripts/raw_darkroom.py INPUT_RAW --output-dir OUTPUT --white-balance auto --half-size
```

`--half-size` 只用于快速候选；正式交付默认全分辨率。不要用它冒充完整分辨率显影。

输出目录默认必须为空。仅在确认其中全是旧版 RAW Darkroom 结果时使用 `--overwrite`。默认隐藏绝对路径、GPS、序列号和所有者字段；只有用户明确要求保留时才使用 `--include-private-metadata`。

4. 读取 `validation.json`。只有 `status` 为 `pass`、四套 TIFF 均为 RGB uint16、尺寸一致、TIFF/JPEG 均带 sRGB ICC 且报告引用完整时，才报告完成。
5. 首选交付 `report.html` 和 `contact-sheet.jpg`，同时列出四套 TIFF/JPEG、分析地图和 `development-parameters.json`。

## 四套候选

- `01-neutral`：中性基准。使用建议基础曝光和适中反差。
- `02-highlight-safe`：高光保护。降低曝光并加强高光保留。
- `03-shadow-open`：暗部展开。提高曝光、抬升暗部并降低饱和度。
- `04-soft-film`：柔和底片。轻微暖调、较低反差和高光去饱和。

候选参数来自 [assets/development-recipes.json](assets/development-recipes.json)。修改配方后必须重新生成，不要只改 JSON 而保留旧图片。

## 解释分析

读取 [references/sensor-analysis.md](references/sensor-analysis.md) 解释：

- 通道黑白电平和 P99/P99.9
- `near_clip_fraction` 与 `clipped_fraction`
- 局部单通道和多通道接近饱和
- 绿色 CFA 暗部信号比例、双绿色变化代理指标和建议 EV
- 何时只能给中等置信度

不要把“有单通道信息”写成“高光一定能救回”。不要从单张 RAW 声称测得真实动态范围或精确信噪比。

## 输出

- `metadata.json`：文件、解码器、尺寸、拍摄、镜头、白平衡和传感器信息。
- `analysis.json`：直接测量、评估、建议 EV、置信度和限制。
- `development-parameters.json`：四套显影的完整复现参数。
- `sensor-preview.jpg`：用于定位分析区域的基础预览。
- `highlight-map.png`：橙色表示局部通道接近饱和，红色表示多通道风险。
- `shadow-map.png`：基于绿色 CFA 块信号；青色表示可尝试展开，紫色表示贴近黑电平。
- `01–04 .tiff/.jpg`：四套候选。TIFF 为 16-bit、显示参考的 sRGB 传递编码，不是场景线性母版；TIFF/JPEG 均嵌入 sRGB ICC。
- `contact-sheet.jpg`：2×2 候选比较。
- `report.html`：可阅读报告。
- `validation.json`：格式与文件完整性检查。

读取 [references/output-and-limitations.md](references/output-and-limitations.md) 处理格式支持、ExifTool、色彩管理和交付限制。

## 质量规则

- 对真实制作保留 RAW 原件和 `development-parameters.json`。
- 白平衡、曝光和高光保护先在 RAW 阶段完成，再考虑 LUT。
- 大面积多通道裁切时明确说明数据已经丢失，不用“AI 修复”替代测量结论。
- 暗部展开版用于观察潜力，不代表噪声已经处理干净。
- 竖拍方向必须同时应用于候选和分析 mask；接触表必须完整容纳画面，不能裁掉主体。
- 默认交付脱敏元数据；不要在公开分享包中泄露 GPS、机身序列号或电脑绝对路径。
- 相机厂商软件、Adobe Camera Raw 和 Capture One 的结果可能不同；本 Skill 使用当前 LibRaw 的解码与色彩路径。
- 非 RAW 输入、损坏文件或当前 LibRaw 不支持的相机必须明确报错，不生成伪结果。
