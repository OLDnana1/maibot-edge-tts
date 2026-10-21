"""Edge TTS 免费语音插件。

使用微软 Edge 在线语音合成服务（``edge-tts``）把文本合成为 MP3，
再通过 MaiBot 的 ``send.custom("voice", ...)`` 能力发送为 QQ 语音。

发送链路：

    插件 -> ctx.send.custom("voice", base64, stream_id)
         -> 核心 VoiceComponent
         -> SnowLuma 适配器 {"type": "record", "data": {"file": "base64://..."}}
         -> QQ 语音消息

插件同时提供两种触发方式：

1. ``edge_tts_speak`` 工具：由大模型在聊天中自行决定是否调用（正常玩法）。
2. ``/语音``、``/tts`` 调试命令：手动测试合成与发送是否正常。
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections import OrderedDict
from typing import Any, Literal, Optional

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType
from pydantic import model_validator

try:  # 插件加载时允许依赖缺失，真正合成时再给出明确错误
    import edge_tts
except Exception:  # pragma: no cover - 仅在依赖缺失时命中
    edge_tts = None  # type: ignore[assignment]


DEFAULT_VOICE_LABEL = "中文-晓晓-女声"

# 下拉可选音色：显示名 -> Edge TTS 官方音色 ID
# 显示名格式统一为「语言/方言-名字-性别」，方便直接看出男女声
VOICE_CHOICES: tuple[tuple[str, str], ...] = (
    ("中文-晓晓-女声", "zh-CN-XiaoxiaoNeural"),
    ("中文-晓伊-女声", "zh-CN-XiaoyiNeural"),
    ("中文-云健-男声", "zh-CN-YunjianNeural"),
    ("中文-云希-男声", "zh-CN-YunxiNeural"),
    ("中文-云夏-男声", "zh-CN-YunxiaNeural"),
    ("中文-云扬-男声", "zh-CN-YunyangNeural"),
    ("东北话-晓北-女声", "zh-CN-liaoning-XiaobeiNeural"),
    ("陕西话-晓妮-女声", "zh-CN-shaanxi-XiaoniNeural"),
    ("粤语-曉佳-女声", "zh-HK-HiuGaaiNeural"),
    ("粤语-曉曼-女声", "zh-HK-HiuMaanNeural"),
    ("粤语-雲龍-男声", "zh-HK-WanLungNeural"),
    ("台湾腔-曉臻-女声", "zh-TW-HsiaoChenNeural"),
    ("台湾腔-曉雨-女声", "zh-TW-HsiaoYuNeural"),
    ("台湾腔-雲哲-男声", "zh-TW-YunJheNeural"),
    ("英语-美式Ava-女声", "en-US-AvaNeural"),
    ("英语-美式Andrew-男声", "en-US-AndrewNeural"),
    ("英语-英式Sonia-女声", "en-GB-SoniaNeural"),
    ("日语-七海-女声", "ja-JP-NanamiNeural"),
    ("韩语-善熙-女声", "ko-KR-SunHiNeural"),
    ("法语-Denise-女声", "fr-FR-DeniseNeural"),
)

VOICE_ID_BY_LABEL: dict[str, str] = {label: voice_id for label, voice_id in VOICE_CHOICES}
VOICE_LABEL_BY_ID: dict[str, str] = {voice_id: label for label, voice_id in VOICE_CHOICES}

# 下拉可选值（Literal 会被 WebUI 渲染成下拉列表）
VoiceId = Literal[
    "中文-晓晓-女声",
    "中文-晓伊-女声",
    "中文-云健-男声",
    "中文-云希-男声",
    "中文-云夏-男声",
    "中文-云扬-男声",
    "东北话-晓北-女声",
    "陕西话-晓妮-女声",
    "粤语-曉佳-女声",
    "粤语-曉曼-女声",
    "粤语-雲龍-男声",
    "台湾腔-曉臻-女声",
    "台湾腔-曉雨-女声",
    "台湾腔-雲哲-男声",
    "英语-美式Ava-女声",
    "英语-美式Andrew-男声",
    "英语-英式Sonia-女声",
    "日语-七海-女声",
    "韩语-善熙-女声",
    "法语-Denise-女声",
]

# 数值参数的合法范围
_ADJUST_LIMITS: dict[str, tuple[int, int]] = {
    "rate": (-50, 100),
    "volume": (-100, 100),
    "pitch": (-50, 50),
}
_LEADING_NUMBER_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?")

# 清洗消息文本里的平台标记，避免把 @、CQ 码念出来
_AT_TAG_PATTERN = re.compile(r"@<[^>]*:\d+>")
_CQ_CODE_PATTERN = re.compile(r"\[CQ:[^\]]*\]")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_VOICE_COMMAND_PREFIX = re.compile(r"^(?:@\S+\s*)?/(?:语音|tts)\s*", re.IGNORECASE)
# 群聊里常见的「@机器人 /语音 ...」形式；@ 会被核心转成 "@昵称" 前缀
_COMMAND_AT_PREFIX = r"(?:@\S+\s*)?"


class PluginSectionConfig(PluginConfigBase):
    """插件基础开关。"""

    __ui_label__ = "插件开关"
    __ui_icon__ = "toggle-left"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="关闭后插件不再合成语音，已注册的语音工具也会拒绝执行",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="1.0.0",
        description="配置版本，升级插件时由宿主维护，一般不需要修改",
        json_schema_extra={"label": "配置版本"},
    )
    command_enabled: bool = Field(
        default=True,
        description="关闭后 /语音、/tts、/音色、/语音设置 等调试命令不响应",
        json_schema_extra={"label": "启用调试命令"},
    )
    cooldown_seconds: float = Field(
        default=3.0,
        description="同一用户在同一会话的最短触发间隔，0 表示不限制",
        json_schema_extra={"label": "冷却时间（秒）"},
    )
    max_text_length: int = Field(
        default=200,
        description="单次合成的最大字符数，超过会被拒绝并提示分段发送",
        json_schema_extra={"label": "单次字数上限"},
    )


class TtsSectionConfig(PluginConfigBase):
    """语音合成参数。"""

    __ui_label__ = "语音合成"
    __ui_icon__ = "mic"
    __ui_order__ = 1

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_values(cls, data: Any) -> Any:
        """兼容旧配置：官方音色 ID 自动转成下拉显示名，``+15%`` 自动转成数字。"""

        if not isinstance(data, dict):
            return data
        data = dict(data)

        voice = str(data.get("voice") or "").strip()
        if voice:
            if voice in VOICE_LABEL_BY_ID:
                # 旧配置里存的是官方音色 ID，转成下拉显示名
                data["voice"] = VOICE_LABEL_BY_ID[voice]
            elif voice not in VOICE_ID_BY_LABEL:
                # 不在下拉里的音色 ID，转交给自定义音色字段
                custom_voice = str(data.get("custom_voice") or "").strip()
                data["custom_voice"] = custom_voice or voice
                data["voice"] = DEFAULT_VOICE_LABEL

        for key, (low, high) in _ADJUST_LIMITS.items():
            raw_value = data.get(key, 0)
            matched = _LEADING_NUMBER_PATTERN.match(str(raw_value).strip())
            if matched:
                number = int(round(float(matched.group(0))))
            else:
                number = 0
            data[key] = max(low, min(high, number))

        return data

    voice: VoiceId = Field(
        default="中文-晓晓-女声",
        description="下拉选择音色，显示名已标注语言、名字和性别；不在此列表的音色用下面的自定义字段",
        json_schema_extra={"label": "音色"},
    )
    custom_voice: str = Field(
        default="",
        description="填写后覆盖上面的下拉选择，可填任意 Edge TTS 官方音色 ID（如 en-GB-SoniaNeural）",
        json_schema_extra={"label": "自定义音色 ID", "placeholder": "留空则使用上面的下拉音色"},
    )
    rate: int = Field(
        default=0,
        ge=-50,
        le=100,
        description="只填数字，单位是百分比。范围 -50 ~ 100：-50 约半速，100 约两倍速，负数变慢",
        json_schema_extra={"label": "语速（%）", "placeholder": "0", "hint": "范围 -50 ~ 100，负数变慢，正数变快"},
    )
    volume: int = Field(
        default=0,
        ge=-100,
        le=100,
        description="只填数字，单位是百分比。范围 -100 ~ 100：-100 基本静音，负数变小，正数变大",
        json_schema_extra={"label": "音量（%）", "placeholder": "0", "hint": "范围 -100 ~ 100，负数变小，正数变大"},
    )
    pitch: int = Field(
        default=0,
        ge=-50,
        le=50,
        description="只填数字，单位是赫兹（Hz）。范围 -50 ~ 50，负数变低，正数变高",
        json_schema_extra={"label": "音调（Hz）", "placeholder": "0", "hint": "范围 -50 ~ 50，负数变低，正数变高"},
    )
    proxy: str = Field(
        default="",
        description="访问微软语音服务需要的代理地址，例如 http://127.0.0.1:7890；留空表示直连",
        json_schema_extra={"label": "代理地址", "placeholder": "http://127.0.0.1:7890（留空直连）"},
    )
    connect_timeout: int = Field(
        default=10,
        description="连接微软语音服务的超时时间",
        json_schema_extra={"label": "连接超时（秒）"},
    )
    receive_timeout: int = Field(
        default=60,
        description="单次语音合成的总超时时间",
        json_schema_extra={"label": "合成超时（秒）"},
    )
    retry_times: int = Field(
        default=2,
        description="合成失败时的重试次数，微软接口短时间内多次请求会偶发失败",
        json_schema_extra={"label": "失败重试次数"},
    )
    cache_size: int = Field(
        default=16,
        description="相同文本与参数的语音缓存条数，0 表示关闭缓存",
        json_schema_extra={"label": "语音缓存条数"},
    )


class DebugSectionConfig(PluginConfigBase):
    """调试与日志。"""

    __ui_label__ = "调试"
    __ui_icon__ = "bug"
    __ui_order__ = 2

    log_synthesis: bool = Field(
        default=True,
        description="是否在 MaiBot 主日志中记录每次合成与发送结果",
        json_schema_extra={"label": "记录合成日志"},
    )


class EdgeTtsPluginConfig(PluginConfigBase):
    """Edge TTS 插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    tts: TtsSectionConfig = Field(default_factory=TtsSectionConfig)
    debug: DebugSectionConfig = Field(default_factory=DebugSectionConfig)


