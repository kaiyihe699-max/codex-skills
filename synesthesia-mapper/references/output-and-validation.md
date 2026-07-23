# 输出与校验

## 运行依赖

- Python 3.10+
- Pillow
- NumPy

运行 `python scripts/capability_check.py` 查看当前环境。第一版不要求 SoundFont、FluidSynth 或 ffmpeg。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `synesthesia.mid` | 标准 MIDI 文件，包含四个音色通道 |
| `synesthesia.wav` | 44.1 kHz、16-bit、双声道基础合成试听 |
| `source-preview.png` | HTML 预览使用的方形源图副本 |
| `visual-score.png` | 1600×1000 位图视觉乐谱 |
| `visual-score.svg` | 可缩放、可继续排版的矢量乐谱 |
| `interactive-preview.html` | 本地交互预览，引用同目录 WAV 和 PNG |
| `mapping.json` | 参数、规则与逐音符数据 |
| `validation.json` | 输出完整性与格式校验 |

不要单独移动 HTML；它需要与 `synesthesia.wav`、`source-preview.png` 保持同目录。

## 校验门槛

只有同时满足以下条件才报告成功：

1. 至少生成一个音符。
2. MIDI 文件以 `MThd` 开头。
3. WAV 为双声道、16-bit，且包含音频帧。
4. PNG 乐谱尺寸为 1600×1000。
5. 预期交付文件全部存在。

`raw_peak` 是归一化前的混音峰值，不是成片响度。内置合成器会软限幅并归一化，仍应把 WAV 当作试听草图。

## 降级处理

- 没有 Python 执行能力：只输出映射方案和建议参数。
- 缺少 Pillow：无法读取图像或生成视觉乐谱。
- 缺少 NumPy：无法提取纹理、合成 WAV 或计算映射。
- 浏览器限制本地媒体：启动简单本地服务器后打开 HTML，或直接播放 WAV。
- 输入图像过于单一：降低显著性阈值可通过提高 `density` 完成；不要伪造“检测到丰富色彩”。

## 当前边界

第一版不包括音频转视觉、MP3 解码、SoundFont 渲染、视频导出和实时摄像头输入。遇到这些需求时，应说明边界并提出下一阶段方案。
