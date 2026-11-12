# TikTok 引流带货：手机实拍风（tiktok-ugc）

## 适用

在 TikTok、Reels、Shorts 上投放的带货 / 引流短视频，目标是让观众觉得“这是真人在家用手机拍的一件事”，而不是广告片。竖屏 9:16，15–45 秒。

不适用：品牌形象片、要求干净棚拍或精致质感的产品片，这些按用户描述另写。

## 结构

默认按 `references/batch.md` 1.0：钩子 → 痛点 → 方案 → 证明 → 下单引导。前 3 秒必须是一个具体的生活场景或问题，不是产品特写。

- **钩子镜头的动作在第 1 秒内发生**，并且是真实速度。观众划走只要一秒，慢慢展开的动作留不住人。剧烈动作的写法见 `batch.md` 1.16，失败案例见 `failure-modes.md`。
- **整片 25–35 秒、8–10 个镜头，每镜 3–4 秒**，一镜只演一件事。
- **证据感镜头更可信：** 手机随手拍、家里监控录像的画面（高角度广角、略糊、固定不动）、第一人称手部特写，都比电影感镜头更像真事。监控画面通常不是 9:16，剪辑时完整放进竖屏加黑边（`fit: contain`），不要裁切。
- **产品第一次出现时，要正好在解决痛点的那一刻**（例如手指按下呼叫键），而不是放在开头。之后的卖点镜头里，产品不运镜或只轻微手持晃动。

## 画面要求

- **像手机拍的：** 手持或随手放着拍，机位在人的视线或胸口高度；构图可以略歪、主体不一定居中；画面整体清楚，但不是每一处都锐利。
- **真实的家：** 有人住过的痕迹（杂物、旧家具、褶皱、电线、插座），不同镜头的房间不一定是样板间里的“完美角落”。
- **真实的光：** 混合光源（窗光加室内灯），亮暗不均，窗户可以略过曝，暗处有轻微噪点；不要均匀柔光和统一暖色调色。
- **真实的人：** 普通人的长相和身材，皮肤有纹理，衣服有褶皱；姿势是自然反应，不是摆拍。
- **真实的产品：** 实际大小，和环境有接触阴影和反光；需要供电的产品画出电源线和插头；不要像渲染图那样完美。
- **人物和场景跨镜头一致**（按 `references/batch.md` 1.15 先做参考图）。

## 提示词写法

按拍照的人来写，150 词左右，依次写：

1. **拍法：** 设备和机位。例如 `Vertical photo taken on an iPhone main camera, held at chest height by someone standing in the doorway, slightly tilted.`
2. **场景和人：** 谁、在哪、在干什么，引用参考图时说明保持哪些（脸、发型、衣服、房间布局）。
3. **这一刻的动作：** 首帧是动作的起点：平缓的动作取开始前一刻，剧烈的动作取已经失去平衡的一瞬（`batch.md` 1.1）。用人的自然反应写，不逐条规定肢体角度。
4. **光和环境：** 具体光源、亮暗关系、两三件具体的杂物。
5. **产品：** 位置、朝向、实际大小、电源线。产品和人同框时，按“离镜头从近到远”写出层次，并确认产品没有被完全挡住（会被挡住的东西，视频里会消失）。产品只是背景小物件时不单独传产品图。
6. **关键禁止项最多三条：** 例如不要文字和水印、不要第二台设备。

视频提示词按 `batch.md` 1.16 写：一段一个动作，写清第几秒发生、真实速度，不用软化词，写清重力，产品镜头不推近。

不写：`realistic`、`photorealistic`、`hyper-detailed`、`cinematic`、`8K`、`masterpiece`、`soft beautiful light`、`perfect`。这些词会把画面推向图库照片或广告大片。也不要堆十几条禁止项。

## 验收规则（加进 spec 的 `checks`，按片子改写）

