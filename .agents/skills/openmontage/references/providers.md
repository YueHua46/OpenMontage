# 渠道、配音、音乐和模型细节

## 渠道选择与切换

各渠道并存，图片和视频分别选择；持久化默认值以仓库 `relay_providers.yaml` 的 `routing.default_tools` 为准，不以本文件的历史示例代替当前配置。当前约定：

- 图片：tokenshop / tststs，默认 `relay_tststs_gpt_image_2`；原有 `gpt-image-2.5` 也保留。
- 视频：OmniaKey MiniMax H3，默认 `relay_omniakey_minimax_h3`，端点 `https://api.omniakey.com/v1/media/tasks`，密钥变量 `OMNIAKEY_API_KEY`；tokenshop 的 `relay_tststs_grok_video_1_5` 保留为可切回的渠道。
- 配音：Fish Audio 直连（非中转站），工具 `fish_audio_tts`，密钥变量 `FISH_AUDIO_API_KEY`，详见下方「配音（Fish Audio）」。
- 密钥只放在仓库 `.env` 或环境变量中；不写入技能、YAML、示例、日志或交付物。

用户明确要求切换渠道就是该项默认渠道切换的授权，无需重复询问。先从 `relay list` 确认目标工具已注册，再执行对应命令。例如恢复 tokenshop 视频：

```powershell
.venv/Scripts/python.exe -X utf8 -m tools.relay route --kind video --tool relay_tststs_grok_video_1_5
```

其他渠道接入后，将 `--tool` 替换为对应的已注册工具名；`--kind image` 同样可单独切换图片默认工具。切换不删除旧渠道或密钥，不移动历史任务，也不发起生成。用户明确要求撤回某渠道时，删除该渠道配置与对应本地密钥，并将默认值恢复到保留的渠道。用 `relay route` 和 `relay check` 核验结果。

新项目按当前默认值提案，获批后仍以 `preferred_tool` 锁定；用户显式指定的渠道优先于默认值。已有 `.relay-job.json` 始终使用记录中的原工具恢复，不能把旧任务 ID 交给新渠道。默认渠道失败时如实报告，不自动改走另一家。渠道切换不等于授权重做失败视频或新增付费调用。

新增渠道时用只读 `GET /v1/models` 核验 Key 与精确模型 ID，按该渠道协议登记提交、查询和下载映射；模型列表可用不等于生成链路实测通过。先做本地 Mock 测试和 `dry_run`，真实生成仍走费用批准。新渠道费率未知时明确说明，不能照搬旧渠道报价；未经明确授权不传 `allow_unpriced: true`。

## 配音（Fish Audio）

Fish Audio 是团队默认配音渠道，密钥在本机仓库 `.env`。默认值：

- 模型：`s2.1-pro-free`（团队默认）；付费版为 `s2.1-pro`。免费期截止前后先核对是否仍可用。
- 音色：官方 Sarah，`voice_id: 933563129e564b19a115bedd57b7406a`（作者 `Fish Official`，英文女声）。同名公开音色很多，不要按名称重新搜索后随意替换。

通过 `tts_selector` 调用，必须同时传下列参数：

```python
{
    "preferred_provider": "fish_audio",
    "model": "s2.1-pro-free",
    "voice_id": "933563129e564b19a115bedd57b7406a",
    "allow_non_relay_paid": True,  # relay_only 策略下不传会被拦截
    "text": "...",
    "output_path": "projects/<id>/assets/audio/<name>.mp3",
}
```

2026-11-06 已实测：选择器、音色和音频下载全链路通过；英文 84 字符生成 4.94 秒 MP3；余额调用前后不变。工具返回的 `cost_usd` 仍按代码中过期的促销日期估算，实际费用以 `GET https://api.fish.audio/wallet/self/api-credit` 的余额变化为准，该接口只读。

免费模型的限制：官方开发者页写明免费至 2026-11-30（博客同时出现 8 月 31 日，日期曾多次变动），无 SLA，请求可能用于改进模型，部分商用场景受限。仓库的 `fish-audio-tts` 技能要求生产或客户旁白不走免费模型；用于商业成片时，提案中要提醒用户并给出 `s2.1-pro` 选项（$15/百万字节），由用户决定。免费期结束或调用失败时如实报告，不自动改用付费模型或其他供应商。

同一段英文用付费 `s2.1-pro` 时比免费版更慢（84 字符为 5.64 秒，免费版 4.94 秒）。写分镜时按每秒约 2.4 个英文词估算，生成后逐句用 `ffprobe` 核对时长；超出时交给用户决定改时长、改词或调语速，不擅自处理。`prosody: {"speed": 1.1}` 能传到 Fish，但 2026-11-06 实测各句时长变化从 -17% 到 +2%，不能当作精确的倍速；每句首尾约有 0.15 秒静音，核算时长时用 `silencedetect` 扣除。

