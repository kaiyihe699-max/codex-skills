# 输出、支持与限制

## 依赖

- Python 3.10+
- rawpy / LibRaw
- NumPy
- Pillow
- tifffile
- ExifTool：可选，只补充厂商、镜头和容器元数据

运行 `python scripts/capability_check.py` 查看本机 rawpy、LibRaw 版本和编解码特性。

## RAW 支持

DNG、CR2/CR3、NEF、ARW、RW2、RAF、ORF、PEF 等只是常见扩展名示例。真正能否解码取决于：

1. 当前 LibRaw 是否识别相机型号和文件版本。
2. 文件是否完整。
3. 构建是否包含该格式所需的可选编解码器。

不要根据扩展名提前承诺支持。让 `rawpy.imread()` 做最终判断。

## 色彩与 TIFF

四套 TIFF 使用 LibRaw 转换到 sRGB 原色，再经过 Skill 的曝光与色调配方，最后以 sRGB 传递函数编码为 RGB uint16。TIFF、JPEG、分析图和接触表均嵌入同一份 sRGB ICC。

它们是高位深、显示参考的显影候选：

- 适合继续做常规调色、比较和导出。
- 不是相机原生色域的场景线性数据。
- 当前版本不嵌入相机 DCP、ICC 或厂商专有配置。

这里的 ICC 指输出 sRGB 显示配置；“不嵌入相机 ICC”指不使用相机输入配置文件，两者不要混淆。

需要 VFX、ACES 或严格场景线性流程时，不要把候选 TIFF 当作 ACES 输入；应在后续版本增加线性导出与明确色彩配置。

## 元数据

rawpy 核心提供尺寸、CFA、黑白电平、白平衡、曝光参数和镜头结构。相机 Make/Model 等容器字段在不同格式中暴露程度不同。

安装 ExifTool 后，脚本会把 JSON 放进 `metadata.json` 的 `exiftool` 字段。默认把绝对路径、GPS、机身序列号和所有者字段替换为 `[redacted]`；只有明确使用 `--include-private-metadata` 时才保留。没有 ExifTool 不影响 RAW 解码和显影。

## 方向与覆盖

脚本记录最初的 `raw.sizes.flip`，让未旋转的传感器分析 mask 与候选图使用同一方向变换。输出目录默认必须为空；`--overwrite` 只允许清理已知 RAW Darkroom 文件，遇到其他文件或子目录立即停止。

## 性能

默认生成四套全分辨率 RGB uint16 TIFF，24MP RAW 可能产生数百 MB 输出。内存不足或只想快速看方向时使用 `--half-size`，并在最终交付时重新运行全分辨率版本。

源 RAW 永远只读，不进行原地修改。
