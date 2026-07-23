# Visual Bible Schema

`visual-bible.json` 是所有场景共同引用的稳定约束。它不替代创作判断，而是避免每一幕重新漂移。

## 必要字段

```json
{
  "schema_version": "1.0",
  "title": "梦境项目标题",
  "aspect_ratio": "4:5",
  "style": "梦境胶片、低饱和冷色、湿润颗粒",
  "palette": {
    "dominant": ["#0D1B2A", "#68727B"],
    "accent": ["#C94335"],
    "highlight": ["#E9D8A6"]
  },
  "camera_language": ["低机位", "长焦压缩", "轻微失焦"],
  "lighting": "阴天散射光，主体边缘有很弱的逆光",
  "texture": "湿润胶片颗粒，细微扫描线",
  "character_anchor": "穿深色外套的人物背影，短发，步伐缓慢",
  "motif": {
    "name": "红色圆形物体",
    "base_form": "红色气球",
    "states": [
      "真实气球",
      "水洼倒影",
      "墙面圆形光斑",
      "悬浮光球",
      "红色月亮"
    ]
  },
  "negative_constraints": [
    "不改变人物服装主色",
    "不加入无关文字和 logo",
    "不使用高饱和多色霓虹背景"
  ]
}
```

## 使用规则

- `character_anchor` 用外观、服装、轮廓和动作写，不要只写抽象身份。
- `motif.states` 表示视觉变化，不表示每一幕都必须完整出现。
- 色板使用十六进制值，方便后续做海报、网页或视频统一。
- 变形应逐步发生；不要从写实物件直接跳成完全无关的抽象图形。
- `negative_constraints` 只放会破坏连续性的约束，不要堆无关的负面词。