## 背景音乐（Openverse）

`pixabay_music` 是网页抓取工具，2026-11-06 实测返回 HTTP 403，不要作为默认来源，也不要尝试绕过拦截。默认使用技能自带脚本，它调用 Openverse 官方公开接口，无需 Key，默认只返回不要求署名的 CC0 曲目：

```powershell
.venv/Scripts/python.exe -X utf8 <SKILL>/scripts/find_music.py search "corporate" "cinematic buildup" "uplifting" --min 30 --max 180
.venv/Scripts/python.exe -X utf8 <SKILL>/scripts/find_music.py download <openverse-id> --output projects/<id>/assets/music/bgm.mp3
```

下载时会写入 `<output>.license.json`，记录标题、作者、授权和来源页，随项目保留。Agent 听不到音频，不能声称听感合适：提供 1–2 首候选，由用户试听后决定。商用前让用户到来源页复核授权；CC0 由上传者声明，不能保证绝对无版权争议。Freesound 文件是 hq 预览 MP3，音质可用但不是无损原文件。

需要署名也可接受时才传 `--license by`，并把 `.license.json` 的 `attribution` 提供给用户放进发布文案。用户自有曲目优先：放在仓库 `music_library/`。

## MiniMax H3（OmniaKey）

使用精确工具 `preferred_tool: relay_omniakey_minimax_h3`、模型 `minimax-h3`。它使用 OmniaKey Media Tasks 协议，不是 MiniMax 官方 v2 地址，也不是 Grok 的 `/videos/generations`。接入模板见仓库 `relay_providers.omniakey.example.yaml`；最新限制和报价以该站视频文档 `https://docs.omniakey.com/en/media/video` 及运行时 registry 为准。

| 用法 | operation | 本地素材参数 |
|---|---|---|
| 文生视频 | `text_to_video` | `prompt`；不传图片 |
| 首帧图生视频 | `image_to_video` | `reference_image_path` |
| 首尾帧图生视频 | `image_to_video` | `reference_image_path` + `last_frame_path` |
| 参考图生视频 | `reference_to_video` | `reference_image_paths: [...]` |

首帧决定开场，参考图仅引导主体或风格，不能互相替代；首尾帧和参考图不得混传。当前适配器的参考模式接收图片，不能据此声称已支持参考音频或视频输入。参考图数量以工具限制为准。

时长为整数 4–15 秒；最低档是 `768P`，另有 `2K`，不要沿用其他渠道的 1 秒、480p 参数。文生视频用具体画幅；图生视频一般跟随首帧形状，不能仅凭 aspect_ratio 保证输出尺寸。H3 自带音轨，默认保留原片，静音仍需用户明确要求。

2026-11-05 的首帧实测已通过提交、查询、下载、SHA-256 比对和完整解码；请求 4 秒/768P，返回 4.458 秒/768×960，带音轨。文生视频、参考图和首尾帧的本地契约测试、dry_run 通过，但尚未分别真实生成；不要把这些测试等同于线上实测。

2026-11-06 的 9:16 首帧模式实测：2K 输出 1440×2560，768P 输出 768×1344，都是 24fps；请求 4/5/6 秒分别返回约 4.46/5.17/6.58 秒，按分镜裁剪即可。原声响度约 -9 到 -11 LUFS，合成时要统一。

提示词要点（实测）：视频提示词必须描述首帧的实际构图，不能要求与首帧不同的景别；首帧是完整产品而提示词写“特写”时，模型在 1.17 秒处自行硬切，特写中产品天线消失。结尾写明 one continuous take、no cuts、no sudden zoom 后，同一镜头未再检出切镜。批量后用 ffmpeg `select='gt(scene,0.2)'` 检查切镜。

并发（实测）：同时提交 7 段时，4 段成功，3 段返回 `The generation service is busy`。视频并发上限先按 3 执行；失败重试属于新的付费提交，需用户批准并传 `confirm_resubmit: true`。`gpt-image-2` 并发 12 张（6 路）未被限流，但可能忽略 9:16，实测文生图和图生图都返回过其他比例（1024×1536、1458×1536），出图后要核对尺寸，再做视频。

工具将素材映射为 `input.media[]` 的 `first_frame` / `last_frame` / `reference_image`，并为每次任务持久化独立的 `Idempotency-Key`。整个请求（包括 Base64）不超过 1 MiB。两个分辨率按各自 `per_second_by_resolution` 费率估算，不混用报价。区分文档声明、本地契约测试通过和逐模式真实生成通过，未实测的模式不得写成已实测。
