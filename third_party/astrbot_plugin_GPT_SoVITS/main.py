import asyncio
import base64
import copy
import random
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain, Record
from astrbot.core.platform import AstrMessageEvent

PROJECT = Path(r'__PROJECT_DIR__')
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
try:
    from app import voice_gate
    from importlib import reload
    voice_gate = reload(voice_gate)
    from app import plugin_audio, voice_captions
    plugin_audio = reload(plugin_audio)
    voice_captions = reload(voice_captions)
except Exception:  # the project path moved; speech stays text-only below
    voice_gate = None
    logger.warning("voice_gate unavailable; GPT-SoVITS speech gate degraded")

from .core.client import GSVApiClient, GSVRequestResult
from .core.config import PluginConfig
from .core.emotion import EmotionJudger
from .core.entry import EntryManager
from .core.local_data import LocalDataManager
from .core.service import GPTSoVITSService


class GPTSoVITSPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context)
        self.local_data = LocalDataManager(self.cfg)
        self.entry_mgr = EntryManager(self.cfg)
        self.client = GSVApiClient(self.cfg)
        self.judger = EmotionJudger(self.cfg)
        self.service = GPTSoVITSService(self.cfg, self.client, self.local_data)

    async def initialize(self):
        if self.cfg.enabled:
            await self.service.load_model()

    async def terminate(self):
        await self.client.close()

    @staticmethod
    def _to_record(res: GSVRequestResult, event=None) -> Record:
        if event is not None:
            try:
                from app.voice_captions import remember_plugin
                remember_plugin(event, res)
            except Exception as exc:
                logger.warning("鲸聊语音原文未保存（%s），保留音频发送，不编造转写", type(exc).__name__)
        if res.file_path:
            try:
                return Record.fromFileSystem(res.file_path)
            except Exception:
                logger.warning(f"无法读取文件：{res.file_path}, 已忽略")
                pass

        if not res.data:
            raise ValueError("无法获取结果数据")

        b64 = base64.urlsafe_b64encode(res.data).decode()
        return Record.fromBase64(b64)


    @filter.on_llm_request(priority=-9012)
    async def _gate_speech(self, event: AstrMessageEvent, req):
        """Whale compatibility: only a permitted Whale turn offers speech."""
        if voice_gate is None or req.func_tool is None:
            return
        try:
            if voice_gate.allowed(event.get_message_str()):
                return
            req.func_tool = copy.copy(req.func_tool)
            req.func_tool.remove_tool('gsv_tts')
        except Exception:
            return

    async def _wait_ready(self, seconds: float = 45.0) -> bool:
        """Wait briefly for the on-demand local service before failing.

        Whale/idle compatibility: the GPT-SoVITS service is started only on
        demand and exits when idle, so a cold first sentence may need to wait
        for the loopback port. No remote host is ever contacted here.
        """
        from urllib.parse import urlsplit
        parts = urlsplit(self.cfg.client.base_url or "")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 9880
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1)
                writer.close()
                return True
            except Exception:
                if loop.time() >= deadline:
                    return False
                await asyncio.sleep(1)

    async def _get_emotion_params(
        self, event: AstrMessageEvent, text: str
    ) -> dict | None:
        entry = None

        if self.cfg.judge.enabled_llm:
            labels = self.entry_mgr.get_names()
            emotion = await self.judger.judge_emotion(event, text=text, labels=labels)
            if emotion:
                entry = self.entry_mgr.get_entry(emotion)

        if entry is None:
            entry = self.entry_mgr.match_entry(text)

        return entry.to_params() if entry else None

    @filter.on_decorating_result(priority=14)
    async def on_decorating_result(self, event: AstrMessageEvent):
        """消息入口"""
        if not self.cfg.enabled:
            return
        # Whale compatibility: avoid a failed 60-second auto-TTS request when
        # the installed plugin has no running model service. Other platforms
        # and configured/online services retain the user's original behavior.
        if event.get_platform_name() == "webchat":
            from app.voice_capabilities import text_only_workflow
            if text_only_workflow(event.get_message_str()):
                return
        if event.get_platform_name() == "webchat" and "[鲸聊语音许可:" in event.get_message_str():
            import asyncio
            from app.voice_capabilities import state
            ready = await asyncio.to_thread(state)
            if not ready.get("gsv_available"):
                return
        cfg = self.cfg.auto

        result = event.get_result()
        if not result:
            return
        chain = result.chain
        if not chain:
            return
        if cfg.only_llm_result and not result.is_llm_result():
            return
        if random.random() > cfg.tts_prob:
            return

        # 收集所有Plain文本片段
        plain_texts = []
        for seg in chain:
            if isinstance(seg, Plain):
                plain_texts.append(seg.text)

        # 仅允许只含有Plain的消息链通过
        if len(plain_texts) != len(chain):
            return

        # 合并所有Plain文本
        combined_text = "\n".join(plain_texts)

        # 仅允许一定长度以下的文本通过
        if len(combined_text) > cfg.max_msg_len:
            return

        params = await self._get_emotion_params(event, combined_text)
        res = await self.service.inference(combined_text, extra_params=params)
        if not bool(res):
            return
        chain.clear()
        chain.append(self._to_record(res, event))

    @filter.command("说", alias={"gsv", "GSV"})
    async def on_command(self, event: AstrMessageEvent):
        """说 <内容>, 直接调用GSV合成语音"""
        if not self.cfg.enabled:
            return

        text = event.message_str.partition(" ")[2]
        if not await self._wait_ready():
            yield event.plain_result("本地语音服务正在启动，稍后再试。")
            return
        res = await self.service.inference(text)

        if not bool(res):
            yield event.plain_result(res.error)
            return

        yield event.chain_result([self._to_record(res, event)])

    @filter.command("重启GSV", alias={"重启gsv"})
    async def tts_control(self, event: AstrMessageEvent):
        """重启GPT_SoVITS"""
        if not self.cfg.enabled:
            return
        yield event.plain_result("重启TTS中...(报错信息请忽略，等待一会即可完成重启)")
        await self.service.restart()

    @filter.llm_tool()
    async def gsv_tts(self, event: AstrMessageEvent, message: str = ""):
        """
        用语音输出要讲的话
        Args:
            message(string): 要讲的话
        """
        try:
            if voice_gate is not None and not voice_gate.allowed(event.get_message_str()):
                return voice_gate.status_text(event.get_message_str()) or "这一轮没有鲸聊语音许可，用文字回复即可。"
            if not await self._wait_ready():
                return "本地语音服务正在启动，这次先用文字回复。"
            params = await self._get_emotion_params(event, message)
            res = await self.service.inference(message, extra_params=params)
            if not bool(res):
                return res.error
            seg = self._to_record(res, event)
            await event.send(event.chain_result([seg]))
        except Exception as e:
            return str(e)
