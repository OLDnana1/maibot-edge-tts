# Edge TTS 免费语音插件

用免费的微软 Edge 在线语音合成（`edge-tts`）把文字变成 QQ 语音。不消耗任何 TTS 付费额度，也不需要自建语音服务。

## 功能

- **大模型自主发语音**：注册 `edge_tts_speak` 工具，由模型决定什么时候用声音说话。
- **调试命令**：`/语音 你好` 或 `/tts 你好`，立刻合成并发送，用来验证整条链路。群里 `@机器人 /语音 你好` 也能识别。
- **参数可调**：音色、语速、音量、音调、代理、单次字数上限、冷却时间都可以在 WebUI 插件配置里改。
- **常用音色查询**：`/音色` 查看内置的中文音色列表。
- **配置查询**：`/语音设置` 查看当前参数和依赖状态。

## 安装

1. 把整个 `Edge-tts` 目录放到 MaiBot 的 `plugins/` 目录下（目录名可以保留 `Edge-tts`，插件 ID 由 `_manifest.json` 决定）。
2. 确认运行 MaiBot 的 Python 环境里已经安装依赖：

   ```bash
   python -m pip install edge-tts
   ```

   Windows 一键包用户请在 MaiBot 使用的那个 Python 里执行（本机为 `D:\PY\python.exe`）。

3. 重启 MaiBot，在 WebUI 插件列表里启用「Edge TTS 免费语音」。
4. 在群里发送 `/语音 你好，这是一条测试语音`，确认能收到语音消息。

> `config.toml` 不需要手动创建，也不需要提交到仓库。MaiBot 首次加载插件时会根据 `config_model` 自动生成默认配置；后续修改请在 WebUI 的插件配置页操作。

### Docker / 云服务器部署

MaiBot 跑在 Docker 里时，插件目录通常是 compose 挂载的 `plugins/`（例如 `~/maibot/data/MaiMBot/plugins`），把插件目录复制进去后重启 core 容器即可。

依赖需要装在**容器内的 Python** 里：

```bash
docker exec -it maim-bot-core python -m pip install edge-tts
```

`_manifest.json` 已经声明了 `edge-tts>=7.0.0`，宿主在加载插件时也会尝试自动补装。但如果容器重建后 `site-packages` 没有挂卷，依赖会丢失，需要重新安装，或者把 `edge-tts` 直接加进镜像。

## 让模型主动发语音

插件只提供工具，是否发语音由模型判断。请在 WebUI 的 **Prompt 管理 → 行为风格** 里加一句类似的话：

> 当你想用声音回应时，调用 `edge_tts_speak` 工具，把要朗读的内容放进 text 参数；调用后不要再重复发送相同的文字。

注意：Planner 只读取「行为风格」，写在「群聊/私聊提示词」里不会影响它是否调用工具。

## 配置项

WebUI 插件配置页的字段名称、提示文字都是中文。音色是下拉列表，显示名直接标明语言和性别；语速、音量、音调只填数字，单位写在字段名里。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 插件总开关 |
| `plugin.command_enabled` | `true` | `/语音`、`/tts`、`/音色` 等调试命令开关 |
| `plugin.cooldown_seconds` | `3.0` | 同一用户在同一会话的触发间隔 |
| `plugin.max_text_length` | `200` | 单次合成字数上限 |
| `tts.voice` | `中文-晓晓-女声` | 音色，下拉选择（20 项，显示名即"语言-名字-性别"） |
| `tts.custom_voice` | 空 | 自定义音色 ID，填写后覆盖下拉选择，可填任意 Edge TTS 音色 |
| `tts.rate` | `0` | 语速（%），只填数字，范围 `-50` ~ `100` |
| `tts.volume` | `0` | 音量（%），只填数字，范围 `-100` ~ `100` |
| `tts.pitch` | `0` | 音调（Hz），只填数字，范围 `-50` ~ `50` |
| `tts.proxy` | 空 | 访问微软服务需要的代理，例如 `http://127.0.0.1:7890` |
| `tts.connect_timeout` | `10` | 连接超时（秒） |
| `tts.receive_timeout` | `60` | 单次合成总超时（秒） |
| `tts.retry_times` | `2` | 合成失败的重试次数（微软接口偶发限流） |
| `tts.cache_size` | `16` | 相同文本+参数的缓存条数，0 表示关闭 |

### 语速 / 音量 / 音调怎么填

输入框只填数字，单位和范围写在字段名和提示里：