```json
[
  {"id": "ugc-phone", "category": "realism", "text": "Reads as a phone photo taken by a person in a real home: uneven mixed light, lived-in details, not a stock photo, showroom or 3D render."},
  {"id": "ugc-people", "category": "realism", "text": "People look ordinary: textured skin, natural unposed body language, no plastic or airbrushed faces."},
  {"id": "ugc-product", "category": "realism", "text": "Product at its real size with contact shadow; powered products show their cable; no render-like perfection."},
  {"id": "ugc-same", "category": "continuity", "text": "Recurring people and rooms match their reference shots: same face, age, hair, clothes, furniture and layout."},
  {"id": "ugc-depth", "category": "realism", "text": "Occlusion is physically right: what is nearer the lens covers what is behind it; the product is never pasted over a nearer person or object."},
  {"id": "ugc-speed", "category": "action", "text": "Actions play at real-time speed and read as what they are at a glance; dropped things fall at once; no slow-motion or staged feel."}
]
```

审片时把整张图当成刷到的一条视频看一眼：第一眼像不像广告或 AI 图。像的话判 `realism` 不通过，写清楚是哪里（光太匀、房间太干净、姿势在演、产品像渲染、产品像贴上去的），再改提示词重生成。视频按每秒 6 帧看动作时段，按 `failure-modes.md` 逐条对照。

## 节奏

- **0 秒就有内容：** 旁白在 0–0.2 秒开口，钩子动作在第 1 秒内发生，开头不留黑场、不放 logo。
- **镜头 3–4 秒一换，**一镜一件事；需要停住看清的产品镜头最多 4 秒。
- **旁白不断档：** 句与句之间的空白控制在 0.9 秒以内；旁白长、画面短时，先把画面用满源片长度、减少停顿，旁白最多加速约 6%，不要硬塞。
- **关键词对关键画面：** 痛点句压在痛点画面上，“One single press”对上按键，卖点词对上演示它的镜头，切镜尽量落在句与句之间的停顿里。
- **结尾干脆：** 最后一句旁白说完后，下单按钮再停留 1.5–2 秒就结束；最后一个镜头按这个时长裁短，不要留长段无声画面。
- **成片验收：** 用 `qc_media.py` 的 `sound_windows` 查句间最长空白（≤ 0.9 秒）和结尾无声的长度（≤ 2 秒）；响度约 −15.5 LUFS，峰值不高于 −1.5 dBFS。

## 声音与文字

- **声音统一：** 全片（同一系列的片子也一样）只用一个固定的声音：spec 顶层写 `"voice": {"provider": "fish_audio", "model": "s2.1-pro", "reference_id": "..."}`，不用不指定 id 的“默认声音”（每次请求可能换成另一个人）。整段旁白一次生成，用 `scripts/split_narration.py` 按句间停顿切成每句一个文件，再放到各镜头；看它打印的切点和每段首尾词，对不上就调整。生成后跑 `scripts/voice_check.py`，音高差异过大就判不通过。声音由 Agent 按片子的人群和语气自己选一个合适的（例如面向成年子女的关爱类广告，用温和、像聊天的成年女声），在提案里说明选了哪个，选定后锁进 spec，整片和后续同系列的片子都不换，用户要求时才换。用户有授权的声音（本人或配音员克隆）优先；不用模仿名人或角色的社区声音。
- **旁白：** 英文约每秒 2.5–2.8 个词。先按旁白算时长，再分配镜头时长；需要时旁白最多加速约 6%。把关键词对上关键画面（例如 “One single press” 对上按键）。旁白的发音和听感由用户试听。
- **模型原声：** 默认静音（`clip_audio: false`），只用旁白。有的模型自带的原声很响，也不一定和画面对得上。不加音乐更像真人拍的，要加由用户决定。
- **画面文字：** 只用用户脚本里的叠加文字，不另外加字幕。用大号粗体，白字黑描边或放在半透明深色底上，放在安全区内，并且放在首帧的留白处，不挡住人脸和产品。下单按钮用醒目的底色，放在结尾产品镜头特意留出的空位上。
- **AI 标注：** 默认不在画面上加任何标注小字（如 “Dramatization”“Simulated”），它会干扰成片。提醒用户发布时打开平台的 AI 生成内容标签。只有用户明确要求时才加到画面里。

## 要向用户要的素材

- **产品在真实家里的手机实拍照**（不同角度，带电源线）。产品参考图如果是渲染图，生成的产品也会有渲染感，这一项对真实感帮助最大。
- 如果有真人出镜：人物参考照片和使用授权。
- 平台要求披露 AI 生成内容时，按平台规则标注。
