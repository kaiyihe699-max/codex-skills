# 输出、Adobe 参数与限制

## XMP

输出 `01-neutral-positive.xmp` 和 `02-print-interpretation.xmp`。它们使用 Camera Raw 命名空间，作用于已经反相的正片 TIFF/JPEG，用于 Lightroom、Adobe Camera Raw 或 Photoshop Camera Raw Filter 的后续调整起点。

基础 ACR 参数无法表达：

- 片基密度减法
- 胶片染料层通道模型
- 红外缺陷蒙版
- 多扫描 HDR 透射率合并

因此 XMP 不是完整负片转换配方。完整复现依赖 `parameters.json` 和输出 TIFF。

## Photoshop 参数

`photoshop-adjustments.json` 记录：

- 正片曲线节点
- 建议色彩平衡
- 颗粒参数
- 灰尘与划痕 mask 路径
- 可作为图层蒙版导入的 PNG

不直接修改 PSD，不声称已创建 Photoshop 图层。

## OCR

- Tesseract 可用时自动识别。
- 不可用时仍输出边码图，状态为 `unavailable`。
- 识别结果经过字符清理和胶片配置模式匹配。
- 低置信度、边码残缺和不同批次代码只作为候选。

## 配置文件

内置扫描仪/相机和胶片配置是通用起点。用户配置 JSON 必须具有唯一 `id`，并提供与内置配置相同的核心字段。

## 限制

- JPEG 或 8-bit 输入无法恢复已经量化或裁切的数据。
- 没有片边时片基只能估计；中性区域假设可能受场景颜色影响。
- 多次扫描中的胶片位移、翘曲或扫描器自动处理会降低 HDR 对齐质量。
- 大划痕和霉斑可能覆盖真实内容，邻域修复不能保证真实性。
- 红外除尘并不适合所有胶片材料。
- 片种识别依赖可见边码与配置库，不保证覆盖所有厂商、年代和批次。
- 印相版本是解释性输出，不等于特定相纸、放大机和化学工艺的精确模拟。