| 字段 | 单位 | 范围 | 例子 |
| --- | --- | --- | --- |
| 语速（%） | 百分比 | `-50` ~ `100` | 填 `15` 表示快 15%，填 `-20` 表示慢 20% |
| 音量（%） | 百分比 | `-100` ~ `100` | 填 `-30` 表示音量降低 30%，`-100` 基本静音 |
| 音调（Hz） | 赫兹 | `-50` ~ `50` | 填 `5` 表示升高 5Hz，填 `-10` 表示降低 10Hz |

插件内部会自动拼成 edge-tts 需要的 `+15%`、`-10Hz`。填超范围会自动收敛到边界值（比如填 `500` 会按 `100` 处理），不会导致配置失效。

## 音色清单

下拉里显示的是"语言-名字-性别"，存的是对应的官方音色 ID：

| 下拉显示名 | 对应音色 ID | 特点 |
| --- | --- | --- |
| 中文-晓晓-女声 | `zh-CN-XiaoxiaoNeural` | 普通话，温暖自然（默认） |
| 中文-晓伊-女声 | `zh-CN-XiaoyiNeural` | 普通话，活泼年轻 |
| 中文-云健-男声 | `zh-CN-YunjianNeural` | 普通话，激昂有力，适合解说 |
| 中文-云希-男声 | `zh-CN-YunxiNeural` | 普通话，阳光少年 |
| 中文-云夏-男声 | `zh-CN-YunxiaNeural` | 普通话，孩童音色 |
| 中文-云扬-男声 | `zh-CN-YunyangNeural` | 普通话，新闻播报，稳重 |
| 东北话-晓北-女声 | `zh-CN-liaoning-XiaobeiNeural` | 东北话 |
| 陕西话-晓妮-女声 | `zh-CN-shaanxi-XiaoniNeural` | 陕西话 |
| 粤语-曉佳-女声 | `zh-HK-HiuGaaiNeural` | 粤语 |
| 粤语-曉曼-女声 | `zh-HK-HiuMaanNeural` | 粤语，温柔 |
| 粤语-雲龍-男声 | `zh-HK-WanLungNeural` | 粤语 |
| 台湾腔-曉臻-女声 | `zh-TW-HsiaoChenNeural` | 台湾腔 |
| 台湾腔-曉雨-女声 | `zh-TW-HsiaoYuNeural` | 台湾腔，年轻 |
| 台湾腔-雲哲-男声 | `zh-TW-YunJheNeural` | 台湾腔 |
| 英语-美式Ava-女声 | `en-US-AvaNeural` | 英语（美式） |
| 英语-美式Andrew-男声 | `en-US-AndrewNeural` | 英语（美式） |
| 英语-英式Sonia-女声 | `en-GB-SoniaNeural` | 英语（英式） |
| 日语-七海-女声 | `ja-JP-NanamiNeural` | 日语 |
| 韩语-善熙-女声 | `ko-KR-SunHiNeural` | 韩语 |
| 法语-Denise-女声 | `fr-FR-DeniseNeural` | 法语 |

Edge TTS 实际有 400 多个音色。查看完整列表：

```bash
edge-tts --list-voices
```

或者：

```bash
python -c "import asyncio, edge_tts; [print(v['ShortName'], v['Locale'], v['Gender']) for v in asyncio.run(edge_tts.list_voices())]"
```

不在上面的 20 项里的音色（比如西班牙语 `es-ES-ElviraNeural`），填到「自定义音色 ID」里即可生效。


## 发送原理

```
插件
  └─ ctx.send.custom("voice", base64_mp3, stream_id, storage_message=False)
       └─ MaiBot 核心构造 VoiceComponent
            └─ SnowLuma 适配器编码为 OneBot record 段
                 {"type": "record", "data": {"file": "base64://..."}}
                      └─ QQ 语音消息
```

这里用的是 MaiBot 官方的 `send.custom` 能力，而不是直接调用适配器 API，因此换适配器时只要目标平台支持 `voice` 段就能继续工作。

## 常见问题

**语音发不出来，但 `/语音` 没有任何提示？**
先看插件配置里的 `command_enabled` 是否为 `true`，再看日志里有没有 `[EdgeTTS]` 开头的行。插件日志使用 `ctx.logger`，会进入 MaiBot 主日志。

**合成报网络错误**
`edge-tts` 需要访问微软的语音服务。国内网络通常可用；如果超时或连接被重置，在 `tts.proxy` 里填本机代理地址。

**模型从来不发语音**
确认 `edge_tts_speak` 出现在模型可用工具列表中，并且在「行为风格」里明确写了工具名。只在提示词里说“可以发语音”通常不够。

**语音没有声音或无法播放**
本插件输出 MP3（24kHz 单声道）。SnowLuma 会把它转成 QQ 语音；如果当前适配器版本不支持 MP3，可先升级 SnowLuma 或适配器插件。

## 许可证

MIT