class EdgeTtsPlugin(MaiBotPlugin):
    """基于 edge-tts 的免费 QQ 语音插件。"""

    config_model = EdgeTtsPluginConfig

    def __init__(self) -> None:
        super().__init__()
        # 合成结果缓存：key 为 (text, voice, rate, volume, pitch)
        self._voice_cache: OrderedDict[tuple[str, str, str, str, str], bytes] = OrderedDict()
        # 冷却记录：key 为 (stream_id, user_id)
        self._last_trigger: dict[tuple[str, str], float] = {}
        # 限制同时进行的合成数量，避免一次涌入大量请求
        self._synthesis_semaphore: Optional[asyncio.Semaphore] = None

    # ===== 生命周期 =====

    async def on_load(self) -> None:
        """插件加载时检查依赖。"""

        if edge_tts is None:
            self.ctx.logger.error(
                "[EdgeTTS] 未找到 edge-tts 依赖，语音功能不可用。"
                "请执行：python -m pip install edge-tts"
            )
            return
        self.ctx.logger.info(
            "[EdgeTTS] 插件已加载，默认音色=%s，命令=%s",
            self.config.tts.voice,
            "开启" if self.config.plugin.command_enabled else "关闭",
        )

    async def on_unload(self) -> None:
        """插件卸载时释放缓存。"""

        self._voice_cache.clear()
        self._last_trigger.clear()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后清空缓存，保证新音色立即生效。"""

        del scope, config_data, version
        self._voice_cache.clear()

    # ===== 大模型工具 =====

    @Tool(
        "edge_tts_speak",
        description=(
            "把一段话用语音发到当前聊天（免费 Edge TTS）。"
            "只有当你想用声音说话时才调用；text 里只放要朗读的内容，"
            "调用后不要再发送内容相同的文字，避免重复。"
        ),
        parameters=[
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="要朗读的文本，支持中英文，最多约 200 字",
                required=True,
            ),
            ToolParameterInfo(
                name="voice",
                param_type=ToolParamType.STRING,
                description=(
                    "可选音色，填中文名或官方 ID，留空使用插件配置。"
                    "常用中文名：中文-晓晓-女声、中文-晓伊-女声、中文-云希-男声、"
                    "中文-云健-男声、中文-云扬-男声、粤语-曉曼-女声"
                ),
                required=False,
            ),
            ToolParameterInfo(
                name="rate",
                param_type=ToolParamType.STRING,
                description="可选语速，只填数字，单位百分比，范围 -50 ~ 100（如 15、-20），留空使用插件配置",
                required=False,
            ),
            ToolParameterInfo(
                name="pitch",
                param_type=ToolParamType.STRING,
                description="可选音调，只填数字，单位 Hz，范围 -50 ~ 50（如 5、-10），留空使用插件配置",
                required=False,
            ),
        ],
    )
    async def handle_speak_tool(
        self,
        text: str = "",
        voice: str = "",
        rate: str = "",
        pitch: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """大模型主动发语音的入口。"""

        stream_id = str(kwargs.get("stream_id") or kwargs.get("chat_id") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()

        if not self.config.plugin.enabled:
            return {"name": "edge_tts_speak", "success": False, "content": "语音插件当前已禁用。"}
        if not stream_id:
            return {"name": "edge_tts_speak", "success": False, "content": "缺少聊天流信息，无法发送语音。"}

        ok, message = await self._speak(
            text=text,
            stream_id=stream_id,
            user_id=user_id or "tool",
            voice=voice,
            rate=rate,
            pitch=pitch,
            enforce_cooldown=False,
        )
        return {"name": "edge_tts_speak", "success": ok, "content": message}

    # ===== 调试命令 =====

    @Command(
        "edge_tts_voice_test",
        description="把 /语音 后面的文字合成为语音并发送（调试）",
        # 这里要求 /语音 与内容之间必须有空白，避免把 /语音设置 误吃掉
        pattern=rf"^{_COMMAND_AT_PREFIX}/语音(?:\s+(?P<content>[\s\S]+))?$",
    )
    async def handle_voice_command(self, stream_id: str = "", text: str = "", **kwargs: Any) -> tuple[bool, Optional[str], bool]:
        """处理 /语音 调试命令。"""

        return await self._handle_debug_command(stream_id, text, kwargs)

    @Command(
        "edge_tts_tts_test",
        description="把 /tts 后面的文字合成为语音并发送（调试）",
        pattern=rf"(?i)^{_COMMAND_AT_PREFIX}/tts(?:\s+(?P<content>[\s\S]+))?$",
    )
    async def handle_tts_command(self, stream_id: str = "", text: str = "", **kwargs: Any) -> tuple[bool, Optional[str], bool]:
        """处理 /tts 调试命令。"""

        return await self._handle_debug_command(stream_id, text, kwargs)

    @Command(
        "edge_tts_voice_list",
        description="列出常用 Edge TTS 音色",
        pattern=rf"^{_COMMAND_AT_PREFIX}/音色",
    )
    async def handle_voice_list(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, Optional[str], bool]:
        """列出常用音色。"""

        del kwargs
        if not self.config.plugin.command_enabled:
            return True, "语音命令已关闭。", True

        current_label = self.config.tts.voice
        current_id = VOICE_ID_BY_LABEL.get(current_label, current_label)
        if self.config.tts.custom_voice:
            current_id = self.config.tts.custom_voice
            current_label = f"自定义（{current_id}）"

        lines = ["🎤 可用音色（WebUI 下拉里的 20 个）："]
        for label, voice_id in VOICE_CHOICES:
            mark = " ← 当前" if voice_id == current_id else ""
            lines.append(f"· {label}　{voice_id}{mark}")
        lines.append(f"\n当前音色：{current_label}")
        lines.append("不在列表里的音色，请填到「自定义音色 ID」；完整 400+ 音色可用 edge-tts --list-voices 查看。")
        await self.ctx.send.text("\n".join(lines), stream_id)
        return True, "已发送音色列表", True

    @Command(
        "edge_tts_voice_config",
        description="查看当前语音配置",
        pattern=rf"^{_COMMAND_AT_PREFIX}/语音设置",
    )
    async def handle_voice_config(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, Optional[str], bool]:
        """查看当前语音参数。"""

        del kwargs
        if not self.config.plugin.command_enabled:
            return True, "语音命令已关闭。", True

        cfg = self.config.tts
        dependency = "已安装" if edge_tts is not None else "未安装（python -m pip install edge-tts）"
        proxy = cfg.proxy or "直连"
        if cfg.custom_voice:
            voice_text = f"{cfg.custom_voice}（自定义）"
        else:
            voice_text = f"{cfg.voice}（{VOICE_ID_BY_LABEL.get(cfg.voice, cfg.voice)}）"
        summary = (
            "🔧 Edge TTS 当前配置\n"
            f"音色：{voice_text}\n"
            f"语速：{cfg.rate:+d}%　音量：{cfg.volume:+d}%　音调：{cfg.pitch:+d}Hz\n"
            f"代理：{proxy}\n"
            f"单次上限：{self.config.plugin.max_text_length} 字\n"
            f"依赖：{dependency}"
        )
        await self.ctx.send.text(summary, stream_id)
        return True, "已发送语音配置", True

    # ===== 内部实现 =====

    async def _handle_debug_command(
        self,
        stream_id: str,
        text: str,
        kwargs: dict[str, Any],
    ) -> tuple[bool, Optional[str], bool]:
        """处理 /语音 与 /tts 两个调试命令。"""

        if not self.config.plugin.enabled:
            return False, None, True
        if not self.config.plugin.command_enabled:
            return True, "语音命令已在插件配置中关闭。", True
        if not stream_id:
            return False, "缺少聊天流信息，无法发送语音。", True

        content = self._extract_command_content(text, kwargs)
        user_id = str(kwargs.get("user_id") or "").strip() or "command"
        if not content:
            await self.ctx.send.text(
                "用法：/语音 要朗读的内容\n例如：/语音 你好，我是语音测试。",
                stream_id,
            )
            return True, "已提示用法", True

        ok, message = await self._speak(
            text=content,
            stream_id=stream_id,
            user_id=user_id,
            voice="",
            rate="",
            pitch="",
            enforce_cooldown=True,
        )
        if not ok:
            await self.ctx.send.text(f"语音发送失败：{message}", stream_id)
        return True, message, True

    def _extract_command_content(self, text: str, kwargs: dict[str, Any]) -> str:
        """从命名捕获组或原始文本中取出要朗读的内容。"""

        matched_groups = kwargs.get("matched_groups")
        if isinstance(matched_groups, dict):
            content = str(matched_groups.get("content") or "").strip()
            if content:
                return content

        raw_text = str(kwargs.get("text") or text or "").strip()
        return _VOICE_COMMAND_PREFIX.sub("", raw_text, count=1).strip()

    async def _speak(
        self,
        *,
        text: str,
        stream_id: str,
        user_id: str,
        voice: str = "",
        rate: str = "",
        pitch: str = "",
        enforce_cooldown: bool,
    ) -> tuple[bool, str]:
        """合成并发送一条语音，返回 ``(是否成功, 提示文本)``。"""

        if edge_tts is None:
            return False, "未安装 edge-tts，请先执行 python -m pip install edge-tts"

        cleaned_text = self._clean_text(text)
        if not cleaned_text:
            return False, "没有可朗读的文本。"

        max_length = max(1, int(self.config.plugin.max_text_length))
        if len(cleaned_text) > max_length:
            return False, f"文本过长（{len(cleaned_text)} 字），上限为 {max_length} 字，请分段发送。"

        if enforce_cooldown and not self._check_cooldown(stream_id, user_id):
            return False, f"触发太频繁，请 {self.config.plugin.cooldown_seconds:g} 秒后再试。"

        cfg = self.config.tts
        # 优先级：工具临时指定 > 自定义音色 > 下拉音色
        voice_setting = str(voice or cfg.custom_voice or cfg.voice or DEFAULT_VOICE_LABEL).strip()
        final_voice = VOICE_ID_BY_LABEL.get(voice_setting, voice_setting)
        final_rate = self._format_percent(rate if str(rate).strip() else cfg.rate, low=-50, high=100)
        final_volume = self._format_percent(cfg.volume, low=-100, high=100)
        final_pitch = self._format_hz(pitch if str(pitch).strip() else cfg.pitch, low=-50, high=50)

        try:
            audio = await self._synthesize(
                text=cleaned_text,
                voice=final_voice,
                rate=final_rate,
                volume=final_volume,
                pitch=final_pitch,
            )
        except asyncio.TimeoutError:
            return False, f"合成超时（{cfg.receive_timeout} 秒），请检查网络或代理设置。"
        except Exception as exc:  # noqa: BLE001 - 需要把底层异常转成可读提示
            self.ctx.logger.error("[EdgeTTS] 合成失败: %s", exc, exc_info=True)
            return False, f"合成失败：{exc}"

        if not audio:
            return False, "合成结果为空，可能是音色 ID 不正确。"

        encoded = base64.b64encode(audio).decode("ascii")
        try:
            sent = await self.ctx.send.custom(
                "voice",
                encoded,
                stream_id,
                storage_message=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("[EdgeTTS] 发送失败: %s", exc, exc_info=True)
            return False, f"发送失败：{exc}"

        if not sent:
            return False, "发送失败：适配器未确认发送成功。"

        if self.config.debug.log_synthesis:
            self.ctx.logger.info(
                "[EdgeTTS] 已发送语音 stream=%s voice=%s chars=%d bytes=%d",
                stream_id,
                final_voice,
                len(cleaned_text),
                len(audio),
            )
        return True, f"已发送语音（{len(cleaned_text)} 字，音色 {final_voice}）"

    @staticmethod
    def _to_adjust_number(value: Any, *, low: int, high: int) -> int:
        """把数字或旧格式字符串（``+15%``、``-10Hz``）转成限定范围内的整数。"""

        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            number = int(round(value))
        else:
            matched = _LEADING_NUMBER_PATTERN.match(str(value or "").strip())
            number = int(round(float(matched.group(0)))) if matched else 0
        return max(low, min(high, number))

    @classmethod
    def _format_percent(cls, value: Any, *, low: int, high: int) -> str:
        """格式化为 edge-tts 需要的 ``+15%`` / ``-20%``。"""

        return f"{cls._to_adjust_number(value, low=low, high=high):+d}%"

    @classmethod
    def _format_hz(cls, value: Any, *, low: int, high: int) -> str:
        """格式化为 edge-tts 需要的 ``+5Hz`` / ``-10Hz``。"""

        return f"{cls._to_adjust_number(value, low=low, high=high):+d}Hz"

    async def _synthesize(
        self,
        *,
        text: str,
        voice: str,
        rate: str,
        volume: str,
        pitch: str,
    ) -> bytes:
        """调用 edge-tts 合成 MP3，命中缓存时直接返回。"""

        cache_key = (text, voice, rate, volume, pitch)
        cached = self._voice_cache.get(cache_key)
        if cached is not None:
            self._voice_cache.move_to_end(cache_key)
            return cached

        cfg = self.config.tts
        if self._synthesis_semaphore is None:
            self._synthesis_semaphore = asyncio.Semaphore(2)

        async def _collect() -> bytes:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
                proxy=cfg.proxy.strip() or None,
                connect_timeout=max(1, int(cfg.connect_timeout)),
                receive_timeout=max(1, int(cfg.receive_timeout)),
            )
            buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buffer.extend(chunk["data"])
            return bytes(buffer)

        async with self._synthesis_semaphore:
            audio = await self._synthesize_with_retry(_collect, retry_times=max(0, int(cfg.retry_times)))

        if audio and int(cfg.cache_size) > 0:
            self._voice_cache[cache_key] = audio
            while len(self._voice_cache) > int(cfg.cache_size):
                self._voice_cache.popitem(last=False)
        return audio

    async def _synthesize_with_retry(self, collect: Any, *, retry_times: int) -> bytes:
        """执行合成，并对服务端偶发的 ``NoAudioReceived`` 做退避重试。"""

        timeout = max(1, int(self.config.tts.receive_timeout)) + 5
        last_error: Optional[Exception] = None
        for attempt in range(retry_times + 1):
            try:
                audio = await asyncio.wait_for(collect(), timeout=timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一重试后抛出
                last_error = exc
            else:
                if audio:
                    return audio
                last_error = RuntimeError("合成结果为空")

            if attempt < retry_times:
                if self.config.debug.log_synthesis:
                    self.ctx.logger.warning(
                        "[EdgeTTS] 第 %d 次合成失败，%.1f 秒后重试: %s",
                        attempt + 1,
                        attempt + 1.5,
                        last_error,
                    )
                await asyncio.sleep(attempt + 1.5)

        if last_error is not None:
            raise last_error
        return b""

    def _check_cooldown(self, stream_id: str, user_id: str) -> bool:
        """检查并更新用户冷却时间。"""

        cooldown = max(0.0, float(self.config.plugin.cooldown_seconds))
        if cooldown <= 0:
            return True

        now = time.monotonic()
        key = (stream_id, user_id)
        last_time = self._last_trigger.get(key, 0.0)
        if now - last_time < cooldown:
            return False

        self._last_trigger[key] = now
        # 简单清理，避免长期运行后字典无限增长
        if len(self._last_trigger) > 512:
            expire_before = now - max(cooldown * 10, 600)
            self._last_trigger = {
                item_key: item_time
                for item_key, item_time in self._last_trigger.items()
                if item_time >= expire_before
            }
        return True

    @staticmethod
    def _clean_text(text: str) -> str:
        """去掉 @ 标记、CQ 码与多余空白。"""

        cleaned = _AT_TAG_PATTERN.sub("", str(text or ""))
        cleaned = _CQ_CODE_PATTERN.sub("", cleaned)
        cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned)
        return cleaned.strip()


def create_plugin() -> EdgeTtsPlugin:
    """创建插件实例。"""

    return EdgeTtsPlugin()
