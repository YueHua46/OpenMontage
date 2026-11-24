# TikTok 引流带货：手机实拍风（tiktok-ugc）

## 适用

在 TikTok、Reels、Shorts 上投放的带货 / 引流短视频，目标是让观众第一眼觉得“这是真人在真实生活里用手机拍的一件事”，而不是精修广告片。竖屏 9:16，通常 15–45 秒。

不适用：品牌形象大片、要求干净棚拍或高奢商业质感的产品片（这类按用户描述另写）。

## 结构

默认按 `references/batch.md` 1.0：钩子 → 痛点 → 方案 → 证明 → 下单引导。（注：若用户已提供完整原稿剧本或只要求修复缺陷，遵守 `SKILL.md` 原稿锁定规则，仅应用本模板的手机实拍观感与动作节奏，不借风格模板删改原稿镜头与时长。）

- **前 3 秒必须是具体生活场景或突发痛点**，不是商品摆拍特写；钩子动作在第 1 秒内以真实速度发生（观众划走只需一秒，慢动作留不住人）。
- **常规新建脚本建议 25–35 秒、8–10 个镜头，每镜 3–4 秒**，一镜只演一件事。
- **证据感镜头更可信：** 家人手机随手抓拍、第一人称手部操作特写、固定设备实录视角（非 9:16 素材剪辑时用 `fit: contain` 留黑边更真实），都比电影感运镜更像真事。
- **商品首次出场卡在解决痛点的那一刻**（如伸手触发核心功能），不硬塞在片头；后续卖点镜头里商品保持固定机位或仅极轻微手持呼吸感。

## 画面要求

- **像手机拍的：** 手持或随手搁置拍摄，机位在人眼或胸口高度；构图可略微不对称、主体不必死板居中；整体清晰但不过度锐化。
- **真实的生活空间：** 有人居住使用的痕迹（日常杂物、旧家具、衣褶、插座电线），不做一尘不染的样板间。
- **真实混合光：** 窗光混室内灯，亮暗自然不均，窗外可略过曝、暗部允许轻微手机噪点；不用影棚均匀柔光或浓重电影调色。
- **真实普通人：** 普通人的长相与身材，皮肤有真实纹理，衣服有自然褶皱；姿态是生活里的本能反应，不是模特摆拍。
- **真实商品质感：** 保持真实物理尺寸（见 `batch.md` 1.155 及对应 `categories/` 品类指南），与桌面/墙面有真实接触阴影和环境反光，有线供电产品露出电源线，不带 3D 渲染塑料光感。

## 提示词写法

按“拿手机拍照的普通人”视角写，150 词左右，依次包含：

1. **拍法与机位：** 如 `Vertical photo taken on an iPhone main camera, held at chest height by someone standing in the doorway, slightly tilted.`
2. **场景与人物：** 谁、在哪、干什么，引用 `@id` 参考母版保持脸型、体型、服装和房间布局。
3. **动作起点时刻：** 平缓动作取开始前一刻，剧烈动作取刚失去平衡的一瞬（`batch.md` 1.1），写自然身体反应，不死板规定关节角度。
4. **光线与生活细节：** 具体光源、明暗对比、两三件生活杂物。
5. **商品与遮挡层次：** 位置、朝向、真实比例、线缆；人与商品同框时按“离镜头从近到远”写清遮挡层次，确保后续要露出的商品在首帧不被完全挡死。商品只是背景小物件时不单独传棚拍白底图。
6. **核心禁止项最多三条：** 如无文字水印、无多余设备。

**禁用词：** `realistic`、`photorealistic`、`hyper-detailed`、`cinematic`、`8K`、`masterpiece`、`soft beautiful light`、`perfect`（这些词会把画面推向图库照片或商业大片）。视频提示词按 `batch.md` 1.16 写。

## 验收规则（加进 spec 的 `checks`，按片子改写）

```json
[
  {"id": "ugc-phone", "category": "realism", "text": "Reads as a phone photo taken by a person in a real environment: uneven mixed light, lived-in details, not a stock photo, showroom or 3D render."},
  {"id": "ugc-people", "category": "realism", "text": "People look ordinary: textured skin, natural unposed body language, no plastic or airbrushed faces."},
  {"id": "ugc-product", "category": "realism", "text": "Product at its real physical scale with contact shadow and proper cabling; no render-like perfection."},
  {"id": "ugc-same", "category": "continuity", "text": "Recurring people and spaces match their reference shots: same face, body build, age, hair, clothes, furniture and layout."},
  {"id": "ugc-depth", "category": "realism", "text": "Occlusion is physically right: what is nearer the lens covers what is behind it; the product is never pasted over a nearer person or object."},
  {"id": "ugc-speed", "category": "action", "text": "Actions play at real-time speed and read as what they are at a glance; dropped things fall at once; no slow-motion or staged feel."}
]
```

审片时先当成刷短视频看第一眼：像广告或 AI 图就判 `realism` 不通过，写明具体原因（光太匀、房间太假、姿势在演、商品像贴纸）再改输入重抽。

## 节奏、声音与文字要点

- **0 秒即入戏：** 旁白在 0–0.2 秒开口，片头不留黑场、不放品牌片头 logo。
- **紧凑不断档：** 镜头 3–4 秒一换；句间空白 ≤ 0.9 秒；关键词精准压在关键动作上（如“One single press”正对按键瞬间）；最后一句说完后下单按钮停留 1.5–2 秒干脆收尾（结尾无声 ≤ 2 秒）。
- **声音与字幕配置：** 旁白锁音色切分及角色对白方案按 `references/compose.md` 执行；UGC 实拍风默认关闭视频原声杂音、不垫背景音乐（更像真人随手拍，需配乐由用户定）；只放用户脚本要求的醒目大字与结尾预留空位上的 CTA 按钮，不挡人脸和商品。AI 平台标签披露遵守 `SKILL.md` 硬规则。

## 建议向用户索取的素材

- **商品在真实使用环境里的手机实拍照**（多角度，含真实连线/安装状态）——不用渲染图作参考是对真实感提升最大的一项。
- 真机功能演示录像或真实 App 录屏（用于替代高危红色镜头）。
- 若指定真人出镜：人物照片及合法使用授权。
